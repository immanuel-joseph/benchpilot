"""Safety interlocks: limits from bench.yaml that block unsafe values before they reach hardware."""

from __future__ import annotations

import math
from dataclasses import dataclass

from benchpilot.core.errors import SafetyViolation


@dataclass(frozen=True)
class Limit:
    min: float | None = None
    max: float | None = None

    def allows(self, value: float) -> bool:
        # NaN compares False against everything, so without this it would slip
        # past both bounds below; infinities are never a sane setpoint either.
        if not math.isfinite(value):
            return False
        if self.min is not None and value < self.min:
            return False
        if self.max is not None and value > self.max:
            return False
        return True

    def describe(self) -> str:
        lo = "-inf" if self.min is None else f"{self.min:g}"
        hi = "+inf" if self.max is None else f"{self.max:g}"
        return f"[{lo}, {hi}]"


class SafetyInterlock:
    """Checks every write against the bench's limits, keyed by logical signal name.

    The check runs in the session layer, above the adapter, so no adapter can
    forget to apply it.
    """

    def __init__(self, limits: dict[str, Limit], label: str = ""):
        self.limits = dict(limits)
        self.label = label  # prefixed to names in messages, e.g. "ecu_var "

    def check(self, signal: str, value: float) -> None:
        name = f"{self.label}{signal}"
        # Non-finite values are blocked for every signal, limited or not: they can
        # still arrive at runtime (e.g. a ramp starting from a NaN reading).
        if not math.isfinite(value):
            raise SafetyViolation(f"blocked write {name} = {value}: not a finite number")
        limit = self.limits.get(signal)
        if limit is not None and not limit.allows(value):
            raise SafetyViolation(
                f"blocked write {name} = {value:g}: outside safety limit {limit.describe()}"
            )
