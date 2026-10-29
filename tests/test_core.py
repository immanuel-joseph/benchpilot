import pytest

from benchpilot.core.config import BenchConfig, find_bench_config, load_bench_config
from benchpilot.core.errors import ConfigError, SafetyViolation, UnknownSignal
from benchpilot.core.registry import load_bench_factory
from benchpilot.core.safety import Limit, SafetyInterlock
from benchpilot.core.session import Session
from benchpilot.core.signals import SignalMap

from conftest import EXAMPLE_DIR, ScriptedBench, make_session


def test_signal_map_suggests_close_match():
    signals = SignalMap({"vehicle.speed_kph": "plant/speed_kph"})
    assert signals.resolve("vehicle.speed_kph") == "plant/speed_kph"
    assert signals.name_of("plant/speed_kph") == "vehicle.speed_kph"
    with pytest.raises(UnknownSignal, match="did you mean 'vehicle.speed_kph'"):
        signals.resolve("vehicle.sped_kph")


def test_interlock_blocks_out_of_range():
    interlock = SafetyInterlock({"grade": Limit(min=-10, max=10), "speed": Limit(max=180)})
    interlock.check("grade", 10)
    interlock.check("speed", -50)  # no lower limit
    interlock.check("unlimited", 1e9)
    with pytest.raises(SafetyViolation, match=r"grade = 10.5.*\[-10, 10\]"):
        interlock.check("grade", 10.5)


def test_session_blocks_before_reaching_adapter():
    bench = ScriptedBench()
    session = make_session(bench, ["grade"], limits={"grade": Limit(max=10)})
    session.set("grade", 5)
    with pytest.raises(SafetyViolation):
        session.set("grade", 50)
    assert bench.written["grade"] == [5]


def test_example_bench_config_loads():
    config = load_bench_config(EXAMPLE_DIR / "bench.yaml")
    assert config.bench.adapter == "sim"
    assert config.limits["road.grade_pct"].max == 12


def test_find_bench_config_walks_up():
    assert find_bench_config(EXAMPLE_DIR / "tests") == EXAMPLE_DIR / "bench.yaml"


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"signals": {}}, "bench"),
        ({"bench": {"adapter": "sim"}, "limits": {"x": {"max": 1}}}, "not in the signal map"),
        ({"bench": {"adapter": "sim"}, "signals": {"x": "x"}, "limits": {"x": {}}}, "at least"),
        ({"bench": {"adapter": "sim"}, "benhc": 1}, "Extra inputs"),
    ],
)
def test_bad_bench_config(tmp_path, raw, message):
    import yaml

    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match=message):
        load_bench_config(path)


def test_unknown_adapter_lists_available():
    with pytest.raises(ConfigError, match="no bench adapter named 'dspace'.*sim"):
        load_bench_factory("dspace")


def test_session_from_config_wires_debugger():
    config = BenchConfig.model_validate({"bench": {"adapter": "sim"}, "debug": {"adapter": "sim"}})
    session = Session.from_config(config)
    assert session.debug is not None
