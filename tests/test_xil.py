"""XIL adapter, exercised against the fake XIL testbench (no dSPACE needed)."""

import math

import pytest
import yaml

from benchpilot.adapters.xil import XilBench, create_bench
from benchpilot.adapters.xil.fake import FakeTestbench
from benchpilot.cli import EXIT_OK, EXIT_TESTS_FAILED, EXIT_USAGE, main
from benchpilot.core.config import BenchConfig, load_bench_config
from benchpilot.core.contract import run_contract
from benchpilot.core.errors import AdapterError, ConfigError
from benchpilot.core.safety import Limit, SafetyInterlock
from benchpilot.core.session import Session
from benchpilot.core.signals import SignalMap
from benchpilot.testcases.executor import Executor
from benchpilot.testcases.schema import TestCase

from conftest import EXAMPLE_DIR

VARIABLES = {
    "Model Root/Vehicle/Speed[km|h]/Value": 42.0,
    "Model Root/Road/Grade[%]/Value": 0.0,
    "Model Root/Cruise/Mode/Value": 0,  # integer variable
    "Model Root/Cruise/Enable/Value": False,  # boolean variable
}


def _bench(variables=None, **options) -> XilBench:
    options = {
        "backend": "fake",
        "sample_period": 0.001,
        "fake_variables": dict(VARIABLES if variables is None else variables),
        **options,
    }
    bench = create_bench(options)
    bench.connect()
    return bench


def _port(bench: XilBench):
    return bench._port


# -- connection ----------------------------------------------------------------


def test_connect_configures_port_without_starting_simulation():
    bench = _bench()
    assert _port(bench).configured_with is not None
    assert _port(bench).State == "eSIMULATION_STOPPED"


def test_start_simulation_option():
    bench = _bench(start_simulation=True)
    assert _port(bench).State == "eSIMULATION_RUNNING"


def test_disconnect_disposes_port():
    bench = _bench()
    port = _port(bench)
    bench.disconnect()
    assert port.disposed
    with pytest.raises(AdapterError, match="not connected"):
        bench.get("Model Root/Vehicle/Speed[km|h]/Value")


# -- typed reads and writes ------------------------------------------------------


def test_read_and_write_float():
    bench = _bench()
    path = "Model Root/Road/Grade[%]/Value"
    bench.set(path, 3.5)
    assert bench.get(path) == 3.5


def test_writes_match_the_variable_type():
    bench = _bench()
    bench.set("Model Root/Cruise/Mode/Value", 2)
    bench.set("Model Root/Cruise/Enable/Value", 1)
    assert _port(bench).writes == [
        ("Model Root/Cruise/Mode/Value", 2),
        ("Model Root/Cruise/Enable/Value", True),
    ]
    assert bench.get("Model Root/Cruise/Enable/Value") == 1.0


@pytest.mark.parametrize(
    "path, value, message",
    [
        ("Model Root/Cruise/Mode/Value", 2.5, "is int; can't write 2.5"),
        ("Model Root/Cruise/Enable/Value", 2, "is boolean; can't write 2"),
    ],
)
def test_rejects_values_the_variable_type_cannot_hold(path, value, message):
    bench = _bench()
    with pytest.raises(AdapterError, match=message):
        bench.set(path, value)
    assert _port(bench).writes == []


def test_unknown_variable_reports_xil_error():
    bench = _bench()
    with pytest.raises(AdapterError, match="does not exist"):
        bench.get("Model Root/Nope")


# -- capture -------------------------------------------------------------------------


def test_polled_capture_samples_during_wait():
    bench = _bench()
    path = "Model Root/Vehicle/Speed[km|h]/Value"
    bench.start_capture([path])
    bench.wait(0.01)
    capture = bench.stop_capture()
    assert len(capture.time) >= 3
    assert len(capture.values[path]) == len(capture.time)
    assert capture.time == sorted(capture.time)


def test_failed_read_during_capture_leaves_a_gap_not_a_crash():
    bench = _bench()
    path = "Model Root/Vehicle/Speed[km|h]/Value"
    bench.start_capture([path])
    del _port(bench).variables[path]
    bench.wait(0.005)
    capture = bench.stop_capture()
    assert math.isnan(capture.values[path][-1])


# -- options ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "options, message",
    [
        ({}, "needs option 'product_version'"),
        ({"product_version": "2023-B"}, "needs option 'maport_config'"),
        ({"backend": "corba"}, "must be 'dotnet' or 'fake'"),
        ({"port": 1}, "unknown options port"),
        ({"product_version": "x", "maport_config": "c.xml", "sample_period": 0}, "positive"),
    ],
)
def test_option_validation(options, message):
    with pytest.raises(ConfigError, match=message):
        create_bench(options)


def test_dotnet_backend_explains_missing_pythonnet():
    try:
        import clr  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("pythonnet is installed here")
    bench = create_bench({"product_version": "2023-B", "maport_config": "c.xml"})
    with pytest.raises(ConfigError, match=r'pip install "benchpilot\[xil\]"'):
        bench.connect()


