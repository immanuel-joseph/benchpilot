"""TRACE32 debug adapter, exercised against the fake Remote API (no Lauterbach needed)."""

import pytest
import yaml

from benchpilot.adapters.trace32 import Trace32Debugger, create_debugger
from benchpilot.cli import EXIT_OK, EXIT_TESTS_FAILED, EXIT_USAGE, main
from benchpilot.core.contract import run_contract
from benchpilot.core.errors import AdapterError, ConfigError, SafetyViolation
from benchpilot.core.safety import Limit, SafetyInterlock
from benchpilot.core.session import Session
from benchpilot.core.signals import SignalMap

from conftest import EXAMPLE_DIR, ScriptedBench

ECU = {"cc_state": 2, "cc_set_speed_kph": 80.0, "mode": 0}


def _dbg(variables=None, **options) -> Trace32Debugger:
    dbg = create_debugger(
        {
            "backend": "fake",
            "fake_variables": dict(ECU if variables is None else variables),
            **options,
        },
        bench=ScriptedBench(),
    )
    dbg.connect()
    return dbg


def test_reads_symbols_as_numbers():
    dbg = _dbg()
    assert dbg.read_var("cc_state") == 2.0
    assert dbg.read_var("cc_set_speed_kph") == 80.0


def test_whole_numbers_are_written_as_integers():
    dbg = _dbg()
    dbg.write_var("mode", 3.0)
    dbg.write_var("cc_set_speed_kph", 85.5)
    assert dbg._dbg.writes == [("mode", 3), ("cc_set_speed_kph", 85.5)]
    assert isinstance(dbg._dbg.writes[0][1], int)


def test_fractional_write_to_integer_symbol_fails_cleanly():
    with pytest.raises(AdapterError, match="TRACE32 write 'mode' failed"):
        _dbg().write_var("mode", 2.5)


def test_unknown_symbol():
    with pytest.raises(AdapterError, match="symbol not found"):
        _dbg().read_var("nope")


def test_disconnect_and_use_after():
    dbg = _dbg()
    fake = dbg._dbg
    dbg.disconnect()
    assert not fake.connected
    with pytest.raises(AdapterError, match="not connected"):
        dbg.read_var("cc_state")


def test_reset_is_opt_in():
    dbg = _dbg()
    dbg.reset_target()
    assert dbg._dbg.commands == []  # never resets a real ECU unless configured
    dbg = _dbg(reset_commands=["SYStem.Up", "Go"])
    dbg.reset_target()
    assert dbg._dbg.commands == ["SYStem.Up", "Go"]


def test_connection_failure_explains_setup():
    def refuse(**kwargs):
        raise ConnectionRefusedError("refused")

    dbg = Trace32Debugger({**create_debugger({}, ScriptedBench()).options}, refuse)
    with pytest.raises(AdapterError, match="Is PowerView running with RCL=NETTCP"):
        dbg.connect()


def test_missing_module_explains_install():
    try:
        import lauterbach.trace32.rcl  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("Lauterbach's module is installed here")
    with pytest.raises(ConfigError, match=r'pip install "benchpilot\[trace32\]"'):
        create_debugger({}, ScriptedBench()).connect()


@pytest.mark.parametrize(
    "options, message",
    [
        ({"host": "x"}, "unknown options host"),
        ({"backend": "jtag"}, "must be 'rcl' or 'fake'"),
        ({"reset_commands": "Go"}, "list of strings"),
    ],
)
def test_option_validation(options, message):
    with pytest.raises(ConfigError, match=message):
        create_debugger(options, ScriptedBench())


# -- session and contract ----------------------------------------------------------------


def _session(ecu_limits=None) -> Session:
    bench = ScriptedBench({"speed": lambda t: 80.0})
    return Session(
        bench=bench,
        debug=create_debugger({"backend": "fake", "fake_variables": ECU}, bench),
        signals=SignalMap({"speed": "speed"}),
        interlock=SafetyInterlock({}),
        ecu_interlock=SafetyInterlock(ecu_limits or {}, label="ecu_var "),
    )


def test_session_connects_and_disconnects_both_sides():
    session = _session()
    session.connect()
    assert session.read_var("cc_state") == 2
    session.disconnect()
    with pytest.raises(AdapterError):
        session.read_var("cc_state")


