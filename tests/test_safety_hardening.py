"""Regression tests for the safety-hardening review findings (one section per finding)."""

import math
import shutil

import pytest
import yaml
from pydantic import ValidationError

from benchpilot.adapters.sim import VirtualBench
from benchpilot.cli import EXIT_OK, EXIT_TESTS_FAILED, main
from benchpilot.core.config import BenchConfig, LimitConfig
from benchpilot.core.errors import SafetyViolation
from benchpilot.core.interfaces import DebugAdapter
from benchpilot.core.safety import Limit
from benchpilot.core.session import Session
from benchpilot.testcases.executor import Executor, Outcome, run_suite
from benchpilot.testcases.schema import ExpectStep, TestCase

from conftest import EXAMPLE_DIR, ScriptedBench, make_session

NAN, INF = float("nan"), float("inf")


def _tc(steps, setup=(), teardown=()):
    return TestCase.model_validate(
        {
            "id": "T",
            "title": "t",
            "setup": list(setup),
            "steps": list(steps),
            "teardown": list(teardown),
        }
    )


# -- 1. NaN/inf bypasses the safety interlock ----------------------------------


@pytest.mark.parametrize("value", [NAN, INF, -INF])
def test_limit_rejects_non_finite(value):
    assert not Limit(min=0, max=10).allows(value)
    assert not Limit(min=0).allows(value)
    assert not Limit(max=10).allows(value)


@pytest.mark.parametrize("value", [NAN, INF, -INF])
def test_non_finite_write_never_reaches_adapter(value):
    bench = ScriptedBench()
    session = make_session(bench, ["x", "free"], limits={"x": Limit(min=0, max=10)})
    with pytest.raises(SafetyViolation):
        session.set("x", value)
    # Even a signal with no configured limit must never receive a non-finite value.
    with pytest.raises(SafetyViolation):
        session.set("free", value)
    assert bench.written == {}


@pytest.mark.parametrize(
    "step",
    [
        {"set": {"signal": "x", "value": NAN}},
        {"set": {"signal": "x", "value": INF}},
        {"ramp": {"signal": "x", "to": INF, "over": "1s"}},
        {"ramp": {"signal": "x", "from": NAN, "to": 1, "over": "1s"}},
        {"expect": {"signal": "x", "equals": NAN}},
        {"expect": {"signal": "x", "equals": 1, "tolerance": INF}},
        {"expect": {"signal": "x", "min": -INF}},
        {"wait": INF},
        {"expect": {"signal": "x", "min": 1, "within": INF}},
    ],
)
def test_schema_rejects_non_finite_numbers(step):
    with pytest.raises(ValidationError):
        _tc([step])


def test_schema_rejects_yaml_nan_literal():
    raw = yaml.safe_load("id: T\ntitle: t\nsteps:\n  - set: {signal: x, value: .nan}\n")
    assert math.isnan(raw["steps"][0]["set"]["value"])
    with pytest.raises(ValidationError):
        TestCase.model_validate(raw)


@pytest.mark.parametrize("limit", [{"min": NAN}, {"max": INF}, {"min": -INF, "max": 1}])
def test_bench_limits_reject_non_finite(limit):
    with pytest.raises(ValidationError):
        LimitConfig.model_validate(limit)


# -- 2. A NaN reading passes range expectations ----------------------------------


@pytest.mark.parametrize("value", [NAN, INF, -INF])
def test_expect_rejects_non_finite_readings(value):
    assert not ExpectStep(signal="s", min=78).accepts(value)
    assert not ExpectStep(signal="s", max=78).accepts(value)
    assert not ExpectStep(signal="s", min=0, max=100).accepts(value)
    assert not ExpectStep(signal="s", equals=80, tolerance=1).accepts(value)


def test_nan_reading_fails_the_test():
    bench = ScriptedBench({"x": lambda t: NAN})
    session = make_session(bench, ["x"])
    result = Executor(session).run(_tc([{"expect": {"signal": "x", "min": 78}}]))
    assert result.outcome is Outcome.FAILED, result.message


# -- 3. Teardown stops at its first failing step ---------------------------------


def test_teardown_attempts_every_step_after_a_failure():
    bench = ScriptedBench()
    session = make_session(bench, ["a", "b", "out"], limits={"out": Limit(max=10)})
    result = Executor(session).run(
        _tc(
            [{"set": {"signal": "a", "value": 5}}],
            teardown=[
                {"set": {"signal": "out", "value": 50}},  # blocked by the interlock
                {"set": {"signal": "a", "value": 0}},
                {"set": {"signal": "b", "value": 0}},
            ],
        )
    )
    assert bench.written == {"a": [5, 0], "b": [0]}
    assert result.outcome is Outcome.ERROR
    assert result.location == "teardown[0] set"


def test_teardown_failure_does_not_mask_earlier_failure():
    bench = ScriptedBench({"x": lambda t: 0.0})
    session = make_session(bench, ["x", "out"], limits={"out": Limit(max=10)})
    result = Executor(session).run(
        _tc(
            [{"expect": {"signal": "x", "equals": 1}}],
            teardown=[
                {"set": {"signal": "out", "value": 50}},
                {"set": {"signal": "out", "value": 0}},
            ],
        )
    )
    assert result.outcome is Outcome.FAILED
    assert result.location == "steps[0] expect"
    assert bench.written == {"out": [0]}


