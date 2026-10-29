"""YAML test case schema and validation.

A test case is one YAML file:

    id: CC-001
    title: Cruise holds set speed on a 3% grade
    requirement: REQ-CC-04
    tags: [cruise, regression]
    setup:     [steps...]
    steps:     [steps...]
    teardown:  [steps...]   # always runs, even after a failure

Each step is a one-key mapping:

    - set:    {signal: road.grade_pct, value: 3}      # or {ecu_var: ..., value: ...}
    - wait:   2s
    - ramp:   {signal: road.grade_pct, to: 5, over: 4s}   # optional 'from'
    - expect: {signal: vehicle.speed_kph, equals: 80, tolerance: 1.5, within: 10s, hold_for: 5s}
    - expect: {ecu_var: cc_state, equals: 2}
    - expect: {signal: vehicle.speed_kph, min: 78, hold_for: 8s}   # "never drops below"
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from benchpilot.core.errors import TestCaseError

STEP_KINDS = ("set", "wait", "ramp", "expect", "expect_response")

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?|\.\d+)\s*(ms|s|min)?\s*$")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "min": 60.0, None: 1.0}


def parse_duration(value: Any) -> float:
    """'2s', '500ms', '1.5min' or a bare number of seconds -> seconds."""
    if isinstance(value, bool):
        raise ValueError("expected a duration like '2s' or '500ms'")
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str) and (m := _DURATION.match(value)):
        seconds = float(m.group(1)) * _UNIT_SECONDS[m.group(2)]
    else:
        raise ValueError(f"invalid duration {value!r}; expected e.g. '2s', '500ms', '1.5min'")
    if seconds < 0:
        raise ValueError("duration can't be negative")
    return seconds


Duration = Annotated[float, BeforeValidator(parse_duration)]


class _Step(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, allow_inf_nan=False)


class _Targeted(_Step):
    """A step aimed at exactly one of a plant signal or an ECU variable."""

    signal: str | None = None
    ecu_var: str | None = None

    @model_validator(mode="after")
    def _one_target(self):
        if (self.signal is None) == (self.ecu_var is None):
            raise ValueError("needs exactly one of 'signal' or 'ecu_var'")
        return self

    @property
    def target(self) -> str:
        return self.signal if self.signal is not None else f"ecu_var {self.ecu_var}"


class SetStep(_Targeted):
    kind: Literal["set"] = "set"
    value: float


class WaitStep(_Step):
    kind: Literal["wait"] = "wait"
    duration: Duration


class RampStep(_Step):
    kind: Literal["ramp"] = "ramp"
    signal: str
    to: float
    over: Duration
    from_: float | None = Field(default=None, alias="from")


class ExpectStep(_Targeted):
    kind: Literal["expect"] = "expect"
    equals: float | None = None
    tolerance: float = 0.0
    min: float | None = None
    max: float | None = None
    within: Duration = 0.0
    hold_for: Duration = 0.0

    @field_validator("tolerance")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("tolerance can't be negative")
        return v

    @model_validator(mode="after")
    def _one_condition(self):
        has_range = self.min is not None or self.max is not None
        if (self.equals is None) == (not has_range):
            raise ValueError("needs either 'equals' (with optional 'tolerance') or 'min'/'max'")
        if self.equals is None and self.tolerance:
            raise ValueError("'tolerance' only applies to 'equals'")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min is greater than max")
        return self

    def accepts(self, value: float) -> bool:
        # A NaN reading compares False against both bounds and would pass a
        # min/max check; a broken sensor must never produce a PASS.
        if not math.isfinite(value):
            return False
        eps = 1e-9
        if self.equals is not None:
            return abs(value - self.equals) <= self.tolerance + eps
        if self.min is not None and value < self.min - eps:
            return False
        if self.max is not None and value > self.max + eps:
            return False
        return True

    def describe(self) -> str:
        if self.equals is not None:
            cond = f"{self.equals:g}" + (f" ± {self.tolerance:g}" if self.tolerance else "")
        elif self.max is None:
            cond = f">= {self.min:g}"
        elif self.min is None:
            cond = f"<= {self.max:g}"
        else:
            cond = f"in [{self.min:g}, {self.max:g}]"
        parts = [cond]
        if self.within:
            parts.append(f"within {self.within:g}s")
        if self.hold_for:
            parts.append(f"held for {self.hold_for:g}s")
        return " ".join(parts)


def parse_overshoot(value: Any) -> tuple[float, bool]:
    """'2%' -> (2.0, True): percent of the step size. 1.5 -> (1.5, False): signal units."""
    if isinstance(value, bool):
        raise ValueError("expected an amount like 1.5 or a percentage like '2%'")
    if isinstance(value, (int, float)):
        amount, percent = float(value), False
    elif isinstance(value, str) and (m := re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", value)):
        amount, percent = float(m.group(1)), True
    else:
        raise ValueError(f"invalid overshoot {value!r}; expected e.g. 1.5 or '2%'")
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("overshoot must be a finite, non-negative amount")
    return amount, percent


Overshoot = Annotated[tuple[float, bool], BeforeValidator(parse_overshoot)]


class ExpectResponseStep(_Targeted):
    """Judge a step response: settling time, overshoot and rise time.

    The signal is observed from the start of this step, so put it right after
    the step that causes the change (the button press, the setpoint write).
    """

    kind: Literal["expect_response"] = "expect_response"
    to: float
    from_: float | None = Field(default=None, alias="from")  # default: read at start
    tolerance: float = 0.0  # settled = within `to` ± tolerance
    settles_within: Duration | None = None
    hold_for: Duration = 1.0  # how long it must stay in the band to count as settled
    overshoot_max: Overshoot | None = None
    rise_time_max: Duration | None = None  # 10% -> 90% of the step
    observe: Duration | None = None  # window when settles_within isn't given

    @model_validator(mode="after")
    def _criteria(self):
        if (
            self.settles_within is None
            and self.overshoot_max is None
            and self.rise_time_max is None
        ):
            raise ValueError("needs at least one of settles_within, overshoot_max, rise_time_max")
        if self.settles_within is not None and self.tolerance <= 0:
            raise ValueError("settles_within needs a positive 'tolerance' (the settling band)")
        if self.tolerance < 0:
            raise ValueError("tolerance can't be negative")
        if self.settles_within is None and self.observe is None:
            raise ValueError("without settles_within, give 'observe': how long to watch")
        if self.from_ is not None and self.from_ == self.to:
            raise ValueError("'from' equals 'to': there is no step to measure")
        return self

    @property
    def window(self) -> float:
        """Longest this step can take."""
        if self.settles_within is not None:
            return self.settles_within + self.hold_for
        return self.observe or 0.0

    def describe(self) -> str:
        start = "" if self.from_ is None else f"{self.from_:g} "
        parts = [f"{start}-> {self.to:g}"]
        if self.settles_within is not None:
            parts.append(f"settles to ±{self.tolerance:g} within {self.settles_within:g}s")
        if self.overshoot_max is not None:
            amount, percent = self.overshoot_max
            parts.append(f"overshoot <= {amount:g}{'%' if percent else ''}")
        if self.rise_time_max is not None:
            parts.append(f"rise <= {self.rise_time_max:g}s")
        return ", ".join(parts)


Step = Annotated[
    SetStep | WaitStep | RampStep | ExpectStep | ExpectResponseStep, Field(discriminator="kind")
]


def _normalize_steps(raw: Any) -> Any:
    """Turn YAML's `{set: {...}}` / `{wait: 2s}` form into `{kind: set, ...}`."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        return raw  # let pydantic report the type error
    out = []
    for item in raw:
        if not (isinstance(item, dict) and len(item) == 1):
            raise ValueError(f"each step must be a one-key mapping like 'wait: 2s', got {item!r}")
        ((kind, body),) = item.items()
        if kind not in STEP_KINDS:
            raise ValueError(f"unknown step {kind!r}; expected one of {', '.join(STEP_KINDS)}")
        if kind == "wait":
            out.append({"kind": "wait", "duration": body})
        elif isinstance(body, dict):
            out.append({"kind": kind, **body})
        else:
            raise ValueError(f"'{kind}' step needs a mapping, got {body!r}")
    return out


