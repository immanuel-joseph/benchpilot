"""ASAM XIL bench adapter (experimental): drives a real HIL bench through the XIL API.

Written against the public ASAM XIL API and dSPACE's published XIL API
examples. It has not been validated on a physical bench: run
`benchpilot check-bench` on yours before trusting it.

The XIL API is a .NET library, reached from Python through pythonnet
(`pip install "benchpilot[xil]"`, Windows). Only the Model Access port
(MAPort) is used: Read and Write of model variables.

bench.yaml:

    bench:
      adapter: xil
      options:
        vendor: dSPACE GmbH              # passed to CreateVendorSpecificTestbench
        product: XIL API
        product_version: "2023-B"        # your installed release
        maport_config: C:/benches/rig1/MAPortConfig.xml
        start_simulation: false          # true: start the real-time app on connect
        sample_period: 0.05              # seconds between polled samples
        assembly_version: "2.1.0.0"      # XIL API .NET assembly version

Signal paths are MAPort variable names, e.g. "Model Root/Vehicle/Speed/Value".

Option `backend: fake` swaps the .NET testbench for an in-memory fake with the
same interface (see fake.py), so a bench.yaml can be tried without dSPACE.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from benchpilot.core.errors import AdapterError, ConfigError
from benchpilot.core.interfaces import BenchAdapter, Capture

DEFAULTS = {
    "vendor": "dSPACE GmbH",
    "product": "XIL API",
    "product_version": None,
    "maport_config": None,
    "start_simulation": False,
    "sample_period": 0.05,
    "assembly_version": "2.1.0.0",
    "port_name": "BenchPilotMAPort",
    "backend": "dotnet",
    "fake_variables": {},
}

TestbenchLoader = Callable[[dict[str, Any]], Any]


class XilBench(BenchAdapter):
    def __init__(self, options: dict[str, Any], load_testbench: TestbenchLoader):
        self.options = options
        self.sample_period = float(options["sample_period"])
        self._load_testbench = load_testbench
        self._testbench: Any = None
        self._port: Any = None
        self._capture: Capture | None = None
        self._write_kind: dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        self._testbench = self._load_testbench(self.options)
        try:
            port = self._testbench.MAPortFactory.CreateMAPort(self.options["port_name"])
            config = port.LoadConfiguration(self.options["maport_config"])
            port.Configure(config, False)
            if self.options["start_simulation"] and "SIMULATION_RUNNING" not in str(port.State):
                port.StartSimulation()
        except Exception as exc:
            raise AdapterError(f"XIL MAPort setup failed: {_describe(exc)}") from None
        self._port = port

    def disconnect(self) -> None:
        port, self._port = self._port, None
        if port is not None:
            try:
                port.Dispose()
            except Exception:  # best effort: we're leaving anyway
                pass

    # -- signals -----------------------------------------------------------

    def get(self, signal: str) -> float:
        value = self._read_raw(signal)
        try:
            return float(value)
        except (TypeError, ValueError):
            raise AdapterError(f"XIL variable {signal!r} is not numeric: {value!r}") from None

    def set(self, signal: str, value: float) -> None:
        port = self._require_port()
        xil_value = self._make_value(signal, value)
        try:
            port.Write(signal, xil_value)
        except Exception as exc:
            raise AdapterError(f"XIL write {signal!r} failed: {_describe(exc)}") from None
        if self._capture is not None:
            self._record()

    def _read_raw(self, signal: str) -> Any:
        port = self._require_port()
        try:
            return port.Read(signal).Value
        except Exception as exc:
            raise AdapterError(f"XIL read {signal!r} failed: {_describe(exc)}") from None

    def _make_value(self, signal: str, value: float) -> Any:
        """Build an XIL value object of the variable's own type.

        XIL is typed: writing a float to an integer or boolean variable fails.
        The type is learned from one read of the variable and then cached.
        """
        kind = self._write_kind.get(signal)
        if kind is None:
            current = self._read_raw(signal)
            if isinstance(current, bool):
                kind = "bool"
            elif isinstance(current, int):
                kind = "int"
            else:
                kind = "float"
            self._write_kind[signal] = kind

        factory = self._testbench.ValueFactory
        if kind == "float":
            return factory.CreateFloatValue(float(value))
        if not float(value).is_integer():
            raise AdapterError(f"XIL variable {signal!r} is {kind}; can't write {value:g}")
        if kind == "bool":
            if value not in (0, 1):
                raise AdapterError(f"XIL variable {signal!r} is boolean; can't write {value:g}")
            return factory.CreateBooleanValue(bool(value))
        return factory.CreateIntValue(int(value))

    # -- capture (polled) --------------------------------------------------

    def start_capture(self, signals: list[str]) -> None:
        self._capture = Capture(values={s: [] for s in signals})
        self._record()

    def stop_capture(self) -> Capture:
        capture, self._capture = self._capture, None
        if capture is None:
            raise AdapterError("stop_capture() called without start_capture()")
        return capture

    def wait(self, seconds: float) -> None:
        """Sleep in sample_period slices, sampling captured signals after each."""
        end = self.time() + seconds
        while True:
            remaining = end - self.time()
            if remaining <= 0:
                break
            time.sleep(min(self.sample_period, remaining))
            if self._capture is not None:
                self._record()

    def _record(self) -> None:
        assert self._capture is not None
        self._capture.time.append(self.time())
        for signal, series in self._capture.values.items():
            try:
                series.append(self.get(signal))
            except AdapterError:
                series.append(math.nan)  # a gap in the plot, not a crashed test

    def _require_port(self) -> Any:
        if self._port is None:
            raise AdapterError("XIL bench is not connected")
        return self._port


def _describe(exc: Exception) -> str:
    # .NET exceptions arrive through pythonnet; their Message is the useful part.
    message = getattr(exc, "Message", None) or str(exc) or type(exc).__name__
    return f"{type(exc).__name__}: {message}"


def load_dotnet_testbench(options: dict[str, Any]) -> Any:
    """Create the vendor's XIL testbench through pythonnet (Windows)."""
    try:
        import clr  # type: ignore[import-not-found]  # provided by pythonnet
    except ImportError:
        raise ConfigError(
            'the xil adapter needs pythonnet: pip install "benchpilot[xil]" (Windows)'
        ) from None
    version = options["assembly_version"]
    assembly = (
        f"ASAM.XIL.Implementation.TestbenchFactory, Version={version}, "
        "Culture=neutral, PublicKeyToken=fc9d65855b27d387"
    )
    try:
        clr.AddReference(assembly)
        from ASAM.XIL.Implementation.TestbenchFactory.Testbench import (  # type: ignore
            TestbenchFactory,
        )
    except Exception as exc:
        raise ConfigError(
            f"could not load the XIL API ({assembly}): {_describe(exc)}. Is the vendor's "
            "XIL API installed, and does 'assembly_version' match it?"
        ) from None
    try:
        return TestbenchFactory().CreateVendorSpecificTestbench(
            options["vendor"], options["product"], options["product_version"]
        )
    except Exception as exc:
        raise ConfigError(
            f"could not create the XIL testbench for {options['vendor']} {options['product']} "
            f"{options['product_version']}: {_describe(exc)}"
        ) from None


def create_bench(options: dict[str, Any]) -> XilBench:
    unknown = set(options) - set(DEFAULTS)
    if unknown:
        raise ConfigError(f"xil adapter: unknown options {', '.join(sorted(unknown))}")
    resolved = {**DEFAULTS, **options}
    if resolved["backend"] == "fake":
        from benchpilot.adapters.xil.fake import FakeTestbench

        variables = dict(resolved["fake_variables"])
        return XilBench(resolved, lambda _opts: FakeTestbench(variables))
    if resolved["backend"] != "dotnet":
        raise ConfigError("xil adapter option 'backend' must be 'dotnet' or 'fake'")
    for required in ("product_version", "maport_config"):
        if not resolved[required]:
            raise ConfigError(f"xil adapter needs option {required!r}")
    if float(resolved["sample_period"]) <= 0:
        raise ConfigError("xil adapter option 'sample_period' must be positive")
    return XilBench(resolved, load_dotnet_testbench)