def test_debugger_connect_failure_disconnects_bench():
    class Bench(ScriptedBench):
        connected = False

        def connect(self):
            self.connected = True

        def disconnect(self):
            self.connected = False

    bench = Bench()

    def refuse(**kwargs):
        raise OSError("no route")

    session = Session(
        bench=bench,
        debug=Trace32Debugger(create_debugger({}, bench).options, refuse),
        signals=SignalMap({}),
        interlock=SafetyInterlock({}),
    )
    with pytest.raises(AdapterError):
        session.connect()
    assert not bench.connected


def test_contract_covers_the_debugger():
    checks = run_contract(
        _session(), ecu_vars=["cc_state", "cc_set_speed_kph"], ecu_writes={"mode": 1}
    )
    debugger_checks = [c for c in checks if "ecu_var" in c.name]
    assert len(debugger_checks) == 5
    assert [c for c in debugger_checks if not c.ok] == []
    names = [c.name for c in checks]
    assert "read ecu_var cc_state" in names
    assert "unknown ecu_var is rejected" in names
    assert "restore ecu_var mode = 0" in names


def test_contract_ecu_write_respects_interlock():
    checks = run_contract(_session({"mode": Limit(max=1)}), ecu_writes={"mode": 5})
    write = next(c for c in checks if c.name.startswith("write ecu_var mode"))
    assert not write.ok and "SafetyViolation" in write.detail


def test_ecu_limits_still_block_writes():
    session = _session({"mode": Limit(max=1)})
    session.connect()
    with pytest.raises(SafetyViolation):
        session.write_var("mode", 5)


# -- full stack: fake XIL bench + fake TRACE32 from a bench.yaml ---------------------------


def _bench_file(tmp_path) -> str:
    config = {
        "name": "xil+trace32 (fake)",
        "bench": {
            "adapter": "xil",
            "options": {
                "backend": "fake",
                "sample_period": 0.001,
                "fake_variables": {"Model Root/Speed/Value": 80.0},
            },
        },
        "debug": {
            "adapter": "trace32",
            "options": {"backend": "fake", "fake_variables": {"cc_state": 2, "mode": 0}},
        },
        "signals": {"vehicle.speed_kph": "Model Root/Speed/Value"},
        "ecu_limits": {"mode": {"min": 0, "max": 3}, "cc_state": {"min": 0, "max": 2}},
    }
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(config))
    return str(path)


def test_test_case_with_ecu_vars_runs_on_xil_plus_trace32(tmp_path):
    bench = _bench_file(tmp_path)
    (tmp_path / "t.yaml").write_text(
        "id: E-1\ntitle: ECU state over the debugger\nsteps:\n"
        "  - expect: {signal: vehicle.speed_kph, equals: 80}\n"
        "  - expect: {ecu_var: cc_state, equals: 2}\n"
        "  - set: {ecu_var: mode, value: 3}\n"
        "  - expect: {ecu_var: mode, equals: 3}\n"
        "teardown:\n  - set: {ecu_var: mode, value: 0}\n"
    )
    assert main(["run", str(tmp_path), "--bench", bench]) == EXIT_OK


def test_check_bench_reads_and_writes_ecu_vars(tmp_path, capsys):
    bench = _bench_file(tmp_path)
    assert main(["check-bench", "--bench", bench, "--write-var", "mode=2"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "read ecu_var cc_state  = 2" in out  # from ecu_limits
    assert "write ecu_var mode = 2 and read back" in out
    assert "restore ecu_var mode = 0" in out
    assert main(["check-bench", "--bench", bench, "--write-var", "mode=9"]) == EXIT_TESTS_FAILED


def test_check_bench_var_flags_need_a_debugger(tmp_path, capsys):
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump({"bench": {"adapter": "sim"}, "signals": {}}))
    assert main(["check-bench", "--bench", str(path), "--read-var", "cc_state"]) == EXIT_USAGE
    assert "need a 'debug' adapter" in capsys.readouterr().err


@pytest.mark.parametrize("arg", ["mode", "mode=", "=1", "mode=inf"])
def test_check_bench_rejects_bad_write_var(arg):
    bench = str(EXAMPLE_DIR / "bench.yaml")
    assert main(["check-bench", "--bench", bench, "--write-var", arg]) == EXIT_USAGE


def test_dspace_template_includes_the_debugger():
    from benchpilot.core.config import load_bench_config

    config = load_bench_config(EXAMPLE_DIR / "dspace.bench.yaml")
    assert config.debug is not None and config.debug.adapter == "trace32"