Steps = Annotated[list[Step], BeforeValidator(_normalize_steps)]


class TestCase(BaseModel):
    __test__ = False  # not a pytest test class

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    requirement: str | None = None
    tags: list[str] = Field(default_factory=list)
    setup: Steps = Field(default_factory=list)
    steps: Steps
    teardown: Steps = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def _not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("a test needs at least one step")
        return v

    def phases(self) -> list[tuple[str, list]]:
        return [("setup", self.setup), ("steps", self.steps), ("teardown", self.teardown)]

    def signals_used(self) -> list[str]:
        seen: dict[str, None] = {}
        for _, steps in self.phases():
            for step in steps:
                name = getattr(step, "signal", None)
                if name is not None:
                    seen[name] = None
        return list(seen)


def load_testcase(path: str | Path) -> TestCase:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TestCaseError(f"{path}: file not found") from None
    except yaml.YAMLError as exc:
        raise TestCaseError(f"{path}: invalid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise TestCaseError(f"{path}: expected a mapping at the top level")
    try:
        return TestCase.model_validate(raw)
    except ValidationError as exc:
        raise TestCaseError(f"{path}:\n{_format_errors(exc)}") from None


def collect_testcase_files(paths: list[str | Path]) -> list[Path]:
    """Expand folders into their *.yaml/*.yml files, sorted.

    Bench configs are skipped: `bench.yaml` and anything named `*.bench.yaml`.
    """
    found: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            found.extend(
                f
                for f in sorted(p.rglob("*"))
                if f.suffix in (".yaml", ".yml") and not _is_bench_file(f)
            )
        else:
            found.append(p)
    return found


def _is_bench_file(path: Path) -> bool:
    return path.stem == "bench" or path.stem.endswith(".bench")


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        msg = err["msg"].removeprefix("Value error, ")
        lines.append(f"  {_format_loc(err['loc'])}: {msg}")
    return "\n".join(lines)


def _format_loc(loc: tuple) -> str:
    """('steps', 2, 'expect', 'within') -> 'steps[2].within'."""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part in STEP_KINDS:  # pydantic's discriminator tag, not a real field
            continue
        else:
            out += f".{part}" if out else str(part)
    return out or "<root>"
