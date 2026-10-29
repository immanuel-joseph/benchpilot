import pytest
from pydantic import ValidationError

from benchpilot.core.errors import TestCaseError
from benchpilot.testcases.schema import (
    ExpectStep,
    RampStep,
    SetStep,
    TestCase,
    WaitStep,
    collect_testcase_files,
    load_testcase,
    parse_duration,
)


@pytest.mark.parametrize(
    "raw, seconds",
    [("2s", 2.0), ("500ms", 0.5), ("1.5min", 90.0), (".5s", 0.5), (3, 3.0), ("4", 4.0)],
)
def test_parse_duration(raw, seconds):
    assert parse_duration(raw) == pytest.approx(seconds)


@pytest.mark.parametrize("raw", ["2 hours", "-1s", "", True, None, -2])
def test_parse_duration_rejects(raw):
    with pytest.raises(ValueError):
        parse_duration(raw)


def test_parses_every_step_kind():
    tc = TestCase.model_validate(
        {
            "id": "T-1",
            "title": "all steps",
            "steps": [
                {"set": {"signal": "a", "value": 1}},
                {"set": {"ecu_var": "x", "value": 2}},
                {"wait": "250ms"},
                {"ramp": {"signal": "a", "from": 0, "to": 5, "over": "2s"}},
                {"expect": {"signal": "a", "equals": 5, "tolerance": 0.1, "within": "1s"}},
                {"expect": {"ecu_var": "x", "min": 1, "hold_for": "1s"}},
            ],
        }
    )
    kinds = [type(s) for s in tc.steps]
    assert kinds == [SetStep, SetStep, WaitStep, RampStep, ExpectStep, ExpectStep]
    assert tc.steps[2].duration == 0.25
    assert tc.steps[3].from_ == 0
    assert tc.signals_used() == ["a"]


@pytest.mark.parametrize(
    "step, message",
    [
        ({"jump": {"signal": "a"}}, "unknown step 'jump'"),
        ({"set": {"value": 1}}, "exactly one of 'signal' or 'ecu_var'"),
        ({"set": {"signal": "a", "ecu_var": "x", "value": 1}}, "exactly one of"),
        ({"set": {"signal": "a", "value": 1, "vlaue": 2}}, "Extra inputs"),
        ({"expect": {"signal": "a"}}, "either 'equals'"),
        ({"expect": {"signal": "a", "equals": 1, "max": 2}}, "either 'equals'"),
        ({"expect": {"signal": "a", "min": 1, "tolerance": 1}}, "'tolerance' only applies"),
        ({"expect": {"signal": "a", "min": 3, "max": 1}}, "min is greater than max"),
        ({"wait": "soon"}, "invalid duration"),
        ({"set": {"signal": "a", "value": 1}, "wait": "1s"}, "one-key mapping"),
    ],
)
def test_rejects_bad_steps(step, message):
    with pytest.raises(ValidationError, match=message):
        TestCase.model_validate({"id": "T", "title": "t", "steps": [step]})


def test_requires_steps():
    with pytest.raises(ValidationError, match="at least one step"):
        TestCase.model_validate({"id": "T", "title": "t", "steps": []})


def test_expect_accepts():
    band = ExpectStep(signal="s", equals=80, tolerance=1.5)
    assert band.accepts(81.5) and band.accepts(78.5) and not band.accepts(81.51)
    floor = ExpectStep(signal="s", min=10)
    assert floor.accepts(10) and floor.accepts(1e9) and not floor.accepts(9.99)
    assert band.describe() == "80 ± 1.5"


def test_load_testcase_reports_file_and_location(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "id: T\ntitle: t\nsteps:\n  - wait: 1s\n  - expect: {signal: a, equals: 1, within: x}\n"
    )
    with pytest.raises(TestCaseError) as exc:
        load_testcase(path)
    assert str(path) in str(exc.value)
    assert "steps[1].within" in str(exc.value)


def test_collect_skips_bench_yaml(tmp_path):
    (tmp_path / "bench.yaml").write_text("x: 1")
    (tmp_path / "faulty.bench.yaml").write_text("x: 1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.yml").write_text("")
    (tmp_path / "a.yaml").write_text("")
    (tmp_path / "notes.md").write_text("")
    names = [p.name for p in collect_testcase_files([tmp_path])]
    assert names == ["a.yaml", "b.yml"]