# -- contract and end to end ----------------------------------------------------------


def _xil_session(limits=None) -> Session:
    bench = create_bench({"backend": "fake", "sample_period": 0.001, "fake_variables": VARIABLES})
    return Session(
        bench=bench,
        signals=SignalMap(
            {
                "vehicle.speed_kph": "Model Root/Vehicle/Speed[km|h]/Value",
                "road.grade_pct": "Model Root/Road/Grade[%]/Value",
                "cruise.mode": "Model Root/Cruise/Mode/Value",
                "cruise.enable": "Model Root/Cruise/Enable/Value",
            }
        ),
        interlock=SafetyInterlock(limits or {}),
    )


def test_bench_contract_passes_on_fake_xil():
    checks = run_contract(_xil_session(), writes={"road.grade_pct": 2.0, "cruise.enable": 1})
    failed = [c for c in checks if not c.ok]
    assert failed == []
    names = [c.name for c in checks]
    assert "restore road.grade_pct = 0" in names
    assert names[0] == "connect" and names[-1] == "disconnect"


def test_contract_write_respects_interlock():
    session = _xil_session(limits={"road.grade_pct": Limit(max=1)})
    checks = run_contract(session, writes={"road.grade_pct": 5})
    write = next(c for c in checks if c.name.startswith("write road.grade_pct"))
    assert not write.ok and "SafetyViolation" in write.detail
    assert session.bench._port is None  # disconnected afterwards


def test_test_case_runs_on_fake_xil():
    session = _xil_session()
    session.connect()
    tc = TestCase.model_validate(
        {
            "id": "X-1",
            "title": "write and verify over XIL",
            "steps": [
                {"set": {"signal": "road.grade_pct", "value": 4}},
                {"set": {"signal": "cruise.mode", "value": 2}},
                {"expect": {"signal": "road.grade_pct", "equals": 4, "within": "10ms"}},
                {"expect": {"signal": "cruise.mode", "equals": 2}},
            ],
            "teardown": [{"set": {"signal": "road.grade_pct", "value": 0}}],
        }
    )
    result = Executor(session).run(tc)
    assert result.passed, result.message
    assert result.capture.values["road.grade_pct"][-1] == 0.0


# -- CLI: check-bench ---------------------------------------------------------------


def test_check_bench_on_simulator(capsys):
    bench = str(EXAMPLE_DIR / "bench.yaml")
    assert main(["check-bench", "--bench", bench]) == EXIT_OK
    out = capsys.readouterr().out
    assert "read-only" in out and "read vehicle.speed_kph" in out and "0 failed" in out


def test_check_bench_write_goes_through_interlock(capsys):
    bench = str(EXAMPLE_DIR / "bench.yaml")
    assert main(["check-bench", "--bench", bench, "--write", "road.grade_pct=50"]) == (
        EXIT_TESTS_FAILED
    )
    assert "SafetyViolation" in capsys.readouterr().out


@pytest.mark.parametrize("arg", ["road.grade_pct", "road.grade_pct=fast", "=3", "x=nan"])
def test_check_bench_rejects_bad_write_args(arg, capsys):
    bench = str(EXAMPLE_DIR / "bench.yaml")
    assert main(["check-bench", "--bench", bench, "--write", arg]) == EXIT_USAGE


def test_check_bench_rejects_unknown_write_signal(capsys):
    bench = str(EXAMPLE_DIR / "bench.yaml")
    assert main(["check-bench", "--bench", bench, "--write", "warp.drive=1"]) == EXIT_USAGE
    assert "unknown signal" in capsys.readouterr().err


def test_check_bench_on_fake_xil_bench_file(tmp_path, capsys):
    config = {
        "name": "xil-fake",
        "bench": {
            "adapter": "xil",
            "options": {"backend": "fake", "sample_period": 0.001, "fake_variables": VARIABLES},
        },
        "signals": {"vehicle.speed_kph": "Model Root/Vehicle/Speed[km|h]/Value"},
    }
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(config))
    assert main(["check-bench", "--bench", str(path)]) == EXIT_OK
    assert "= 42" in capsys.readouterr().out


def test_dspace_template_is_a_valid_bench_config():
    config = load_bench_config(EXAMPLE_DIR / "dspace.bench.yaml")
    assert isinstance(config, BenchConfig)
    assert config.bench.adapter == "xil"
    sim = load_bench_config(EXAMPLE_DIR / "bench.yaml")
    assert set(config.signals) == set(sim.signals)  # same tests run on both benches


def test_fake_testbench_matches_adapter_usage():
    # Guard: the fake must keep the object shape the adapter relies on.
    tb = FakeTestbench({"a": 1.0})
    port = tb.MAPortFactory.CreateMAPort("p")
    port.Configure(port.LoadConfiguration("x.xml"), False)
    port.Write("a", tb.ValueFactory.CreateFloatValue(2.0))
    assert port.Read("a").Value == 2.0
