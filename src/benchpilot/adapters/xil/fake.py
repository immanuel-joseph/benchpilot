"""In-memory stand-in for an XIL testbench, with the object shapes the adapter uses.

Mirrors the parts of the ASAM XIL API that XilBench touches:

    testbench.MAPortFactory.CreateMAPort(name) -> port
    port.LoadConfiguration(path) / port.Configure(config, force)
    port.State / port.StartSimulation() / port.Dispose()
    port.Read(name).Value / port.Write(name, value)
    testbench.ValueFactory.CreateFloatValue / CreateIntValue / CreateBooleanValue

Like the real API it is typed: writing a float value to an integer variable
fails. Variables keep their Python type (float, int or bool) from
`fake_variables`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class TestbenchPortException(Exception):
    """Same name as the XIL exception, so error handling is exercised realistically."""

    __test__ = False  # not a pytest test class

    def __init__(self, message: str):
        super().__init__(message)
        self.Message = message


@dataclass
class _Value:
    Value: Any


class _ValueFactory:
    def CreateFloatValue(self, value: float) -> _Value:  # noqa: N802 - XIL naming
        return _Value(float(value))

    def CreateIntValue(self, value: int) -> _Value:  # noqa: N802
        return _Value(int(value))

    def CreateBooleanValue(self, value: bool) -> _Value:  # noqa: N802
        return _Value(bool(value))


class FakeMAPort:
    def __init__(self, variables: dict[str, Any]):
        self.variables = variables
        self.State = "eDISCONNECTED"
        self.configured_with: Any = None
        self.disposed = False
        self.writes: list[tuple[str, Any]] = []

    def LoadConfiguration(self, path: str) -> dict:  # noqa: N802
        return {"path": path}

    def Configure(self, config: Any, force: bool) -> None:  # noqa: N802
        self.configured_with = config
        self.State = "eSIMULATION_STOPPED"

    def StartSimulation(self) -> None:  # noqa: N802
        self.State = "eSIMULATION_RUNNING"

    def StopSimulation(self) -> None:  # noqa: N802
        self.State = "eSIMULATION_STOPPED"

    def Read(self, name: str) -> _Value:  # noqa: N802
        self._check(name)
        return _Value(self.variables[name])

    def Write(self, name: str, value: _Value) -> None:  # noqa: N802
        self._check(name)
        current = self.variables[name]
        if type(value.Value) is not type(current):
            raise TestbenchPortException(
                f"type mismatch writing {name}: variable is {type(current).__name__}, "
                f"value is {type(value.Value).__name__}"
            )
        self.variables[name] = value.Value
        self.writes.append((name, value.Value))

    def Dispose(self) -> None:  # noqa: N802
        self.disposed = True
        self.State = "eDISCONNECTED"

    def _check(self, name: str) -> None:
        if self.disposed:
            raise TestbenchPortException("port is disposed")
        if name not in self.variables:
            raise TestbenchPortException(f"variable '{name}' does not exist")


class _MAPortFactory:
    def __init__(self, testbench: FakeTestbench):
        self._testbench = testbench

    def CreateMAPort(self, name: str) -> FakeMAPort:  # noqa: N802
        port = FakeMAPort(self._testbench.variables)
        self._testbench.ports.append(port)
        return port


class FakeTestbench:
    __test__ = False  # not a pytest test class

    def __init__(self, variables: dict[str, Any] | None = None):
        self.variables = dict(variables or {})
        self.ports: list[FakeMAPort] = []
        self.MAPortFactory = _MAPortFactory(self)
        self.ValueFactory = _ValueFactory()
