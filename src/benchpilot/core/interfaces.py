"""The two interfaces every adapter implements. Tests only ever talk to these."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Capture:
    """Time-series recorded between start_capture() and stop_capture().

    `values[name][i]` is the sample of `name` at `time[i]` (seconds of bench time).
    """

    time: list[float] = field(default_factory=list)
    values: dict[str, list[float]] = field(default_factory=dict)

    @property
    def signals(self) -> list[str]:
        return list(self.values)

    def renamed(self, rename: Callable[[str], str]) -> Capture:
        """Return a copy with each signal key passed through `rename`."""
        return Capture(
            time=list(self.time),
            values={rename(k): list(v) for k, v in self.values.items()},
        )


class BenchAdapter(ABC):
    """Drives the plant side: the HIL simulator or a virtual bench.

    Signal arguments are adapter-level paths (already resolved from logical names
    through the bench's signal map).
    """

    #: How often the executor polls signals while waiting on an `expect`, in seconds.
    sample_period: float = 0.05

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def set(self, signal: str, value: float) -> None: ...

    @abstractmethod
    def get(self, signal: str) -> float: ...

    @abstractmethod
    def start_capture(self, signals: list[str]) -> None: ...

    @abstractmethod
    def stop_capture(self) -> Capture: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    # Time hooks. Real benches run in wall-clock time, so the defaults sleep;
    # simulators override these to step their model instead, which lets a 30 s
    # test finish in milliseconds.

    def time(self) -> float:
        """Current bench time in seconds. Only differences are meaningful."""
        # perf_counter, not monotonic: monotonic ticks only every ~15.6 ms on Windows.
        return time.perf_counter()

    def wait(self, seconds: float) -> None:
        """Let `seconds` of bench time pass."""
        if seconds > 0:
            time.sleep(seconds)

    def reset(self) -> None:  # noqa: B027 - optional hook, deliberately not abstract
        """Return the plant to its initial state between tests. Optional."""


class DebugAdapter(ABC):
    """Reads/writes ECU internals: a debugger or a virtual ECU."""

    # Optional hooks: a real debugger has a connection to open and close; a
    # virtual one shares its bench's lifetime and needs neither.
    def connect(self) -> None:  # noqa: B027 - optional hook, deliberately not abstract
        """Open the connection to the debugger."""

    def disconnect(self) -> None:  # noqa: B027 - optional hook, deliberately not abstract
        """Close the connection to the debugger."""

    @abstractmethod
    def read_var(self, name: str) -> float: ...

    @abstractmethod
    def write_var(self, name: str, value: float) -> None: ...

    @abstractmethod
    def reset_target(self) -> None: ...
