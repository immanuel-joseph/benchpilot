"""Lauterbach TRACE32 debug adapter (experimental): ECU variables through the debugger.

Written against Lauterbach's public Remote API documentation and its official
Python module (`lauterbach-trace32-rcl`). It has not been validated on a
physical target: run `benchpilot check-bench` before trusting it.

TRACE32 PowerView must be running with the Remote API enabled in its config
file (config.t32), for example:

    RCL=NETTCP
    PORT=20000

Install with `pip install "benchpilot[trace32]"`.

bench.yaml:

    debug:
      adapter: trace32
      options:
        node: localhost        # PC running TRACE32 PowerView
        port: 20000            # RCL port from config.t32
        protocol: TCP
        timeout: 10            # seconds
        reset_commands: []     # PRACTICE commands for reset_target, e.g.
                               # ["SYStem.Up", "Go"]; empty = never reset

Variables are the ECU program's symbols, e.g. `cc_state` or `ctrl.mode`.
Writing while the ECU runs needs run-time memory access enabled in TRACE32.

Option `backend: fake` swaps the Remote API for an in-memory fake with the
same interface (see fake.py).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from benchpilot.core.errors import AdapterError, ConfigError
from benchpilot.core.interfaces import BenchAdapter, DebugAdapter

DEFAULTS: dict[str, Any] = {
    "node": "localhost",
    "port": 20000,
    "protocol": "TCP",
    "timeout": 10.0,
    "reset_commands": [],
    "backend": "rcl",
    "fake_variables": {},
}

Connect = Callable[..., Any]


class Trace32Debugger(DebugAdapter):
    def __init__(self, options: dict[str, Any], connect: Connect):
        self.options = options
        self._connect = connect
        self._dbg: Any = None

    def connect(self) -> None:
        try:
            self._dbg = self._connect(
                node=self.options["node"],
                port=int(self.options["port"]),
                protocol=self.options["protocol"],
                timeout=float(self.options["timeout"]),
            )
        except ConfigError:
            raise
        except Exception as exc:
            raise AdapterError(
                f"could not connect to TRACE32 at {self.options['node']}:{self.options['port']} "
                f"({_describe(exc)}). Is PowerView running with RCL=NETTCP and this PORT?"
            ) from None

    def disconnect(self) -> None:
        dbg, self._dbg = self._dbg, None
        if dbg is not None:
            try:
                dbg.disconnect()
            except Exception:  # best effort: we're leaving anyway
                pass

    def read_var(self, name: str) -> float:
        dbg = self._require()
        try:
            value = dbg.variable.read(name).value
        except Exception as exc:
            raise AdapterError(f"TRACE32 read {name!r} failed: {_describe(exc)}") from None
        try:
            return float(value)
        except (TypeError, ValueError):
            raise AdapterError(f"TRACE32 variable {name!r} is not numeric: {value!r}") from None

    def write_var(self, name: str, value: float) -> None:
        dbg = self._require()
        # Integer-typed symbols reject "2.0"; send whole numbers as integers.
        value = float(value)  # callers may pass ints; int.is_integer() needs Python 3.12
        payload: float | int = int(value) if math.isfinite(value) and value.is_integer() else value
        try:
            dbg.variable.write(name, payload)
        except Exception as exc:
            raise AdapterError(f"TRACE32 write {name!r} failed: {_describe(exc)}") from None

    def reset_target(self) -> None:
        commands = self.options["reset_commands"]
        if not commands:
            return  # resetting a real ECU between tests is opt-in
        dbg = self._require()
        for command in commands:
            try:
                dbg.cmd(command)
            except Exception as exc:
                raise AdapterError(
                    f"TRACE32 command {command!r} failed: {_describe(exc)}"
                ) from None

    def _require(self) -> Any:
        if self._dbg is None:
            raise AdapterError("TRACE32 is not connected")
        return self._dbg


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def connect_rcl(**kwargs: Any) -> Any:
    try:
        import lauterbach.trace32.rcl as t32  # type: ignore[import-not-found]
    except ImportError:
        raise ConfigError(
            'the trace32 adapter needs Lauterbach\'s module: pip install "benchpilot[trace32]"'
        ) from None
    return t32.connect(**kwargs)


def create_debugger(options: dict[str, Any], bench: BenchAdapter) -> Trace32Debugger:
    unknown = set(options) - set(DEFAULTS)
    if unknown:
        raise ConfigError(f"trace32 adapter: unknown options {', '.join(sorted(unknown))}")
    resolved = {**DEFAULTS, **options}
    commands = resolved["reset_commands"]
    if not isinstance(commands, list) or not all(isinstance(c, str) for c in commands):
        raise ConfigError("trace32 adapter option 'reset_commands' must be a list of strings")
    if resolved["backend"] == "fake":
        from benchpilot.adapters.trace32.fake import FakeTrace32

        variables = dict(resolved["fake_variables"])
        return Trace32Debugger(resolved, lambda **_kw: FakeTrace32(variables))
    if resolved["backend"] != "rcl":
        raise ConfigError("trace32 adapter option 'backend' must be 'rcl' or 'fake'")
    return Trace32Debugger(resolved, connect_rcl)
