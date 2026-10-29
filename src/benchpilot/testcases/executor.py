"""Runs test case steps against a connected Session: set, wait, ramp, expect."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Container, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from benchpilot.core.errors import BenchPilotError
from benchpilot.core.interfaces import Capture
from benchpilot.core.session import Session
from benchpilot.testcases.schema import (
    ExpectResponseStep,
    ExpectStep,
    RampStep,
    SetStep,
    TestCase,
    WaitStep,
)

_EPS = 1e-9


class Outcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"  # an expect didn't hold: the system under test misbehaved
    ERROR = "error"  # the test couldn't run properly: bad signal, interlock, adapter fault


@dataclass
class StepRecord:
    """When one step ran and how it ended; used to annotate reports."""

    location: str  # e.g. "steps[3] expect"
    description: str  # e.g. "expect vehicle.speed_kph 80 ± 1.5 within 10s"
    start: float  # bench time, same clock as the capture
    end: float
    outcome: Outcome
    signal: str | None = None  # plant signal the step acts on, if any
    band: tuple[float, float] | None = None  # accepted range, for expect steps
    ecu_var: str | None = None  # ECU variable the step acts on, if any
    samples: list[tuple[float, float]] = field(default_factory=list)  # expect readings
    metrics: dict[str, float] = field(default_factory=dict)  # measured by expect_response


@dataclass
class TestResult:
    __test__ = False  # not a pytest test class

    testcase: TestCase
    outcome: Outcome
    message: str = ""
    location: str = ""  # e.g. "steps[3] expect"
    bench_time_s: float = 0.0
    wall_time_s: float = 0.0
    capture: Capture | None = None
    source: Path | None = None
    steps: list[StepRecord] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASSED


class ExpectationFailed(Exception):
    pass


class _StepFailure(Exception):
    def __init__(self, outcome: Outcome, message: str, location: str):
        super().__init__(message)
        self.outcome = outcome
        self.message = message
        self.location = location


class TestInterrupted(KeyboardInterrupt):
    """Ctrl+C arrived mid-test. Teardown has already run; `result` is the partial result."""

    __test__ = False  # not a pytest test class

    def __init__(self, result: TestResult):
        super().__init__()
        self.result = result


def preflight(tc: TestCase, signals: Container[str], has_debug: bool) -> list[str]:
    """Problems that would stop `tc` running on a bench with these signal names.

    Needs only the bench's configuration, never a live adapter, so it's safe to
    call without touching hardware.
    """
    problems = []
    for name in tc.signals_used():
        if name not in signals:
            problems.append(f"unknown signal {name!r} (not in the bench's signal map)")
    uses_ecu = any(getattr(s, "ecu_var", None) for _, steps in tc.phases() for s in steps)
    if uses_ecu and not has_debug:
        problems.append("test uses ecu_var, but bench.yaml has no 'debug' adapter")
    return problems


def _record(result: TestResult, failure: _StepFailure) -> None:
    result.outcome, result.message, result.location = (
        failure.outcome,
        failure.message,
        failure.location,
    )


def describe_step(step: SetStep | WaitStep | RampStep | ExpectStep | ExpectResponseStep) -> str:
    if isinstance(step, SetStep):
        return f"set {step.target} = {step.value:g}"
    if isinstance(step, WaitStep):
        return f"wait {step.duration:g}s"
    if isinstance(step, RampStep):
        start = "" if step.from_ is None else f" from {step.from_:g}"
        return f"ramp {step.signal}{start} to {step.to:g} over {step.over:g}s"
    if isinstance(step, ExpectResponseStep):
        return f"expect_response {step.target} {step.describe()}"
    return f"expect {step.target} {step.describe()}"


def _band(step) -> tuple[float, float] | None:
    if isinstance(step, ExpectResponseStep):
        return (step.to - step.tolerance, step.to + step.tolerance)
    if not isinstance(step, ExpectStep):
        return None
    if step.equals is not None:
        return (step.equals - step.tolerance, step.equals + step.tolerance)
    lo = -math.inf if step.min is None else step.min
    hi = math.inf if step.max is None else step.max
    return (lo, hi)


class Executor:
    def __init__(self, session: Session):
        self.session = session
        self._records: list[StepRecord] = []
        self._samples: list[tuple[float, float]] = []
        self._metrics: dict[str, float] = {}

    def preflight(self, tc: TestCase) -> list[str]:
        """Problems that would stop `tc` running on this bench, found before touching it."""
        return preflight(tc, self.session.signals, has_debug=self.session.debug is not None)

    def run(self, tc: TestCase, source: Path | None = None) -> TestResult:
        result = TestResult(testcase=tc, outcome=Outcome.PASSED, source=source)
        self._records = result.steps
        problems = self.preflight(tc)
        if problems:
            result.outcome, result.message = Outcome.ERROR, "; ".join(problems)
            return result

        wall0, bench0 = time.perf_counter(), self.session.time()
        try:
            self.session.start_capture(tc.signals_used())
        except Exception as exc:
            result.outcome, result.message = Outcome.ERROR, f"could not start capture: {exc}"
            return result
        interrupted = False
        try:
            try:
                self._run_phase("setup", tc.setup)
                self._run_phase("steps", tc.steps)
            except _StepFailure as f:
                _record(result, f)
            except KeyboardInterrupt:
                # Ctrl+C: still make the bench safe before giving control back.
                interrupted = True
                result.outcome, result.message = Outcome.ERROR, "interrupted by user"
            # Teardown always runs, so the bench is left safe even after a failure.
            first_failure = self._run_teardown(tc.teardown)
            if first_failure is not None and result.passed:
                _record(result, first_failure)
        finally:
            result.capture = self.session.stop_capture()
            result.bench_time_s = self.session.time() - bench0
            result.wall_time_s = time.perf_counter() - wall0
        if interrupted:
            raise TestInterrupted(result)
        return result

    # -- steps -------------------------------------------------------------

    def _run_phase(self, phase: str, steps: list) -> None:
        for i, step in enumerate(steps):
            self._run_located(phase, i, step)

    def _run_teardown(self, steps: list) -> _StepFailure | None:
        """Attempt every teardown step, even after one fails; return the first failure.

        Stopping at the first failure would leave later steps (say, switching off
        a load) undone on a real bench.
        """
        first = None
        for i, step in enumerate(steps):
            try:
                self._run_located("teardown", i, step)
            except _StepFailure as f:
                first = first or f
        return first

    def _run_located(self, phase: str, index: int, step) -> None:
        location = f"{phase}[{index}] {step.kind}"
        start = self.session.time()
        outcome = Outcome.ERROR  # stays ERROR if interrupted mid-step
        self._samples: list[tuple[float, float]] = []
        self._metrics: dict[str, float] = {}
        try:
            self._run_step(step)
            outcome = Outcome.PASSED
        except ExpectationFailed as exc:
            outcome = Outcome.FAILED
            raise _StepFailure(Outcome.FAILED, str(exc), location) from None
        except BenchPilotError as exc:
            raise _StepFailure(Outcome.ERROR, str(exc), location) from None
        except Exception as exc:  # adapter bugs shouldn't take down the whole run
            message = f"{type(exc).__name__}: {exc}"
            raise _StepFailure(Outcome.ERROR, message, location) from None
        finally:
            self._records.append(
                StepRecord(
                    location=location,
                    description=describe_step(step),
                    start=start,
                    end=self.session.time(),
                    outcome=outcome,
                    signal=getattr(step, "signal", None),
                    band=_band(step),
                    ecu_var=getattr(step, "ecu_var", None),
                    samples=self._samples,
                    metrics=self._metrics,
                )
            )

    def _run_step(
        self, step: SetStep | WaitStep | RampStep | ExpectStep | ExpectResponseStep
    ) -> None:
        if isinstance(step, SetStep):
            if step.signal is not None:
                self.session.set(step.signal, step.value)
            else:
                self.session.write_var(step.ecu_var, step.value)
        elif isinstance(step, WaitStep):
            self.session.wait(step.duration)
        elif isinstance(step, RampStep):
            self._ramp(step)
        elif isinstance(step, ExpectStep):
            self._expect(step)
        elif isinstance(step, ExpectResponseStep):
            self._expect_response(step)
        else:  # pragma: no cover - the schema rules this out
            raise TypeError(f"unsupported step {step!r}")

    def _ramp(self, step: RampStep) -> None:
        start = step.from_ if step.from_ is not None else self.session.get(step.signal)
        # Check both ends up front so an unsafe ramp is refused before it starts,
        # not halfway through.
        self.session.interlock.check(step.signal, start)
        self.session.interlock.check(step.signal, step.to)
        n = max(1, round(step.over / self.session.sample_period))
        for i in range(1, n + 1):
            self.session.set(step.signal, start + (step.to - start) * i / n)
            self.session.wait(step.over / n)

    def _expect(self, step: ExpectStep) -> None:
        read: Callable[[], float]
        if step.signal is not None:
            read = lambda: self.session.get(step.signal)  # noqa: E731
        else:
            read = lambda: self.session.read_var(step.ecu_var)  # noqa: E731

        start = self.session.time()
        deadline = start + step.within
        in_band_since: float | None = None
        held = 0.0
        while True:
            now = self.session.time()
            value = read()
            self._samples.append((now, value))
            if step.accepts(value):
                if in_band_since is None:
                    in_band_since = now
                held = now - in_band_since
                if held >= step.hold_for - _EPS:
                    return
            else:
                if in_band_since is not None and now > deadline + _EPS:
                    raise ExpectationFailed(
                        f"{step.target} = {value:.6g} at +{now - start:.2f}s, expected "
                        f"{step.describe()} (held only {held:.2f}s)"
                    )
                in_band_since = None
                if now >= deadline - _EPS:
                    raise ExpectationFailed(
                        f"{step.target} = {value:.6g}, expected {step.describe()}"
                    )
            self.session.wait(self.session.sample_period)

    def _expect_response(self, step: ExpectResponseStep) -> None:
        read = _reader(self.session, step)
        start = self.session.time()
        origin = step.from_ if step.from_ is not None else read()
        if not math.isfinite(origin) or origin == step.to:
            raise ExpectationFailed(
                f"{step.target} starts at {origin:.6g}: no step to {step.to:g} to measure"
            )
        observer = _ResponseObserver(start, origin, step)
        deadline = start + step.window
        while True:
            now = self.session.time()
            value = read()
            self._samples.append((now, value))
            observer.add(now, value)
            if observer.done() or now >= deadline - _EPS:
                break
            self.session.wait(self.session.sample_period)

        metrics, ok, bad = observer.judge()
        self._metrics.update(metrics)
        if bad:
            details = "; ".join(bad + ok)
            raise ExpectationFailed(f"{step.target} {origin:.6g} -> {step.to:g}: {details}")


class _ResponseObserver:
    """Accumulates step-response measurements from successive readings."""

    def __init__(self, start: float, origin: float, step: ExpectResponseStep):
        self.start, self.origin, self.step = start, origin, step
        self.size = step.to - origin
        self.direction = 1.0 if self.size > 0 else -1.0
        self.t10: float | None = None
        self.t90: float | None = None
        self.max_excess = 0.0  # furthest past `to`, in signal units
        self.in_band_since: float | None = None
        self.settled_at: float | None = None

    def add(self, now: float, value: float) -> None:
        step = self.step
        if not math.isfinite(value):  # a broken reading counts as out of band
            self.in_band_since = None
            return
        progress = (value - self.origin) / self.size
        if self.t10 is None and progress >= 0.1:
            self.t10 = now
        if self.t90 is None and progress >= 0.9:
            self.t90 = now
        self.max_excess = max(self.max_excess, (value - step.to) * self.direction)
        if abs(value - step.to) <= step.tolerance + _EPS:
            if self.in_band_since is None:
                self.in_band_since = now
            if step.settles_within is not None and now - self.in_band_since >= step.hold_for - _EPS:
                self.settled_at = self.in_band_since
        else:
            self.in_band_since = None

    def done(self) -> bool:
        return self.settled_at is not None

    def judge(self) -> tuple[dict[str, float], list[str], list[str]]:
        """Return (metrics, passed descriptions, failed descriptions)."""
        step, metrics, ok, bad = self.step, {}, [], []
        if step.settles_within is not None:
            if self.settled_at is None:
                bad.append(
                    f"never settled within ±{step.tolerance:g} of {step.to:g} "
                    f"(max {step.settles_within:g}s)"
                )
            else:
                settle = self.settled_at - self.start
                metrics["settling_time_s"] = settle
                text = f"settled after {settle:.2f}s (max {step.settles_within:g}s)"
                (ok if settle <= step.settles_within + _EPS else bad).append(text)
        if step.overshoot_max is not None:
            amount, percent = step.overshoot_max
            pct = 100.0 * self.max_excess / abs(self.size)
            metrics["overshoot"] = self.max_excess
            metrics["overshoot_pct"] = pct
            measured = pct if percent else self.max_excess
            unit = "%" if percent else ""
            text = f"overshoot {self.max_excess:.4g} ({pct:.1f}%) (max {amount:g}{unit})"
            (ok if measured <= amount + _EPS else bad).append(text)
        if step.rise_time_max is not None:
            if self.t10 is None or self.t90 is None:
                bad.append(f"never reached 90% of the step (rise max {step.rise_time_max:g}s)")
            else:
                rise = self.t90 - self.t10
                metrics["rise_time_s"] = rise
                text = f"rise {rise:.2f}s (max {step.rise_time_max:g}s)"
                (ok if rise <= step.rise_time_max + _EPS else bad).append(text)
        return metrics, ok, bad


def _reader(session: Session, step) -> Callable[[], float]:
    if step.signal is not None:
        return lambda: session.get(step.signal)
    return lambda: session.read_var(step.ecu_var)


def run_suite(
    session: Session,
    testcases: Iterable[tuple[Path | None, TestCase]],
    reset_between_tests: bool = True,
    on_result: Callable[[TestResult], None] | None = None,
) -> list[TestResult]:
    """Run each test on an already-connected session, resetting the bench in between.

    If a reset fails, that test is recorded as ERROR and the suite stops: running
    on a bench in an unknown state would make every later result meaningless.
    On Ctrl+C the interrupted test's result is recorded, then TestInterrupted
    propagates.
    """
    executor = Executor(session)
    results: list[TestResult] = []

    def emit(result: TestResult) -> None:
        results.append(result)
        if on_result is not None:
            on_result(result)

    for source, tc in testcases:
        if reset_between_tests:
            try:
                session.reset()
            except Exception as exc:
                message = f"bench reset failed: {exc}; suite stopped"
                emit(TestResult(tc, Outcome.ERROR, message=message, source=source))
                break
        try:
            result = executor.run(tc, source=source)
        except TestInterrupted as exc:
            emit(exc.result)
            raise
        emit(result)
    return results
