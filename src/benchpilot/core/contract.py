"""Bench contract: checks every bench adapter must pass, real or simulated.

The same checks run in this repo's unit tests (against the simulator and the
fake XIL testbench) and on a real bench through `benchpilot check-bench`, so
an adapter written from documentation can be proven on hardware by its user.

By default every check is read-only. Writes happen only for signals the
caller names explicitly; they go through the session's safety interlocks, and
each written signal is restored to its original value afterwards.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from benchpilot.core.session import Session

UNKNOWN_PATH = "__benchpilot_contract_no_such_signal__"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def run_contract(
    session: Session,
    writes: dict[str, float] | None = None,
    on_check: Callable[[Check], None] | None = None,
    ecu_vars: list[str] | None = None,
    ecu_writes: dict[str, float] | None = None,
) -> list[Check]:
    """Connect, run the checks, disconnect. Returns one Check per item.

    With a debug adapter configured, also reads each of `ecu_vars`, checks that
    an unknown variable is rejected, and runs `ecu_writes` (through the ECU
    interlock, each restored afterwards).
    """
    checks: list[Check] = []

    def record(name: str, ok: bool, detail: str = "") -> bool:
        check = Check(name, ok, detail)
        checks.append(check)
        if on_check is not None:
            on_check(check)
        return ok

    try:
        session.connect()
    except Exception as exc:
        record("connect", False, _err(exc))
        return checks
    record("connect", True)

    try:
        names = session.signals.names
        for name in names:
            try:
                value = session.get(name)
            except Exception as exc:
                record(f"read {name}", False, _err(exc))
                continue
            record(f"read {name}", math.isfinite(value), f"= {value:g}")

        try:
            value = session.bench.get(UNKNOWN_PATH)
            record("unknown path is rejected", False, f"read returned {value!r}")
        except Exception as exc:
            record("unknown path is rejected", True, type(exc).__name__)

        period = session.sample_period
        t0 = session.time()
        session.wait(2 * period)
        elapsed = session.time() - t0
        record("time advances on wait", elapsed > 0, f"{elapsed:.3f}s for {2 * period:.3f}s")

        _check_capture(session, names, period, record)

        for signal, target in (writes or {}).items():
            _check_write(session, signal, target, period, record)

        if session.debug is not None:
            _check_debugger(session, ecu_vars or [], ecu_writes or {}, record)
    finally:
        try:
            session.disconnect()
            record("disconnect", True)
        except Exception as exc:
            record("disconnect", False, _err(exc))
    return checks


def _check_capture(session: Session, names: list[str], period: float, record) -> None:
    try:
        session.start_capture(names)
        session.wait(3 * period)
        capture = session.stop_capture()
    except Exception as exc:
        record("capture", False, _err(exc))
        return
    n = len(capture.time)
    ordered = all(a <= b for a, b in zip(capture.time, capture.time[1:], strict=False))
    aligned = all(len(capture.values.get(s, [])) == n for s in names)
    ok = n >= 2 and ordered and aligned
    record("capture", ok, f"{n} samples, ordered={ordered}, aligned={aligned}")


def _check_write(session: Session, signal: str, target: float, period: float, record) -> None:
    name = f"write {signal} = {target:g} and read back"
    try:
        original = session.get(signal)
        session.set(signal, target)  # goes through the safety interlock
        session.wait(5 * period)
        readback = session.get(signal)
    except Exception as exc:
        record(name, False, _err(exc))
        return
    ok = math.isclose(readback, target, rel_tol=1e-6, abs_tol=1e-6)
    record(name, ok, f"read {readback:g}")
    try:
        session.set(signal, original)
        record(f"restore {signal} = {original:g}", True)
    except Exception as exc:
        record(f"restore {signal} = {original:g}", False, _err(exc))


def _check_debugger(session: Session, names: list[str], writes: dict[str, float], record) -> None:
    for name in names:
        try:
            value = session.read_var(name)
        except Exception as exc:
            record(f"read ecu_var {name}", False, _err(exc))
            continue
        record(f"read ecu_var {name}", math.isfinite(value), f"= {value:g}")

    try:
        value = session.read_var(UNKNOWN_PATH)
        record("unknown ecu_var is rejected", False, f"read returned {value!r}")
    except Exception as exc:
        record("unknown ecu_var is rejected", True, type(exc).__name__)

    for name, target in writes.items():
        label = f"write ecu_var {name} = {target:g} and read back"
        try:
            original = session.read_var(name)
            session.write_var(name, target)  # goes through the ECU interlock
            readback = session.read_var(name)
        except Exception as exc:
            record(label, False, _err(exc))
            continue
        ok = math.isclose(readback, target, rel_tol=1e-6, abs_tol=1e-6)
        record(label, ok, f"read {readback:g}")
        try:
            session.write_var(name, original)
            record(f"restore ecu_var {name} = {original:g}", True)
        except Exception as exc:
            record(f"restore ecu_var {name} = {original:g}", False, _err(exc))


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"
