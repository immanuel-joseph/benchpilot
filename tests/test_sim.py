import pytest

from benchpilot.adapters.sim import create_bench, create_debugger
from benchpilot.core.config import load_bench_config
from benchpilot.core.errors import AdapterError, ConfigError
from benchpilot.core.session import Session
from benchpilot.sim import CruiseState
from benchpilot.testcases.executor import Executor, run_suite
from benchpilot.testcases.schema import TestCase, collect_testcase_files, load_testcase

from conftest import EXAMPLE_DIR, ScriptedBench


def test_vehicle_coasts_down_without_throttle(sim_session):
    sim_session.set("vehicle.speed_kph", 100)
    sim_session.wait(5)
    speed = sim_session.get("vehicle.speed_kph")
    assert 80 < speed < 100


def test_uphill_slows_more_than_flat():
    def coast(grade):
        bench = create_bench({})
        bench.connect()
        bench.set("plant/speed_kph", 80)
        bench.set("plant/grade_pct", grade)
        bench.wait(5)
        return bench.get("plant/speed_kph")

    assert coast(5) < coast(0) < coast(-5)


def test_cruise_engages_and_holds(sim_session):
    s = sim_session
    s.set("vehicle.speed_kph", 70)
    s.set("cruise.enable", 1)
    s.wait(0.1)
    assert s.read_var("cc_state") == CruiseState.STANDBY
    s.set("cruise.set_button", 1)
    s.wait(0.1)
    assert s.read_var("cc_state") == CruiseState.ACTIVE
    s.wait(20)
    assert s.get("vehicle.speed_kph") == pytest.approx(s.read_var("cc_set_speed_kph"), abs=0.5)


def test_time_is_simulated(sim_session):
    t0 = sim_session.time()
    sim_session.wait(3600)  # an hour of bench time
    assert sim_session.time() - t0 == pytest.approx(3600)


def test_capture_records_logical_names(sim_session):
    sim_session.start_capture(["vehicle.speed_kph"])
    sim_session.set("vehicle.speed_kph", 50)
    sim_session.wait(0.05)
    capture = sim_session.stop_capture()
    assert capture.signals == ["vehicle.speed_kph"]
    assert len(capture.time) == len(capture.values["vehicle.speed_kph"]) == 7
    assert capture.values["vehicle.speed_kph"][0] == 0
    assert capture.values["vehicle.speed_kph"][1] == 50


def test_reset_restores_initial_state(sim_session):
    sim_session.set("vehicle.speed_kph", 90)
    sim_session.write_var("cc_state", 2)
    sim_session.wait(1)
    sim_session.reset()
    assert sim_session.get("vehicle.speed_kph") == 0
    assert sim_session.read_var("cc_state") == 0
    assert sim_session.time() == 0


def test_read_only_and_unknown_paths(sim_session):
    with pytest.raises(AdapterError, match="read-only"):
        sim_session.bench.set("plant/throttle_pct", 50)
    with pytest.raises(AdapterError, match="no signal path"):
        sim_session.bench.get("plant/warp_factor")
    with pytest.raises(AdapterError, match="unknown ECU variable"):
        sim_session.read_var("nope")


def test_bad_options():
    with pytest.raises(ConfigError, match="unknown model"):
        create_bench({"model": "tractor"})
    with pytest.raises(ConfigError, match="unknown options"):
        create_bench({"speed": 1})
    with pytest.raises(ConfigError, match="only works with the sim bench"):
        create_debugger({}, ScriptedBench())


def test_driver_pedal_overrides_cruise(sim_session):
    tc = TestCase.model_validate(
        {
            "id": "T",
            "title": "t",
            "setup": [
                {"set": {"signal": "vehicle.speed_kph", "value": 60}},
                {"set": {"signal": "cruise.enable", "value": 1}},
                {"wait": "0.1s"},
                {"set": {"signal": "cruise.set_button", "value": 1}},
                {"wait": "0.1s"},
            ],
            "steps": [
                {"expect": {"ecu_var": "cc_state", "equals": 2}},
                {"set": {"signal": "driver.accel_pedal_pct", "value": 100}},
                {"expect": {"signal": "engine.throttle_pct", "equals": 100, "within": "0.1s"}},
            ],
        }
    )
    result = Executor(sim_session).run(tc)
    assert result.passed, result.message


@pytest.mark.parametrize(
    "fault, failing_test",
    [
        ("brake_cancel_ignored", "CC-002"),
        ("low_min_engage", "CC-003"),
        ("double_accel_step", "CC-004"),
        ("no_integral", "CC-005"),
    ],
)
def test_example_suite_catches_each_injected_fault(fault, failing_test):
    """Mutation check: a suite that passes on buggy firmware proves nothing."""
    config = load_bench_config(EXAMPLE_DIR / "bench.yaml")
    config.bench.options["faults"] = [fault]
    files = collect_testcase_files([EXAMPLE_DIR])
    with Session.from_config(config) as session:
        results = run_suite(session, [(f, load_testcase(f)) for f in files])
    failed = [r.testcase.id for r in results if not r.passed]
    assert failed == [failing_test]


def test_unknown_fault_is_a_config_error():
    with pytest.raises(ConfigError, match="unknown fault 'gremlins'"):
        create_bench({"faults": ["gremlins"]})
