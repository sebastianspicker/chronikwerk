"""Focused safeguards for admission, rate limiting, identifiers, and redaction."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr

from chronikwerk.adapters.pdf.url_fetcher import _SafeURLFetcher
from chronikwerk.app.admin import auth
from chronikwerk.app.jobs import ticket_storage
from chronikwerk.app.jobs.admission import AdmissionClosed, JobAdmission
from chronikwerk.app.middleware.rate_limit import _InMemoryTokenBucketLimiter
from chronikwerk.config.redact import REDACTED_VALUE, redact_settings_dict, scrub_secrets_in_text
from chronikwerk.domain.ticket_id import extract_ticket_id


def test_admission_is_bounded_and_closes_queued_work() -> None:
    async def exercise() -> None:
        admission = JobAdmission(max_pending=1, max_running=1)
        assert admission.try_reserve()
        assert admission.try_reserve()
        assert not admission.try_reserve()
        await admission.acquire()
        assert (admission.pending, admission.running) == (1, 1)
        await admission.close()
        await admission.release()
        try:
            await admission.acquire()
        except AdmissionClosed:
            pass
        else:  # pragma: no cover - the close boundary is the assertion
            raise AssertionError("closed admission accepted queued work")

    asyncio.run(exercise())


def test_rate_limiter_consumes_burst_and_refills_per_client() -> None:
    now = [0.0]
    limiter = _InMemoryTokenBucketLimiter(rps=2, burst=2, now=lambda: now[0])

    async def exercise() -> None:
        assert await limiter.allow("client-a")
        assert await limiter.allow("client-a")
        assert not await limiter.allow("client-a")
        assert await limiter.allow("client-b")
        now[0] = 0.5
        assert await limiter.allow("client-a")

    asyncio.run(exercise())


def test_ticket_ids_reject_boolean_zero_and_ambiguous_values() -> None:
    assert extract_ticket_id({"ticket_id": " 42 "}) == 42
    assert extract_ticket_id({"ticket": {"id": "+7"}}) == 7
    assert extract_ticket_id({"ticket_id": True, "ticket": {"id": 9}}) == 9
    assert extract_ticket_id({"ticket_id": 0}) is None
    assert extract_ticket_id({"ticket": "4.2"}) is None


def test_redaction_preserves_shape_without_disclosing_tokens() -> None:
    secret = "credential-that-must-not-leak"
    value = redact_settings_dict(
        {"api_token": secret, "nested": {"url": f"https://user:{secret}@example"}}
    )
    assert value["api_token"] == REDACTED_VALUE
    assert secret not in repr(value)
    assert secret not in scrub_secrets_in_text(f"Authorization: Bearer {secret}")


def test_admin_token_is_exact_and_sessions_expire_on_idle_or_absolute_time(monkeypatch) -> None:
    assert auth.access_token_matches("admin-token", SecretStr("admin-token"))
    assert not auth.access_token_matches("admin-token ", SecretStr("admin-token"))
    assert not auth.access_token_matches("admin-token", None)
    now = [100.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    idle = auth.AdminSessionStore(idle_seconds=5, absolute_seconds=30)
    idle_session = idle.create(locale="en")
    now[0] = 106.0
    assert idle.get(idle_session.session_id) is None
    absolute = auth.AdminSessionStore(idle_seconds=50, absolute_seconds=10)
    absolute_session = absolute.create(locale="en")
    now[0] = 109.0
    assert absolute.get(absolute_session.session_id) is not None
    now[0] = 117.0
    assert absolute.get(absolute_session.session_id) is None


def _transaction(
    tmp_path: Path, *, existing: bool
) -> tuple[ticket_storage._StorageTransaction, Path]:
    """Build a staged transaction with an optional prior canonical pair."""
    root = tmp_path / "archive"
    root.mkdir(parents=True)
    staged = root / ".staged"
    staged.mkdir()
    target, sidecar = root / "42.pdf", root / "42.json"
    if existing:
        target.write_bytes(b"old-pdf")
        sidecar.write_bytes(b'{"old":true}')
    (staged / target.name).write_bytes(b"new-pdf")
    (staged / sidecar.name).write_bytes(b'{"new":true}')
    transaction = ticket_storage._StorageTransaction(target, sidecar, "transaction", root, False)
    return transaction, staged


def test_failed_storage_commit_restores_prior_pair_and_leaves_no_first_write_pair(
    monkeypatch, tmp_path
) -> None:
    real_move = ticket_storage.move_file_within_root
    for existing in (True, False):
        transaction, staged = _transaction(tmp_path / str(existing), existing=existing)

        def fail_sidecar(source, destination, *, staged=staged, transaction=transaction, **kwargs):
            if source == staged / transaction.sidecar_path.name:
                raise OSError("sidecar commit failed")
            return real_move(source, destination, **kwargs)

        monkeypatch.setattr(ticket_storage, "move_file_within_root", fail_sidecar)
        with pytest.raises(OSError, match="sidecar commit failed"):
            ticket_storage._commit_files_to_storage(staged, transaction)
        if existing:
            assert transaction.target_path.read_bytes() == b"old-pdf"
            assert transaction.sidecar_path.read_bytes() == b'{"old":true}'
        else:
            assert not transaction.target_path.exists()
            assert not transaction.sidecar_path.exists()
        assert not list(transaction.storage_root.glob("*.bak.*"))


def test_storage_surfaces_incomplete_rollback_with_recovery_path(monkeypatch, tmp_path) -> None:
    transaction, staged = _transaction(tmp_path, existing=True)
    real_move = ticket_storage.move_file_within_root

    def fail_commit_and_pdf_restore(source, destination, **kwargs):
        if source == staged / transaction.sidecar_path.name:
            raise OSError("sidecar commit failed")
        if source.name.startswith(f"{transaction.target_path.name}.bak."):
            raise OSError("pdf restore failed")
        return real_move(source, destination, **kwargs)

    monkeypatch.setattr(ticket_storage, "move_file_within_root", fail_commit_and_pdf_restore)
    with pytest.raises(ticket_storage.StorageTransactionError) as raised:
        ticket_storage._commit_files_to_storage(staged, transaction)
    assert raised.value.primary_error.args == ("sidecar commit failed",)
    assert any(
        failure.operation == "rollback_restore" for failure in raised.value.rollback_failures
    )
    assert raised.value.recovery_paths == (transaction.pdf_backup,)


def test_pdf_asset_fetcher_confines_files_and_rejects_network_urls(tmp_path) -> None:
    from weasyprint.urls import FatalURLFetchingError

    root = tmp_path / "templates"
    root.mkdir()
    asset = root / "style.css"
    asset.write_bytes(b"body{}")
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret")
    (root / "escape.txt").symlink_to(outside)
    fetcher = _SafeURLFetcher(root)

    assert fetcher.fetch(asset.as_uri()).read() == b"body{}"
    assert fetcher.fetch("data:text/plain,safe").read() == b"safe"
    for url in (
        outside.as_uri(),
        (root / "escape.txt").as_uri(),
        root.as_uri(),
        "https://example.test/a",
    ):
        with pytest.raises(FatalURLFetchingError):
            fetcher.fetch(url)
