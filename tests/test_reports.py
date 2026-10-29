import math
import xml.etree.ElementTree as ET
from datetime import datetime

import pytest

from benchpilot.adapters.sim import VirtualBench
from benchpilot.cli import EXIT_TESTS_FAILED, EXIT_USAGE, main
from benchpilot.core.interfaces import Capture
from benchpilot.reporting.html import MAX_POINTS, _decimate, render_html
from benchpilot.reporting.junit import junit_xml
from benchpilot.testcases.executor import Executor, Outcome, StepRecord, TestResult
from benchpilot.testcases.schema import TestCase

from conftest import EXAMPLE_DIR, ScriptedBench, make_session

FAULTY = str(EXAMPLE_DIR / "faulty.bench.yaml")


def _tc(id="T-1", title="a test", **extra):
    return TestCase.model_validate({"id": id, "title": title, "steps": [{"wait": "1s"}], **extra})


def _result(outcome=Outcome.PASSED, **kw) -> TestResult:
    return TestResult(testcase=kw.pop("testcase", _tc()), outcome=outcome, **kw)


# -- step records (executor) -----------------------------------------------------------


def test_executor_records_each_step_with_times_and_readings():
    bench = ScriptedBench({"x": lambda t: min(10.0, 2.0 * t)})
    session = make_session(bench, ["x", "out"])
    tc = TestCase.model_validate(
        {
            "id": "T",
            "title": "t",
            "steps": [
                {"set": {"signal": "out", "value": 1}},
                {"expect": {"signal": "x", "equals": 10, "within": "6s"}},
                {"expect": {"signal": "x", "equals": 99}},
            ],
        }
    )
    result = Executor(session).run(tc)
    s0, s1, s2 = result.steps
    assert (s0.location, s0.outcome, s0.signal) == ("steps[0] set", Outcome.PASSED, "out")
    assert s1.band == (10, 10) and s1.outcome is Outcome.PASSED
    assert s1.end == pytest.approx(5.0) and len(s1.samples) == 51
    assert s2.outcome is Outcome.FAILED and s2.samples == [(5.0, 10.0)]


# -- JUnit ---------------------------------------------------------------------------------


def test_junit_counts_and_details():
    results = [
        _result(testcase=_tc("A", requirement="REQ-1", tags=["x", "y"])),
        _result(
            Outcome.FAILED, testcase=_tc("B"), message="speed = 70", location="steps[2] expect"
        ),
        _result(Outcome.ERROR, testcase=_tc("C"), message="blocked\x00 write"),
    ]
    root = ET.fromstring(junit_xml(results, "rig-1", datetime(2026, 1, 2, 3, 4, 5)))
    suite = root.find("testsuite")
    assert (suite.get("tests"), suite.get("failures"), suite.get("errors")) == ("3", "1", "1")
    assert suite.get("hostname") is None  # never record the machine name
    assert suite.get("timestamp") == "2026-01-02T03:04:05"
    a, b, c = suite.findall("testcase")
    props = {p.get("name"): p.get("value") for p in a.find("properties")}
    assert props["requirement"] == "REQ-1" and props["tags"] == "x,y"
    assert b.find("failure").text == "steps[2] expect: speed = 70"
    assert c.find("error").get("message") == "blocked write"  # control char stripped


# -- HTML ----------------------------------------------------------------------------------


def _captured(values, times=None):
    times = times or [i * 0.1 for i in range(len(values))]
    return Capture(time=times, values={"speed": list(values)})


def test_html_escapes_untrusted_text():
    tc = _tc("X<1>", "<script>alert(1)</script>", requirement='"REQ"&')
    html = render_html([_result(Outcome.FAILED, testcase=tc, message="<img src=x>")], "b<", "sim")
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "<img src=x>" not in html
    assert "<script" not in html  # the report itself has no scripts at all


