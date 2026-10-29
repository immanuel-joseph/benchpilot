import pytest

import benchpilot.core.registry as registry
from benchpilot.cli import EXIT_OK, EXIT_TESTS_FAILED, main
from benchpilot.core.config import BenchConfig, load_bench_config
from benchpilot.testcases.plan import plan_test
from benchpilot.testcases.schema import TestCase

from conftest import EXAMPLE_DIR


@pytest.fixture(autouse=True)
def no_adapters(monkeypatch):
    """A dry run must never create an adapter, i.e. never touch a bench."""

    def forbidden(name):
        raise AssertionError(f"dry run created adapter {name!r}")

    monkeypatch.setattr(registry, "load_bench_factory", forbidden)
    monkeypatch.setattr(registry, "load_debug_factory", forbidden)


def _config(**extra) -> BenchConfig:
    return BenchConfig.model_validate(
        {
            "bench": {"adapter": "anything"},
            "signals": {"grade": "Model Root/Grade", "speed": "Model Root/Speed"},
            "limits": {"grade": {"min": -10, "max": 10}},
            **extra,
        }
    )


def _tc(steps, teardown=()):
    return TestCase.model_validate(
        {"id": "T-1", "title": "plan me", "steps": steps, "teardown": list(teardown)}
    )


def test_plan_shows_paths_limits_and_time():
    plan = plan_test(
        _tc(
            [
                {"set": {"signal": "grade", "value": 3}},
                {"wait": "2s"},
                {"ramp": {"signal": "grade", "from": 3, "to": 5, "over": "4s"}},
                {
                    "expect": {
                        "signal": "speed",
                        "equals": 80,
                        "tolerance": 1,
                        "within": "10s",
                        "hold_for": "5s",
                    }
                },
            ],
            teardown=[{"set": {"signal": "grade", "value": 0}}],
        ),
        _config(),
    )
    text = "\n".join(plan.lines)
    assert plan.problems == []
    assert "set grade = 3 -> Model Root/Grade  limit [-10, 10] ok" in text
    assert "ramp grade 3 -> 5 over 4s -> Model Root/Grade  limit [-10, 10] ok" in text
    assert "expect speed 80 ± 1 within 10s held for 5s" in text
    assert "teardown[0]" in text
    assert plan.max_bench_time_s == pytest.approx(2 + 4 + 15)


def test_plan_flags_blocked_writes():
    plan = plan_test(
        _tc(
            [
                {"set": {"signal": "grade", "value": 30}},
                {"ramp": {"signal": "grade", "from": 0, "to": -20, "over": "1s"}},
            ]
        ),
        _config(),
    )
    assert len(plan.problems) == 2
    assert "steps[0] set grade = 30: BLOCKED" in plan.problems[0]
    assert "steps[1] ramp" in plan.problems[1]


def test_plan_flags_unknown_signal_and_missing_debug():
    plan = plan_test(
        _tc(
            [{"set": {"signal": "warp", "value": 1}}, {"expect": {"ecu_var": "mode", "equals": 1}}]
        ),
        _config(),
    )
    assert any("unknown signal 'warp'" in p for p in plan.problems)
    assert any("no 'debug' adapter" in p for p in plan.problems)


def test_ramp_without_from_is_checked_at_runtime():
    plan = plan_test(_tc([{"ramp": {"signal": "grade", "to": 5, "over": "1s"}}]), _config())
    assert plan.problems == []
    assert "start read from the bench at run time" in "\n".join(plan.lines)


def test_ecu_writes_use_ecu_limits():
    config = _config(debug={"adapter": "anything"}, ecu_limits={"mode": {"max": 2}})
    plan = plan_test(_tc([{"set": {"ecu_var": "mode", "value": 5}}]), config)
    assert "ecu_var mode = 5: BLOCKED" in plan.problems[0]


def test_cli_dry_run_on_examples(capsys):
    assert main(["run", str(EXAMPLE_DIR), "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "nothing is sent to the bench" in out
    assert "-> plant/speed_kph" in out
    assert "5 test(s) planned, 0 problem(s)" in out


def test_cli_dry_run_reports_problems(tmp_path, capsys):
    (tmp_path / "bench.yaml").write_text((EXAMPLE_DIR / "bench.yaml").read_text())
    (tmp_path / "t.yaml").write_text(
        "id: X\ntitle: too steep\nsteps:\n  - set: {signal: road.grade_pct, value: 40}\n"
    )
    assert main(["run", str(tmp_path), "--dry-run"]) == EXIT_TESTS_FAILED
    assert "BLOCKED" in capsys.readouterr().out


def test_dry_run_works_with_uninstalled_adapter(tmp_path, capsys):
    # A lab bench file can be dry-run on a laptop without the vendor software.
    config = load_bench_config(EXAMPLE_DIR / "dspace.bench.yaml")
    assert config.bench.adapter == "xil"
    bench = str(EXAMPLE_DIR / "dspace.bench.yaml")
    args = ["run", str(EXAMPLE_DIR / "tests"), "--bench", bench, "--dry-run"]
    # XIL bench + TRACE32 debugger: every example test, ECU checks included, can run.
    assert main(args) == EXIT_OK
    out = capsys.readouterr().out
    assert "-> Model Root/TODO/Vehicle/Speed/Value" in out
    assert "PROBLEM" not in out
