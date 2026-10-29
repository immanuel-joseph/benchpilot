import shutil

from benchpilot.cli import EXIT_OK, EXIT_TESTS_FAILED, EXIT_USAGE, main

from conftest import EXAMPLE_DIR


def test_examples_pass(capsys):
    assert main(["run", str(EXAMPLE_DIR)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "5 passed, 0 failed, 0 error(s)" in out


def test_tag_filter(capsys):
    assert main(["run", str(EXAMPLE_DIR), "--tag", "safety"]) == EXIT_OK
    assert "1 passed" in capsys.readouterr().out


def test_failing_test_sets_exit_code(tmp_path, capsys):
    shutil.copy(EXAMPLE_DIR / "bench.yaml", tmp_path)
    (tmp_path / "t.yaml").write_text(
        "id: X-1\ntitle: speed is not 50\nsteps:\n"
        "  - expect: {signal: vehicle.speed_kph, equals: 50}\n"
    )
    assert main(["run", str(tmp_path)]) == EXIT_TESTS_FAILED
    out = capsys.readouterr().out
    assert "FAIL" in out and "vehicle.speed_kph = 0, expected 50" in out


def test_validate(tmp_path, capsys):
    (tmp_path / "good.yaml").write_text("id: G\ntitle: g\nsteps: [{wait: 1s}]\n")
    (tmp_path / "typo.yaml").write_text(
        "id: B\ntitle: b\nsteps: [{set: {signal: vehicle.sped_kph, value: 1}}]\n"
    )
    bench = EXAMPLE_DIR / "bench.yaml"
    assert main(["validate", str(tmp_path), "--bench", str(bench)]) == EXIT_TESTS_FAILED
    out = capsys.readouterr().out
    assert "OK       " in out and "good.yaml" in out
    assert "INVALID" in out and "unknown signal 'vehicle.sped_kph'" in out


def test_duplicate_ids_and_missing_bench(tmp_path, capsys):
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text("id: SAME\ntitle: t\nsteps: [{wait: 1s}]\n")
    assert main(["run", str(tmp_path)]) == EXIT_USAGE
    assert "duplicate test id 'SAME'" in capsys.readouterr().err

    (tmp_path / "b.yaml").unlink()
    assert main(["run", str(tmp_path)]) == EXIT_USAGE
    assert "no bench.yaml found" in capsys.readouterr().err
