"""Dry run: describe what a test would do on a bench, without touching it.

Works from the bench configuration alone (signal map, limits, whether a debug
adapter is configured). No adapter is created, so planning is safe anywhere,
including on a laptop without the vendor software.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from benchpilot.core.config import BenchConfig
from benchpilot.core.errors import SafetyViolation
from benchpilot.core.safety import Limit, SafetyInterlock
from benchpilot.testcases.executor import preflight
from benchpilot.testcases.schema import (
    ExpectResponseStep,
    ExpectStep,
    RampStep,
    SetStep,
    TestCase,
    WaitStep,
)


@dataclass
class TestPlan:
    __test__ = False  # not a pytest test class

    testcase: TestCase
    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    max_bench_time_s: float = 0.0  # upper bound: every expect uses its full window


def plan_test(tc: TestCase, config: BenchConfig) -> TestPlan:
    plan = TestPlan(tc)
    plan.problems.extend(preflight(tc, config.signals, has_debug=config.debug is not None))
    interlock = SafetyInterlock({k: v.to_limit() for k, v in config.limits.items()})
    ecu_interlock = SafetyInterlock(
        {k: v.to_limit() for k, v in config.ecu_limits.items()}, label="ecu_var "
    )

    def path(signal: str) -> str:
        return config.signals.get(signal, "<not in signal map>")

    def limit_note(lim: dict[str, Limit], name: str) -> str:
        limit = lim.get(name)
        return f"limit {limit.describe()}" if limit else "no limit"

    def check(where: str, what: str, checker: SafetyInterlock, name: str, *values: float) -> str:
        try:
            for value in values:
                checker.check(name, value)
        except SafetyViolation as exc:
            plan.problems.append(f"{where} {what}: BLOCKED ({exc})")
            return "BLOCKED"
        return "ok"

    for phase, steps in tc.phases():
        for i, step in enumerate(steps):
            where = f"{phase}[{i}]"
            if isinstance(step, SetStep) and step.signal is not None:
                what = f"set {step.signal} = {step.value:g}"
                verdict = check(where, what, interlock, step.signal, step.value)
                note = limit_note(interlock.limits, step.signal)
                line = f"{what} -> {path(step.signal)}  {note} {verdict}"
            elif isinstance(step, SetStep):
                what = f"set ecu_var {step.ecu_var} = {step.value:g}"
                verdict = check(where, what, ecu_interlock, step.ecu_var, step.value)
                note = limit_note(ecu_interlock.limits, step.ecu_var)
                line = f"{what} (debug adapter)  {note} {verdict}"
            elif isinstance(step, WaitStep):
                line = f"wait {step.duration:g}s"
                plan.max_bench_time_s += step.duration
            elif isinstance(step, RampStep):
                start = "current value" if step.from_ is None else f"{step.from_:g}"
                what = f"ramp {step.signal} {start} -> {step.to:g} over {step.over:g}s"
                ends = [step.to] if step.from_ is None else [step.from_, step.to]
                verdict = check(where, what, interlock, step.signal, *ends)
                note = limit_note(interlock.limits, step.signal)
                line = f"{what} -> {path(step.signal)}  {note} {verdict}"
                if step.from_ is None:
                    line += " (start read from the bench at run time, checked then)"
                plan.max_bench_time_s += step.over
            elif isinstance(step, ExpectStep):
                line = f"expect {step.target} {step.describe()}"
                plan.max_bench_time_s += step.within + step.hold_for
            elif isinstance(step, ExpectResponseStep):
                line = f"expect_response {step.target} {step.describe()}"
                plan.max_bench_time_s += step.window
            else:  # pragma: no cover - the schema rules this out
                line = repr(step)
            plan.lines.append(f"{where:<13} {line}")
    return plan
