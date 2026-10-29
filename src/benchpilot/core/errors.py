"""Exception hierarchy shared by every layer."""


class BenchPilotError(Exception):
    """Base class for all framework errors."""


class ConfigError(BenchPilotError):
    """bench.yaml is missing, malformed, or names an adapter that can't be loaded."""


class TestCaseError(BenchPilotError):
    """A YAML test case failed schema validation."""

    __test__ = False  # not a pytest test class


class UnknownSignal(BenchPilotError):
    """A logical signal name has no entry in the bench's signal map."""


class SafetyViolation(BenchPilotError):
    """A value was blocked by a safety interlock before reaching the bench."""


class AdapterError(BenchPilotError):
    """An adapter rejected an operation (unknown path, read-only signal, not connected...)."""
