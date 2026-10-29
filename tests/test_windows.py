"""Behaviour that matters on Windows lab PCs, reproducible on any OS."""

import os
import subprocess
import sys

import pytest

from benchpilot.core.interfaces import BenchAdapter, Capture

from conftest import EXAMPLE_DIR, REPO_ROOT


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "cp437"])
def test_cli_survives_limited_console_encodings(encoding):
    # Windows consoles and redirected output often can't encode "·" or "±";
    # that must degrade the text, never crash a run mid-suite.
    env = {**os.environ, "PYTHONIOENCODING": encoding}
    proc = subprocess.run(
        [sys.executable, "-m", "benchpilot", "run", str(EXAMPLE_DIR)],
        capture_output=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode(encoding, "replace")
    assert b"5 passed, 0 failed" in proc.stdout


class _RealTimeBench(BenchAdapter):
    """Uses the default (wall-clock) time hooks, like a real bench adapter."""

    def connect(self): ...
    def set(self, signal, value): ...
    def get(self, signal):
        return 0.0

    def start_capture(self, signals): ...
    def stop_capture(self):
        return Capture()

    def disconnect(self): ...


def test_default_bench_clock_is_high_resolution(monkeypatch):
    # time.monotonic() ticks every ~15.6 ms on Windows; perf_counter() doesn't.
    import benchpilot.core.interfaces as interfaces

    monkeypatch.setattr(interfaces.time, "perf_counter", lambda: 123.456)
    assert _RealTimeBench().time() == 123.456


def test_scripts_and_hooks_keep_unix_line_endings():
    # Git for Windows converts to CRLF by default, which breaks "#!/bin/sh" scripts.
    attributes = (REPO_ROOT / ".gitattributes").read_text()
    assert "*.sh text eol=lf" in attributes
    assert ".githooks/* text eol=lf" in attributes