# -- 4. Ctrl+C mid-test skips teardown ---------------------------------------------


class InterruptingBench(ScriptedBench):
    """Raises KeyboardInterrupt (as Ctrl+C would) once bench time passes `at`."""

    def __init__(self, at: float):
        super().__init__()
        self.at = at
        self.capture_stopped = False

    def wait(self, seconds: float) -> None:
        super().wait(seconds)
        if self.now >= self.at:
            self.at = math.inf  # only once
            raise KeyboardInterrupt

    def stop_capture(self):
        self.capture_stopped = True
        return super().stop_capture()


def test_interrupt_runs_teardown_then_reraises():
    bench = InterruptingBench(at=1.0)
    session = make_session(bench, ["out"])
    with pytest.raises(KeyboardInterrupt):
        Executor(session).run(
            _tc(
                [{"set": {"signal": "out", "value": 7}}, {"wait": "5s"}],
                teardown=[{"set": {"signal": "out", "value": 0}}],
            )
        )
    assert bench.written["out"] == [7, 0]
    assert bench.capture_stopped


def test_cli_interrupt_exits_130_after_teardown(monkeypatch, capsys):
    calls = {"n": 0}
    original = VirtualBench.wait

    def wait(self, seconds):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        original(self, seconds)

    monkeypatch.setattr(VirtualBench, "wait", wait)
    assert main(["run", str(EXAMPLE_DIR)]) == 130
    captured = capsys.readouterr()
    assert "interrupted; teardown ran" in captured.err
    assert "error(s)" in captured.out  # summary still printed


# -- 5. ECU variable writes have no safety limits ----------------------------------


def _sim_config(**extra):
    return BenchConfig.model_validate(
        {"bench": {"adapter": "sim"}, "debug": {"adapter": "sim"}, **extra}
    )


def test_ecu_limits_block_out_of_range_writes():
    session = Session.from_config(_sim_config(ecu_limits={"cc_state": {"min": 0, "max": 2}}))
    session.connect()
    session.write_var("cc_state", 2)
    with pytest.raises(SafetyViolation):
        session.write_var("cc_state", 9)
    with pytest.raises(SafetyViolation):
        session.write_var("cc_set_speed_kph", NAN)  # non-finite blocked even without a limit
    assert session.read_var("cc_state") == 2


def test_ecu_limits_reject_non_finite_config():
    with pytest.raises(ValidationError):
        _sim_config(ecu_limits={"cc_state": {"max": INF}})


def test_ecu_limit_violation_is_a_test_error():
    class RecordingDebugger(DebugAdapter):
        def __init__(self):
            self.writes = []

        def read_var(self, name):
            return 0.0

        def write_var(self, name, value):
            self.writes.append((name, value))

        def reset_target(self):
            pass

    debugger = RecordingDebugger()
    session = make_session(ScriptedBench(), [], debug=debugger)
    session.ecu_interlock.limits["v"] = Limit(max=1)
    result = Executor(session).run(_tc([{"set": {"ecu_var": "v", "value": 5}}]))
    assert result.outcome is Outcome.ERROR
    assert debugger.writes == []


# -- 6. A failing bench reset crashes the suite ------------------------------------


class BrokenResetBench(ScriptedBench):
    def __init__(self):
        super().__init__({"ok": lambda t: 1.0})
        self.resets = 0

    def reset(self):
        self.resets += 1
        if self.resets == 2:
            raise RuntimeError("power supply not responding")


def test_reset_failure_is_an_error_and_stops_the_suite():
    bench = BrokenResetBench()
    session = make_session(bench, ["ok"])
    ok = _tc([{"expect": {"signal": "ok", "equals": 1}}])
    results = run_suite(session, [(None, ok), (None, ok), (None, ok)])
    assert [r.outcome for r in results] == [Outcome.PASSED, Outcome.ERROR]
    assert "bench reset failed: power supply not responding" in results[1].message


# -- 7. validate --bench instantiates adapters --------------------------------------


def test_validate_does_not_create_adapters(tmp_path, monkeypatch, capsys):
    import benchpilot.core.registry as registry

    def forbidden(name):
        raise AssertionError(f"validate created adapter {name!r}")

    monkeypatch.setattr(registry, "load_bench_factory", forbidden)
    monkeypatch.setattr(registry, "load_debug_factory", forbidden)

    bench = tmp_path / "bench.yaml"
    raw = yaml.safe_load((EXAMPLE_DIR / "bench.yaml").read_text())
    raw["bench"]["adapter"] = "lab-rack-7"  # not installed: must not matter
    bench.write_text(yaml.safe_dump(raw))
    tests = tmp_path / "tests"
    shutil.copytree(EXAMPLE_DIR / "tests", tests)
    assert main(["validate", str(tests), "--bench", str(bench)]) == EXIT_OK

    (tests / "typo.yaml").write_text(
        "id: X\ntitle: t\nsteps: [{set: {signal: vehicle.sped_kph, value: 1}}]\n"
    )
    assert main(["validate", str(tests), "--bench", str(bench)]) == EXIT_TESTS_FAILED
    assert "unknown signal 'vehicle.sped_kph'" in capsys.readouterr().out
