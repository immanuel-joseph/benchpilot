"""expect_response: settling time, overshoot and rise time on known waveforms.

The ScriptedBench samples every 0.1 s, so measured times are within one sample
of the analytic values.
"""

import math

import pytest
from pydantic import ValidationError

from benchpilot.cli import main
from benchpilot.core.config import BenchConfig
from benchpilot.reporting.html import render_html
from benchpilot.testcases.executor import Executor, Outcome
from benchpilot.testcases.plan import plan_test
from benchpilot.testcases.schema import TestCase

from conftest import EXAMPLE_DIR, ScriptedBench, make_session

SAMPLE = 0.1  # ScriptedBench.sample_period


def first_order(t):  # 0 -> 10, tau = 1 s, no overshoot
    return 10 * (1 - math.exp(-t))


def underdamped(t):  # 0 -> 10 with a large overshoot
    return 10 * (1 - math.exp(-t) * math.cos(3 * t))


def slow(t):  # 0 -> 10, tau = 10 s
    return 10 * (1 - math.exp(-t / 10))


def _run(waveform, **response):
    bench = ScriptedBench({"x": waveform})
    session = make_session(bench, ["x"])
    tc = TestCase.model_validate(
        {"id": "T", "title": "t", "steps": [{"expect_response": {"signal": "x", **response}}]}
    )
    result = Executor(session).run(tc)
    return result, result.steps[-1]


def test_first_order_response_measures_textbook_values():
    result, step = _run(
        first_order,
        to=10,
        tolerance=0.5,
        settles_within="5s",
        overshoot_max="1%",
        rise_time_max="3s",
    )
    assert result.passed, result.message
    # Enters 10 ± 0.5 when 1 - e^-t >= 0.95, i.e. t = ln 20 ≈ 3.0 s.
    assert step.metrics["settling_time_s"] == pytest.approx(math.log(20), abs=SAMPLE)
    # 10% -> 90%: t = ln(10/9) to ln 10, i.e. 2.2 s.
    assert step.metrics["rise_time_s"] == pytest.approx(math.log(9), abs=SAMPLE)
    assert step.metrics["overshoot"] == 0
    assert step.band == (9.5, 10.5)


def test_overshoot_percent_fails_with_measured_value():
    result, step = _run(underdamped, to=10, tolerance=0.5, settles_within="10s", overshoot_max="5%")
    assert result.outcome is Outcome.FAILED
    assert step.metrics["overshoot_pct"] > 5
    assert "overshoot" in result.message and "(max 5%)" in result.message
    assert result.message.index("overshoot") < result.message.index("settled")  # failures first


def test_overshoot_in_signal_units():
    peak = max(underdamped(i * SAMPLE) for i in range(100))
    result, step = _run(underdamped, to=10, observe="10s", overshoot_max=peak - 10 + 0.01)
    assert result.passed, result.message
    assert step.metrics["overshoot"] == pytest.approx(peak - 10)


def test_slow_rise_fails():
    result, _ = _run(slow, to=10, observe="5s", rise_time_max="2s")
    assert result.outcome is Outcome.FAILED
    assert "never reached 90% of the step" in result.message


def test_never_settling_fails():
    result, _ = _run(
        lambda t: 10 + 3 * math.sin(4 * t),
        to=10,
        tolerance=0.5,
        settles_within="3s",
        **{"from": 0},
    )
    assert result.outcome is Outcome.FAILED
    assert "never settled within ±0.5 of 10" in result.message


def test_downward_step():
    result, step = _run(
        lambda t: 10 * math.exp(-t),
        to=0,
        tolerance=0.5,
        settles_within="5s",
        overshoot_max="1%",
        rise_time_max="3s",
    )
    assert result.passed, result.message
    assert step.metrics["rise_time_s"] == pytest.approx(math.log(9), abs=SAMPLE)


def test_origin_is_read_when_from_is_omitted():
    result, _ = _run(lambda t: 4.0, to=10, observe="1s", overshoot_max="1%")
    assert result.passed  # flat at 4: never overshoots
    result, _ = _run(lambda t: 10.0, to=10, observe="1s", overshoot_max="1%")
    assert result.outcome is Outcome.FAILED
    assert "no step to 10 to measure" in result.message


def test_broken_readings_count_as_out_of_band():
    def flaky(t):
        return math.nan if t < 2 else 10.0

    result, step = _run(flaky, to=10, tolerance=0.5, settles_within="5s", **{"from": 0})
    assert result.passed, result.message
    assert step.metrics["settling_time_s"] == pytest.approx(2.0, abs=SAMPLE)


def test_settles_early_and_stops_watching():
    result, step = _run(
        lambda t: 10.0, to=10, tolerance=0.5, settles_within="60s", hold_for="1s", **{"from": 0}
    )
    assert result.passed
    assert step.end - step.start == pytest.approx(1.0, abs=SAMPLE)  # not 61 s


@pytest.mark.parametrize(
    "body, message",
    [
        ({"to": 10, "tolerance": 1}, "at least one of"),
        ({"to": 10, "settles_within": "5s"}, "positive 'tolerance'"),
        ({"to": 10, "overshoot_max": "2%"}, "give 'observe'"),
        ({"to": 10, "from": 10, "observe": "1s", "overshoot_max": 1}, "no step to measure"),
        ({"to": 10, "observe": "1s", "overshoot_max": "-2%"}, "invalid overshoot"),
        ({"to": 10, "observe": "1s", "overshoot_max": "lots"}, "invalid overshoot"),
        ({"to": 10, "observe": "1s", "rise_time_max": "2s", "tolerance": -1}, "negative"),
    ],
)
def test_schema_validation(body, message):
    with pytest.raises(ValidationError, match=message):
        TestCase.model_validate(
            {"id": "T", "title": "t", "steps": [{"expect_response": {"signal": "x", **body}}]}
        )


def test_dry_run_describes_and_bounds_time():
    config = BenchConfig.model_validate({"bench": {"adapter": "any"}, "signals": {"x": "p/x"}})
    tc = TestCase.model_validate(
        {
            "id": "T",
            "title": "t",
            "steps": [
                {
                    "expect_response": {
                        "signal": "x",
                        "to": 10,
                        "tolerance": 1,
                        "settles_within": "15s",
                        "overshoot_max": "2%",
                    }
                }
            ],
        }
    )
    plan = plan_test(tc, config)
    assert "expect_response x -> 10, settles to ±1 within 15s, overshoot <= 2%" in plan.lines[0]
    assert plan.max_bench_time_s == pytest.approx(16.0)  # settles_within + default hold 1 s


def test_report_shows_measurements():
    result, _ = _run(first_order, to=10, tolerance=0.5, settles_within="5s", rise_time_max="3s")
    html = render_html([result], "rig", "sim")
    assert "measured: settled after 3.00s" in html and "rise 2.20s" in html


def test_example_suite_uses_expect_response(capsys):
    text = (EXAMPLE_DIR / "tests" / "cc_004_accel_button.yaml").read_text()
    assert "expect_response:" in text
    assert main(["run", str(EXAMPLE_DIR / "tests" / "cc_004_accel_button.yaml")]) == 0
