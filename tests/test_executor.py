import pytest

from benchpilot.core.safety import Limit
from benchpilot.testcases.executor import Executor, Outcome, run_suite
from benchpilot.testcases.schema import TestCase

from conftest import ScriptedBench, make_session


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


def _run(waveforms, steps, **kwargs):
    bench = ScriptedBench(waveforms)
    session = make_session(bench, list(waveforms) + ["out"], limits=kwargs.pop("limits", None))
    return Executor(session).run(_tc(steps, **kwargs)), bench


def ramp_up(t):  # 0 at t=0, reaches 10 at t=5, then flat
    return min(10.0, 2.0 * t)


@pytest.mark.parametrize(
    "expect, outcome",
    [
        ({"equals": 0}, Outcome.PASSED),  # instantaneous check at t=0
        ({"equals": 10}, Outcome.FAILED),  # not yet
        ({"equals": 10, "within": "6s"}, Outcome.PASSED),
        ({"equals": 10, "within": "4s"}, Outcome.FAILED),  # too slow
        ({"equals": 10, "tolerance": 2, "within": "4s"}, Outcome.PASSED),  # 8 at t=4
        ({"min": 0, "max": 10, "hold_for": "8s"}, Outcome.PASSED),
        ({"max": 5, "hold_for": "4s"}, Outcome.FAILED),  # exceeds 5 at t=2.5
        ({"max": 5, "hold_for": "2s"}, Outcome.PASSED),
    ],
)
def test_expect_timing(expect, outcome):
    result, _ = _run({"x": ramp_up}, [{"expect": {"signal": "x", **expect}}])
    assert result.outcome is outcome, result.message


def test_hold_restarts_when_band_is_left_before_deadline():
    # In band 0-1s, out 1-2s, back in from 2s on: entering by the 3s deadline and
    # then holding 2s must pass even though the first stint was too short.
    def blip(t):
        return 100.0 if 1.0 <= t < 2.0 else 0.0

    result, _ = _run(
        {"x": blip}, [{"expect": {"signal": "x", "equals": 0, "within": "3s", "hold_for": "2s"}}]
    )
    assert result.passed, result.message


def test_failure_stops_steps_but_runs_teardown():
    result, bench = _run(
        {"x": lambda t: 0.0},
        [
            {"expect": {"signal": "x", "equals": 1}},
            {"set": {"signal": "out", "value": 99}},
        ],
        teardown=[{"set": {"signal": "out", "value": 0}}],
    )
    assert result.outcome is Outcome.FAILED
    assert result.location == "steps[0] expect"
    assert "x = 0, expected 1" in result.message
    assert bench.written["out"] == [0]  # steps[1] never ran; teardown did


def test_safety_violation_is_an_error_not_a_failure():
    result, bench = _run(
        {},
        [{"set": {"signal": "out", "value": 50}}],
        limits={"out": Limit(max=10)},
    )
    assert result.outcome is Outcome.ERROR
    assert "safety limit" in result.message
    assert "out" not in bench.written


def test_unsafe_ramp_is_refused_before_it_starts():
    result, bench = _run(
        {},
        [{"ramp": {"signal": "out", "from": 0, "to": 50, "over": "1s"}}],
        limits={"out": Limit(max=10)},
    )
    assert result.outcome is Outcome.ERROR
    assert "out" not in bench.written


def test_ramp_is_linear():
    result, bench = _run({}, [{"ramp": {"signal": "out", "from": 0, "to": 10, "over": "1s"}}])
    assert result.passed
    assert bench.written["out"] == pytest.approx([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert result.bench_time_s == pytest.approx(1.0)


def test_preflight_catches_unknown_signal_and_missing_debugger():
    result, _ = _run(
        {"x": ramp_up},
        [{"expect": {"signal": "y", "equals": 1}}, {"expect": {"ecu_var": "v", "equals": 1}}],
    )
    assert result.outcome is Outcome.ERROR
    assert "unknown signal 'y'" in result.message
    assert "no 'debug' adapter" in result.message


def test_adapter_crash_is_reported_and_suite_continues():
    def broken(t):
        raise RuntimeError("bus off")

    bench = ScriptedBench({"x": broken, "ok": lambda t: 1.0})
    session = make_session(bench, ["x", "ok"])
    results = run_suite(
        session,
        [
            (None, _tc([{"expect": {"signal": "x", "equals": 1}}])),
            (None, _tc([{"expect": {"signal": "ok", "equals": 1}}])),
        ],
    )
    assert [r.outcome for r in results] == [Outcome.ERROR, Outcome.PASSED]
    assert "RuntimeError: bus off" in results[0].message
