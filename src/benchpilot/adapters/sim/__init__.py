"""Default adapters: a virtual bench and virtual debugger around the built-in models.

Time is simulated. `wait(2.0)` steps the model 2 s forward and returns at once,
so a full test suite runs in well under a second of wall time.

Signal paths:

    plant/speed_kph            rw  vehicle speed (writing it sets the initial condition)
    plant/grade_pct            rw  road grade, positive = uphill
    plant/throttle_pct         r   throttle actually applied, 0-100
    driver/accel_pedal_pct     rw  0-100
    driver/brake_pedal_pct     rw  0-100
    driver/cruise_main_switch  rw  0/1
    driver/cruise_set_button   rw  0/1, acts on the rising edge
    driver/cruise_cancel_button rw 0/1, acts on the rising edge
    driver/cruise_accel_button rw  0/1, +5 kph per rising edge

Options: `model` (cruise_control), `dt` (step, seconds), and `faults`, a list of
firmware bugs to inject (see CruiseControlEcu.FAULTS).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchpilot.core.errors import AdapterError, ConfigError
from benchpilot.core.interfaces import BenchAdapter, Capture, DebugAdapter
from benchpilot.sim.plant import VehiclePlant
from benchpilot.sim.virtual_ecu import CruiseControlEcu

MODELS = ("cruise_control",)


class VirtualBench(BenchAdapter):
    def __init__(self, dt: float = 0.01, faults: list[str] | None = None):
        if dt <= 0:
            raise ConfigError("sim adapter option 'dt' must be positive")
        self.dt = dt
        self.sample_period = dt
        self.plant = VehiclePlant()
        try:
            self.ecu = CruiseControlEcu(faults=faults)
        except ValueError as exc:
            raise ConfigError(f"sim adapter: {exc}") from None
        self.connected = False
        self._steps = 0
        self._capture: Capture | None = None
        self._paths = self._build_paths()

    # -- BenchAdapter ------------------------------------------------------

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def reset(self) -> None:
        self.plant.reset()
        self.ecu.reset()
        self._steps = 0

    def set(self, signal: str, value: float) -> None:
        getter, setter = self._lookup(signal)
        if setter is None:
            raise AdapterError(f"sim signal {signal!r} is read-only")
        setter(float(value))
        if self._capture is not None:
            # Record the new value at the current timestamp so step changes show
            # up in plots exactly when they happened.
            self._record()

    def get(self, signal: str) -> float:
        getter, _ = self._lookup(signal)
        return getter()

    def start_capture(self, signals: list[str]) -> None:
        for s in signals:
            self._lookup(s)
        self._capture = Capture(values={s: [] for s in signals})
        self._record()

    def stop_capture(self) -> Capture:
        capture, self._capture = self._capture, None
        if capture is None:
            raise AdapterError("stop_capture() called without start_capture()")
        return capture

    def time(self) -> float:
        return self._steps * self.dt

    def wait(self, seconds: float) -> None:
        self._require_connected()
        for _ in range(round(seconds / self.dt)):
            self._step()

    # -- internals ---------------------------------------------------------

    def _step(self) -> None:
        self.ecu.vehicle_speed_kph = self.plant.speed_kph
        self.ecu.step(self.dt)
        self.plant.throttle = self.ecu.throttle_cmd
        self.plant.brake = self.ecu.brake_pedal
        self.plant.step(self.dt)
        self._steps += 1
        if self._capture is not None:
            self._record()

    def _record(self) -> None:
        assert self._capture is not None
        self._capture.time.append(self.time())
        for signal, series in self._capture.values.items():
            series.append(self.get(signal))

    def _lookup(self, signal: str) -> tuple[Callable[[], float], Callable[[float], None] | None]:
        self._require_connected()
        try:
            return self._paths[signal]
        except KeyError:
            raise AdapterError(
                f"sim bench has no signal path {signal!r}; known: {', '.join(self._paths)}"
            ) from None

    def _require_connected(self) -> None:
        if not self.connected:
            raise AdapterError("sim bench is not connected")

    def _build_paths(self) -> dict[str, tuple[Callable[[], float], Callable[[float], None] | None]]:
        plant, ecu = self.plant, self.ecu

        def ecu_input(attr: str, scale: float = 1.0):
            return (
                lambda: getattr(ecu, attr) * scale,
                lambda v: setattr(ecu, attr, v / scale),
            )

        return {
            "plant/speed_kph": (
                lambda: plant.speed_kph,
                lambda v: setattr(plant, "speed_kph", v),
            ),
            "plant/grade_pct": (
                lambda: plant.grade_pct,
                lambda v: setattr(plant, "grade_pct", v),
            ),
            "plant/throttle_pct": (lambda: plant.throttle * 100.0, None),
            "driver/accel_pedal_pct": ecu_input("accel_pedal", 100.0),
            "driver/brake_pedal_pct": ecu_input("brake_pedal", 100.0),
            "driver/cruise_main_switch": ecu_input("main_switch"),
            "driver/cruise_set_button": ecu_input("set_button"),
            "driver/cruise_cancel_button": ecu_input("cancel_button"),
            "driver/cruise_accel_button": ecu_input("accel_button"),
        }


class VirtualDebugger(DebugAdapter):
    """Reads and writes the virtual ECU's variables, like a debugger reading RAM."""

    def __init__(self, ecu: CruiseControlEcu):
        self.ecu = ecu

    def read_var(self, name: str) -> float:
        try:
            return self.ecu.read(name)
        except KeyError as exc:
            raise AdapterError(exc.args[0]) from None

    def write_var(self, name: str, value: float) -> None:
        try:
            self.ecu.write(name, value)
        except KeyError as exc:
            raise AdapterError(exc.args[0]) from None

    def reset_target(self) -> None:
        self.ecu.reset()


def create_bench(options: dict[str, Any]) -> VirtualBench:
    options = dict(options)
    model = options.pop("model", "cruise_control")
    if model not in MODELS:
        raise ConfigError(f"sim adapter: unknown model {model!r}; available: {', '.join(MODELS)}")
    dt = options.pop("dt", 0.01)
    faults = options.pop("faults", [])
    if not isinstance(faults, list):
        raise ConfigError("sim adapter option 'faults' must be a list of fault names")
    if options:
        raise ConfigError(f"sim adapter: unknown options {', '.join(sorted(options))}")
    return VirtualBench(dt=float(dt), faults=faults)


def create_debugger(options: dict[str, Any], bench: BenchAdapter) -> VirtualDebugger:
    if options:
        raise ConfigError(f"sim debugger: unknown options {', '.join(sorted(options))}")
    if not isinstance(bench, VirtualBench):
        raise ConfigError("the sim debug adapter only works with the sim bench adapter")
    return VirtualDebugger(bench.ecu)
