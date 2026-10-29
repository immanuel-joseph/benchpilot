"""Command line: `benchpilot run | validate`."""

from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime
from pathlib import Path

from benchpilot import __version__
from benchpilot.core.config import BenchConfig, find_bench_config, load_bench_config
from benchpilot.core.contract import Check, run_contract
from benchpilot.core.errors import BenchPilotError, TestCaseError
from benchpilot.core.session import Session
from benchpilot.reporting import write_html, write_junit
from benchpilot.testcases.executor import (
    Outcome,
    TestInterrupted,
    TestResult,
    preflight,
    run_suite,
)
from benchpilot.testcases.plan import plan_test
from benchpilot.testcases.schema import TestCase, collect_testcase_files, load_testcase

EXIT_OK, EXIT_TESTS_FAILED, EXIT_USAGE = 0, 1, 2
EXIT_INTERRUPTED = 130  # shell convention: 128 + SIGINT

_LABEL = {Outcome.PASSED: "PASS", Outcome.FAILED: "FAIL", Outcome.ERROR: "ERROR"}


def main(argv: list[str] | None = None) -> int:
    _tolerate_console_encoding()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BenchPilotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


def _tolerate_console_encoding() -> None:
    """Replace characters the console can't encode instead of crashing.

    Windows consoles and redirected output may use a code page without "·" or
    "±"; an encoding error mid-suite would abort the run on a live bench.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchpilot", description="Vendor-agnostic hardware-in-the-loop testing."
    )
    parser.add_argument("--version", action="version", version=f"benchpilot {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run YAML test cases on a bench")
    run.add_argument("paths", nargs="+", type=Path, help="test files or folders")
    run.add_argument("--bench", type=Path, help="bench.yaml (default: nearest one above the tests)")
    run.add_argument("--tag", action="append", default=[], help="only run tests with this tag")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="show what each test would do, with bench paths and limit checks; touch nothing",
    )
    run.add_argument("--html", type=Path, metavar="PATH", help="write an HTML report with plots")
    run.add_argument("--junit", type=Path, metavar="PATH", help="write JUnit XML results")
    run.set_defaults(func=_cmd_run)

    validate = sub.add_parser("validate", help="check YAML test cases without running them")
    validate.add_argument("paths", nargs="+", type=Path, help="test files or folders")
    validate.add_argument(
        "--bench", type=Path, help="also check signal names against this bench.yaml"
    )
    validate.set_defaults(func=_cmd_validate)

    check = sub.add_parser(
        "check-bench",
        help="check that a bench works with BenchPilot (read-only unless --write is given)",
    )
    check.add_argument("--bench", type=Path, help="bench.yaml (default: nearest one above .)")
    check.add_argument(
        "--write",
        action="append",
        default=[],
        metavar="SIGNAL=VALUE",
        help="also write VALUE to SIGNAL, read it back, then restore it (repeatable)",
    )
    check.add_argument(
        "--read-var",
        action="append",
        default=[],
        metavar="NAME",
        help="ECU variable to read through the debug adapter (default: those in ecu_limits)",
    )
    check.add_argument(
        "--write-var",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="also write VALUE to ECU variable NAME, read it back, then restore it",
    )
    check.set_defaults(func=_cmd_check_bench)
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    loaded, errors = _load_all(args.paths)
    for message in errors:
        print(f"error: {message}", file=sys.stderr)
    if errors:
        return EXIT_USAGE
    if args.tag:
        wanted = set(args.tag)
        loaded = [(p, tc) for p, tc in loaded if wanted & set(tc.tags)]
    if not loaded:
        print("no test cases found", file=sys.stderr)
        return EXIT_USAGE

    bench_path = args.bench or find_bench_config(args.paths[0])
    if bench_path is None:
        print(f"error: no bench.yaml found above {args.paths[0]}; pass --bench", file=sys.stderr)
        return EXIT_USAGE
    config = load_bench_config(bench_path)
    if args.dry_run:
        if args.html or args.junit:
            print("error: --html/--junit need a real run, not --dry-run", file=sys.stderr)
            return EXIT_USAGE
        return _dry_run(config, loaded)

    print(
        f"benchpilot {__version__} · bench {config.name!r} ({config.bench.adapter}) · "
        f"{len(loaded)} test(s)"
    )
    results: list[TestResult] = []
    started_at = datetime.now()

    def on_result(result: TestResult) -> None:
        results.append(result)
        _print_result(result)

    try:
        with Session.from_config(config) as session:
            run_suite(
                session,
                loaded,
                reset_between_tests=config.reset_between_tests,
                on_result=on_result,
            )
    except KeyboardInterrupt as exc:
        _print_summary(results, time.perf_counter() - started)
        _write_reports(args, config, results, started_at)
        if isinstance(exc, TestInterrupted):
            print("interrupted; teardown ran", file=sys.stderr)
        else:
            print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    _print_summary(results, time.perf_counter() - started)
    _write_reports(args, config, results, started_at)
    return EXIT_OK if all(r.passed for r in results) else EXIT_TESTS_FAILED


def _write_reports(
    args: argparse.Namespace, config: BenchConfig, results: list[TestResult], started_at: datetime
) -> None:
    """Write whatever reports were asked for, even for a partial (interrupted) run."""
    if args.html:
        path = write_html(results, args.html, config.name, config.bench.adapter, started_at)
        print(f"HTML report: {path}")
    if args.junit:
        path = write_junit(results, args.junit, config.name, started_at)
        print(f"JUnit XML:   {path}")


def _dry_run(config: BenchConfig, loaded: list[tuple[Path, TestCase]]) -> int:
    print(
        f"benchpilot {__version__} · DRY RUN on bench {config.name!r} ({config.bench.adapter}) · "
        f"{len(loaded)} test(s) · nothing is sent to the bench"
    )
    problems = 0
    total = 0.0
    for _, tc in loaded:
        plan = plan_test(tc, config)
        problems += len(plan.problems)
        total += plan.max_bench_time_s
        print(f"\n{tc.id}  {tc.title}  (up to {plan.max_bench_time_s:g}s of bench time)")
        for line in plan.lines:
            print(f"  {line}")
        for problem in plan.problems:
            print(f"  PROBLEM  {problem}")
    print(
        f"\n{len(loaded)} test(s) planned, {problems} problem(s) · up to {total:g}s of bench time"
    )
    return EXIT_OK if problems == 0 else EXIT_TESTS_FAILED


def _cmd_validate(args: argparse.Namespace) -> int:
    loaded, errors = _load_all(args.paths)
    for message in errors:
        print(f"INVALID  {message}")

    invalid = len(errors)
    # Checks names against the bench's configuration only. No adapter is created,
    # so validating never opens a connection to hardware.
    config = load_bench_config(args.bench) if args.bench is not None else None
    for path, tc in loaded:
        problems = []
        if config is not None:
            problems = preflight(tc, config.signals, has_debug=config.debug is not None)
        for problem in problems:
            print(f"INVALID  {path}: {problem}")
        if problems:
            invalid += 1
        else:
            print(f"OK       {path}  ({tc.id})")
    print(f"{len(loaded) + len(errors)} file(s), {invalid} invalid")
    return EXIT_OK if invalid == 0 else EXIT_TESTS_FAILED


def _cmd_check_bench(args: argparse.Namespace) -> int:
    writes = _parse_assignments(args.write, "--write", "SIGNAL")
    var_writes = _parse_assignments(args.write_var, "--write-var", "NAME")
    if writes is None or var_writes is None:
        return EXIT_USAGE

    bench_path = args.bench or find_bench_config(Path.cwd())
    if bench_path is None:
        print("error: no bench.yaml found; pass --bench", file=sys.stderr)
        return EXIT_USAGE
    config = load_bench_config(bench_path)
    unknown = [s for s in writes if s not in config.signals]
    if unknown:
        print(f"error: --write names unknown signal(s): {', '.join(unknown)}", file=sys.stderr)
        return EXIT_USAGE

    if (args.read_var or var_writes) and config.debug is None:
        print("error: --read-var/--write-var need a 'debug' adapter in bench.yaml", file=sys.stderr)
        return EXIT_USAGE
    ecu_vars = list(dict.fromkeys([*args.read_var, *config.ecu_limits]))
    n_writes = len(writes) + len(var_writes)
    mode = f"writes {n_writes} value(s), then restores them" if n_writes else "read-only"
    print(
        f"benchpilot {__version__} · checking bench {config.name!r} "
        f"({config.bench.adapter}) · {mode}"
    )
    checks = run_contract(
        Session.from_config(config),
        writes=writes,
        on_check=_print_check,
        ecu_vars=ecu_vars,
        ecu_writes=var_writes,
    )
    failed = sum(not c.ok for c in checks)
    print(f"\n{len(checks) - failed} passed, {failed} failed")
    return EXIT_OK if failed == 0 else EXIT_TESTS_FAILED


def _parse_assignments(items: list[str], flag: str, what: str) -> dict[str, float] | None:
    out: dict[str, float] = {}
    for item in items:
        name, sep, raw = item.partition("=")
        try:
            value = float(raw)
        except ValueError:
            value = math.nan
        if not sep or not name or not math.isfinite(value):
            print(f"error: {flag} expects {what}=NUMBER, got {item!r}", file=sys.stderr)
            return None
        out[name] = value
    return out


def _print_check(check: Check) -> None:
    label = "PASS " if check.ok else "FAIL "
    detail = f"  {check.detail}" if check.detail else ""
    print(f"{label}  {check.name}{detail}")


def _load_all(paths: list[Path]) -> tuple[list[tuple[Path, TestCase]], list[str]]:
    loaded, errors = [], []
    ids: dict[str, Path] = {}
    for path in collect_testcase_files(paths):
        try:
            tc = load_testcase(path)
        except TestCaseError as exc:
            errors.append(str(exc))
            continue
        if tc.id in ids:
            errors.append(f"{path}: duplicate test id {tc.id!r} (also in {ids[tc.id]})")
            continue
        ids[tc.id] = path
        loaded.append((path, tc))
    return loaded, errors


def _print_result(r: TestResult) -> None:
    tc = r.testcase
    print(
        f"{_LABEL[r.outcome]:<5}  {tc.id:<8} {tc.title}  "
        f"({r.bench_time_s:.1f}s bench, {r.wall_time_s:.2f}s wall)"
    )
    if not r.passed:
        where = f"{r.location}: " if r.location else ""
        print(f"         {where}{r.message}")


def _print_summary(results: list[TestResult], elapsed: float) -> None:
    counts = {o: sum(r.outcome is o for r in results) for o in Outcome}
    print(
        f"\n{counts[Outcome.PASSED]} passed, {counts[Outcome.FAILED]} failed, "
        f"{counts[Outcome.ERROR]} error(s) in {elapsed:.2f}s"
    )