def test_html_opens_failures_and_links_summary():
    html = render_html(
        [_result(testcase=_tc("OK-1")), _result(Outcome.FAILED, testcase=_tc("BAD-1"))],
        "rig",
        "sim",
    )
    assert '<details id="t-OK-1">' in html
    assert '<details id="t-BAD-1" open>' in html
    assert 'href="#t-BAD-1"' in html
    assert "1 passed" in html and "1 failed" in html


def test_html_plots_signal_with_expect_band():
    step = StepRecord(
        "steps[0] expect",
        "expect speed 80 ± 1",
        0.0,
        0.5,
        Outcome.PASSED,
        signal="speed",
        band=(79, 81),
    )
    r = _result(capture=_captured([80.0] * 6), steps=[step])
    html = render_html([r], "rig", "sim")
    assert "<polyline" in html and 'class="band-passed"' in html


def test_html_zooms_to_checked_range_when_needed():
    values = [0.0] + [80.0] * 20
    step = StepRecord(
        "steps[0] expect", "e", 0.5, 2.0, Outcome.PASSED, signal="speed", band=(79, 81)
    )
    html = render_html([_result(capture=_captured(values), steps=[step])], "rig", "sim")
    assert "zoomed to checked range" in html


def test_html_breaks_lines_at_nan_gaps():
    r = _result(capture=_captured([1.0, 2.0, math.nan, 3.0, 4.0]))
    assert render_html([r], "rig", "sim").count("<polyline") == 2


def test_html_ecu_chart_needs_several_readings():
    one = StepRecord(
        "steps[0] expect",
        "e",
        0,
        0,
        Outcome.PASSED,
        band=(2, 2),
        ecu_var="mode",
        samples=[(0.0, 2.0)],
    )
    many = StepRecord(
        "steps[0] expect",
        "e",
        0,
        0.1,
        Outcome.FAILED,
        band=(1, 1),
        ecu_var="mode",
        samples=[(0.0, 2.0), (0.05, 2.0), (0.1, 2.0)],
    )
    assert "ecu_var mode" not in render_html([_result(steps=[one])], "rig", "sim")
    html = render_html([_result(Outcome.FAILED, steps=[many])], "rig", "sim")
    assert "ecu_var mode" in html and 'class="band-failed"' in html


def test_decimation_bounds_points_and_keeps_spikes():
    n = 20_000
    values = [0.0] * n
    values[12_345] = 99.0
    ts, vs = _decimate([i * 0.001 for i in range(n)], values)
    assert len(ts) <= MAX_POINTS
    assert 99.0 in vs


# -- CLI -----------------------------------------------------------------------------------


def test_cli_writes_both_reports(tmp_path, capsys):
    html, junit = tmp_path / "out" / "r.html", tmp_path / "out" / "r.xml"
    code = main(
        ["run", str(EXAMPLE_DIR), "--bench", FAULTY, "--html", str(html), "--junit", str(junit)]
    )
    assert code == EXIT_TESTS_FAILED
    page = html.read_text()
    assert page.count("<details") == 5 and "ecu_var cc_state" in page
    assert ET.parse(junit).getroot().get("failures") == "1"
    out = capsys.readouterr().out
    assert "HTML report:" in out and "JUnit XML:" in out


def test_cli_rejects_reports_with_dry_run(tmp_path):
    args = ["run", str(EXAMPLE_DIR), "--dry-run", "--html", str(tmp_path / "r.html")]
    assert main(args) == EXIT_USAGE


def test_cli_interrupted_run_still_writes_reports(tmp_path, monkeypatch):
    calls = {"n": 0}
    original = VirtualBench.wait

    def wait(self, seconds):
        calls["n"] += 1
        if calls["n"] == 40:  # partway through the suite
            raise KeyboardInterrupt
        original(self, seconds)

    monkeypatch.setattr(VirtualBench, "wait", wait)
    junit = tmp_path / "r.xml"
    assert main(["run", str(EXAMPLE_DIR), "--junit", str(junit)]) == 130
    suite = ET.parse(junit).getroot()
    assert int(suite.get("tests")) >= 1 and int(suite.get("errors")) == 1
