"""bench.yaml loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from benchpilot.core.errors import ConfigError
from benchpilot.core.safety import Limit

BENCH_FILENAME = "bench.yaml"


class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    options: dict[str, Any] = Field(default_factory=dict)


class LimitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _ordered(self) -> LimitConfig:
        if self.min is None and self.max is None:
            raise ValueError("a limit needs at least one of 'min' or 'max'")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min ({self.min:g}) is greater than max ({self.max:g})")
        return self

    def to_limit(self) -> Limit:
        return Limit(min=self.min, max=self.max)


class BenchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "bench"
    bench: AdapterConfig
    debug: AdapterConfig | None = None
    signals: dict[str, str] = Field(default_factory=dict)
    limits: dict[str, LimitConfig] = Field(default_factory=dict)
    # Limits on ECU variable writes made through the debug adapter, keyed by variable name.
    ecu_limits: dict[str, LimitConfig] = Field(default_factory=dict)
    reset_between_tests: bool = True

    @model_validator(mode="after")
    def _limits_name_known_signals(self) -> BenchConfig:
        unknown = sorted(set(self.limits) - set(self.signals))
        if unknown:
            raise ValueError(f"limits set for signals not in the signal map: {', '.join(unknown)}")
        return self


def load_bench_config(path: str | Path) -> BenchConfig:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path}: file not found") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    try:
        return BenchConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{path}: {_format_errors(exc)}") from None


def find_bench_config(start: str | Path) -> Path | None:
    """Look for bench.yaml in `start` (or its folder, if it's a file) and each parent."""
    start = Path(start).resolve()
    folder = start if start.is_dir() else start.parent
    for candidate in (folder, *folder.parents):
        path = candidate / BENCH_FILENAME
        if path.is_file():
            return path
    return None


def _format_errors(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"]) or "<root>"
        parts.append(f"{where}: {err['msg']}")
    return "; ".join(parts)
