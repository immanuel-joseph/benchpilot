"""Adapter discovery through Python entry points.

Another installed package can add adapters by registering them in its own
pyproject.toml:

    [project.entry-points."benchpilot.bench_adapters"]
    mybench = "my_package.adapter:create_bench"

    [project.entry-points."benchpilot.debug_adapters"]
    trace32 = "benchpilot_hil.trace32:create_debugger"

Factories have these signatures:

    create_bench(options: dict) -> BenchAdapter
    create_debugger(options: dict, bench: BenchAdapter) -> DebugAdapter
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

from benchpilot.core.errors import ConfigError
from benchpilot.core.interfaces import BenchAdapter, DebugAdapter

BENCH_GROUP = "benchpilot.bench_adapters"
DEBUG_GROUP = "benchpilot.debug_adapters"

BenchFactory = Callable[[dict[str, Any]], BenchAdapter]
DebugFactory = Callable[[dict[str, Any], BenchAdapter], DebugAdapter]

# Built-ins resolve even when the package isn't installed (e.g. running from a checkout).
_BUILTIN = {
    BENCH_GROUP: {
        "sim": "benchpilot.adapters.sim:create_bench",
        "xil": "benchpilot.adapters.xil:create_bench",
    },
    DEBUG_GROUP: {
        "sim": "benchpilot.adapters.sim:create_debugger",
        "trace32": "benchpilot.adapters.trace32:create_debugger",
    },
}


def available(group: str) -> list[str]:
    names = set(_BUILTIN.get(group, {}))
    names.update(ep.name for ep in entry_points(group=group))
    return sorted(names)


def load_bench_factory(name: str) -> BenchFactory:
    return _load(BENCH_GROUP, name)


def load_debug_factory(name: str) -> DebugFactory:
    return _load(DEBUG_GROUP, name)


def _load(group: str, name: str) -> Any:
    for ep in entry_points(group=group):
        if ep.name == name:
            try:
                return ep.load()
            except ImportError as exc:
                raise ConfigError(
                    f"adapter {name!r} is installed but failed to import: {exc}. "
                    "Is the vendor library it wraps installed?"
                ) from None
    target = _BUILTIN.get(group, {}).get(name)
    if target is None:
        kind = "bench" if group == BENCH_GROUP else "debug"
        raise ConfigError(
            f"no {kind} adapter named {name!r}; available: {', '.join(available(group)) or 'none'}"
        )
    module, _, attr = target.partition(":")
    return getattr(importlib.import_module(module), attr)
