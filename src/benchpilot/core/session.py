"""A connected bench: adapters + signal map + safety interlocks behind one object.

The executor only uses this class, so mapping and interlocks can't be bypassed.
"""

from __future__ import annotations

from benchpilot.core.config import BenchConfig
from benchpilot.core.errors import BenchPilotError
from benchpilot.core.interfaces import BenchAdapter, Capture, DebugAdapter
from benchpilot.core.registry import load_bench_factory, load_debug_factory
from benchpilot.core.safety import SafetyInterlock
from benchpilot.core.signals import SignalMap


class Session:
    def __init__(
        self,
        bench: BenchAdapter,
        signals: SignalMap,
        interlock: SafetyInterlock,
        debug: DebugAdapter | None = None,
        name: str = "bench",
        ecu_interlock: SafetyInterlock | None = None,
    ):
        self.bench = bench
        self.debug = debug
        self.signals = signals
        self.interlock = interlock
        self.ecu_interlock = ecu_interlock or SafetyInterlock({}, label="ecu_var ")
        self.name = name

    @classmethod
    def from_config(cls, config: BenchConfig) -> Session:
        bench = load_bench_factory(config.bench.adapter)(dict(config.bench.options))
        debug = None
        if config.debug is not None:
            debug = load_debug_factory(config.debug.adapter)(dict(config.debug.options), bench)
        return cls(
            bench=bench,
            debug=debug,
            signals=SignalMap(config.signals),
            interlock=SafetyInterlock({k: v.to_limit() for k, v in config.limits.items()}),
            ecu_interlock=SafetyInterlock(
                {k: v.to_limit() for k, v in config.ecu_limits.items()}, label="ecu_var "
            ),
            name=config.name,
        )

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        self.bench.connect()
        if self.debug is not None:
            try:
                self.debug.connect()
            except BaseException:
                self.bench.disconnect()  # don't leave the bench connected on the way out
                raise

    def disconnect(self) -> None:
        # Close both sides even if the first one fails.
        try:
            if self.debug is not None:
                self.debug.disconnect()
        finally:
            self.bench.disconnect()

    def __enter__(self) -> Session:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()

    def reset(self) -> None:
        self.bench.reset()
        if self.debug is not None:
            self.debug.reset_target()

    # -- plant side --------------------------------------------------------

    def set(self, signal: str, value: float) -> None:
        path = self.signals.resolve(signal)
        self.interlock.check(signal, value)
        self.bench.set(path, value)

    def get(self, signal: str) -> float:
        return self.bench.get(self.signals.resolve(signal))

    def start_capture(self, signals: list[str]) -> None:
        self.bench.start_capture([self.signals.resolve(s) for s in signals])

    def stop_capture(self) -> Capture:
        return self.bench.stop_capture().renamed(self.signals.name_of)

    # -- ECU side ----------------------------------------------------------

    def read_var(self, name: str) -> float:
        return self._require_debug().read_var(name)

    def write_var(self, name: str, value: float) -> None:
        debug = self._require_debug()
        self.ecu_interlock.check(name, value)
        debug.write_var(name, value)

    def _require_debug(self) -> DebugAdapter:
        if self.debug is None:
            raise BenchPilotError("this test uses ecu_var, but bench.yaml has no 'debug' adapter")
        return self.debug

    # -- time --------------------------------------------------------------

    @property
    def sample_period(self) -> float:
        return self.bench.sample_period

    def time(self) -> float:
        return self.bench.time()

    def wait(self, seconds: float) -> None:
        self.bench.wait(seconds)
