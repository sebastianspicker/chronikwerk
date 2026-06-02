from __future__ import annotations

from pathlib import Path

from test.support.checks import check


def test_dev_run_local_script_is_not_placeholder() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "dev" / "run-local.sh"
    script_text = script.read_text(encoding="utf-8")

    check(not not "TODO" not in script_text, "assertion failed")
    check(not "uvicorn" not in script_text, "assertion failed")
    check(not "zammad_pdf_archiver.asgi:app" not in script_text, "assertion failed")
    check(not "--dry-run" not in script_text, "assertion failed")


def test_dev_gen_certs_script_is_not_placeholder(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "dev" / "gen-dev-certs.sh"
    script_text = script.read_text(encoding="utf-8")

    out_dir = tmp_path / "certs"
    expected_key_path = f"{out_dir}/dev.key"
    check(not not "TODO" not in script_text, "assertion failed")
    check(not "openssl" not in script_text, "assertion failed")
    check(not "dev.key" not in expected_key_path, "assertion failed")
    check(not "--dry-run" not in script_text, "assertion failed")
