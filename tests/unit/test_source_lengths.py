"""Verifies the deterministic maintained-source physical-line gate."""

from __future__ import annotations

from pathlib import Path

from scripts.ci import check_source_lengths


def _write_lines(path: Path, line_count: int) -> None:
    """Write a deterministic source fixture with the requested physical lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("line\n" * line_count, encoding="utf-8")


def test_source_length_limit_accepts_600_lines_and_rejects_601(tmp_path: Path, capsys) -> None:
    _write_lines(tmp_path / "src/chronikwerk/accepted.py", 600)
    _write_lines(tmp_path / "tests/rejected.py", 601)

    assert check_source_lengths.main(tmp_path) == 1
    captured = capsys.readouterr()

    assert "accepted.py" not in captured.err
    assert "tests/rejected.py: 601 lines (maximum 600)" in captured.err


def test_source_length_gate_scans_every_maintained_root_and_root_source(
    tmp_path: Path,
) -> None:
    expected = {
        "frontend/sample.css",
        "infra/e2e/sample.py",
        "playwright.config.ts",
        "scripts/sample.sh",
        "src/chronikwerk/sample.html",
        "tests/sample.mjs",
    }
    for relative_path in expected:
        _write_lines(tmp_path / relative_path, 1)

    discovered = {
        path.relative_to(tmp_path).as_posix()
        for path in check_source_lengths.maintained_source_paths(tmp_path)
    }

    assert discovered == expected


def test_source_length_gate_exempts_only_generated_admin_bundles(tmp_path: Path) -> None:
    _write_lines(tmp_path / "src/chronikwerk/static/admin/admin.css", 601)
    _write_lines(tmp_path / "src/chronikwerk/static/admin/admin.js", 601)
    _write_lines(tmp_path / "frontend/admin/admin.js", 601)

    offenders, failures, scanned_count = check_source_lengths.scan_source_lengths(tmp_path)

    assert offenders == [("frontend/admin/admin.js", 601)]
    assert failures == []
    assert scanned_count == 1


def test_source_length_diagnostics_are_sorted_by_repository_path(tmp_path: Path, capsys) -> None:
    _write_lines(tmp_path / "tests/z-last.py", 601)
    _write_lines(tmp_path / "scripts/a-first.py", 602)

    assert check_source_lengths.main(tmp_path) == 1
    diagnostics = capsys.readouterr().err.splitlines()

    assert diagnostics[0].startswith("source-length-check: scripts/a-first.py:")
    assert diagnostics[1].startswith("source-length-check: tests/z-last.py:")


def test_source_length_gate_fails_closed_on_unreadable_source(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    unreadable = tmp_path / "src/chronikwerk/unreadable.py"
    _write_lines(unreadable, 1)
    original_read_bytes = Path.read_bytes

    def fail_selected_read(path: Path) -> bytes:
        if path == unreadable:
            raise PermissionError("permission denied")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_selected_read)

    assert check_source_lengths.main(tmp_path) == 1
    assert (
        "source-length-check: src/chronikwerk/unreadable.py: unreadable source: "
        "permission denied" in capsys.readouterr().err
    )
