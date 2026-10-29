"""In-memory stand-in for a TRACE32 Remote API connection.

Mirrors the calls Trace32Debugger makes on the object returned by
`lauterbach.trace32.rcl.connect(...)`:

    dbg.variable.read(name).value
    dbg.variable.write(name, value)
    dbg.cmd(command)
    dbg.disconnect()

Like a real target, it's typed: writing a fractional value to an integer
symbol fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CommandError(Exception):
    """Same idea as the Remote API's command errors."""


@dataclass
class _Read:
    value: Any


class _Variables:
    def __init__(self, owner: FakeTrace32):
        self._owner = owner

    def read(self, name: str) -> _Read:
        self._owner._check(name)
        return _Read(self._owner.variables[name])

    def write(self, name: str, value: Any) -> None:
        self._owner._check(name)
        current = self._owner.variables[name]
        if isinstance(current, int) and not isinstance(value, int):
            raise CommandError(f"symbol {name} is an integer; can't write {value!r}")
        self._owner.variables[name] = value
        self._owner.writes.append((name, value))


class FakeTrace32:
    def __init__(self, variables: dict[str, Any] | None = None):
        self.variables = dict(variables or {})
        self.writes: list[tuple[str, Any]] = []
        self.commands: list[str] = []
        self.connected = True
        self.variable = _Variables(self)

    def cmd(self, command: str) -> None:
        self._alive()
        self.commands.append(command)

    def disconnect(self) -> None:
        self.connected = False

    def _check(self, name: str) -> None:
        self._alive()
        if name not in self.variables:
            raise CommandError(f"symbol not found: {name}")

    def _alive(self) -> None:
        if not self.connected:
            raise CommandError("not connected")
