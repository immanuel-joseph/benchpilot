from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from benchpilot.adapters.sim import create_bench, create_debugger
from benchpilot.core.interfaces import BenchAdapter, Capture
from benchpilot.core.safety import Limit, SafetyInterlock
from benchpilot.core.session import Session
from benchpilot.core.signals import SignalMap

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = REPO_ROOT / "examples" / "cruise_control"


class ScriptedBench(BenchAdapter):
    """Deterministic fake bench: each signal is a function of bench time.

    Proves a third-party adapter only needs the BenchAdapter interface, and lets
    executor tests describe exact waveforms.
    """

    sample_period = 0.1

    def __init__(self, waveforms: dict[str, Callable[[float], float]] | None = None):
        self.waveforms = dict(waveforms or {})
        self.written: dict[str, list[float]] = {}
        self.now = 0.0

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def set(self, signal: str, value: float) -> None:
        self.written.setdefault(signal, []).append(value)
        self.waveforms[signal] = lambda t, v=value: v

    def get(self, signal: str) -> float:
        return self.waveforms[signal](self.now)

    def start_capture(self, signals: list[str]) -> None: ...

    def stop_capture(self) -> Capture:
        return Capture()

    def time(self) -> float:
        return self.now

    def wait(self, seconds: float) -> None:
        self.now = round(self.now + seconds, 9)


def make_session(bench: BenchAdapter, signals: list[str], limits=None, debug=None) -> Session:
    return Session(
        bench=bench,
        debug=debug,
        signals=SignalMap({s: s for s in signals}),
        interlock=SafetyInterlock(limits or {}),
    )


@pytest.fixture
def sim_session() -> Session:
    bench = create_bench({})
    session = Session(
        bench=bench,
        debug=create_debugger({}, bench),
        signals=SignalMap(
            {
                "vehicle.speed_kph": "plant/speed_kph",
                "road.grade_pct": "plant/grade_pct",
                "engine.throttle_pct": "plant/throttle_pct",
                "driver.accel_pedal_pct": "driver/accel_pedal_pct",
                "cruise.enable": "driver/cruise_main_switch",
                "cruise.set_button": "driver/cruise_set_button",
            }
        ),
        interlock=SafetyInterlock({"road.grade_pct": Limit(min=-10, max=10)}),
    )
    session.connect()
    yield session
    session.disconnect()
