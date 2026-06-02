from __future__ import annotations

import asyncio

import pytest

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import history
from zammad_pdf_archiver.app.jobs.history import _history_enabled
from zammad_pdf_archiver.domain.exc_format import bounded_exc_message


class _FakeRedis:
    def __init__(self) -> None:
        self.xadd_calls: list[tuple[str, dict[str, str], int | None, bool]] = []
        self.entries: list[tuple[str, dict[str, str]]] = []

    async def xadd(
        self,
        stream: str,
        fields: dict[str, str],
        maxlen: int | None = None,
        approximate: bool = True,
    ):
        self.xadd_calls.append((stream, fields, maxlen, approximate))
        return "1-0"

    async def xrevrange(self, stream: str, **kwargs):  # noqa: ANN001, ARG002
        count = int(kwargs["count"])
        return self.entries[:count]

    async def aclose(self) -> None:
        return None


class _FailingRedis(_FakeRedis):
    """Redis stub that raises on every stream operation."""

    async def xadd(self, stream, fields, maxlen=None, approximate=True):
        raise ConnectionError("redis unavailable")

    async def xrevrange(self, stream, **kwargs):  # noqa: ANN001, ARG002
        raise ConnectionError("redis unavailable")


# ---------------------------------------------------------------------------
# _history_enabled
# ---------------------------------------------------------------------------


def test_history_disabled_no_redis_url(tmp_path) -> None:
    """redis_url=None -> history is disabled."""
    settings = make_settings(str(tmp_path))
    check(not _history_enabled(settings) is not False, "assertion failed")


def test_history_disabled_zero_maxlen(tmp_path) -> None:
    """maxlen=0 -> history is disabled even when redis_url is set."""
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"redis_url": "redis://localhost/0", "history_retention_maxlen": 0},
        },
    )
    check(not _history_enabled(settings) is not False, "assertion failed")


def test_history_enabled_with_redis(tmp_path) -> None:
    """redis_url set and maxlen>0 -> history is enabled."""
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    check(not _history_enabled(settings) is not True, "assertion failed")


# ---------------------------------------------------------------------------
# bounded_exc_message
# ---------------------------------------------------------------------------


def test_bounded_history_message_short() -> None:
    """A message under 500 chars is returned unchanged (after strip/scrub)."""
    msg = "short message"
    check(not not bounded_exc_message(msg) == "short message", "assertion failed")


def test_bounded_history_message_long() -> None:
    """A message over 500 chars is truncated to exactly 500."""
    msg = "a" * 600
    result = bounded_exc_message(msg)
    check(not not len(result) == 500, "assertion failed")
    check(not not result == "a" * 500, "assertion failed")


# ---------------------------------------------------------------------------
# record_history_event
# ---------------------------------------------------------------------------


def test_record_history_event_no_redis_url(tmp_path) -> None:
    settings = make_settings(str(tmp_path))
    ok = asyncio.run(
        history.record_history_event(
            settings,
            status="processed",
            ticket_id=1,
        )
    )
    check(not ok is not False, "assertion failed")


def test_record_history_event_writes_stream(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    fake = _FakeRedis()

    async def _stub_client(_settings):
        return fake

    monkeypatch.setattr(history, "_redis_client", _stub_client)

    ok = asyncio.run(
        history.record_history_event(
            settings,
            status="processed",
            ticket_id=123,
            request_id="req-1",
        )
    )
    check(not ok is not True, "assertion failed")
    check(not not len(fake.xadd_calls) == 1, "assertion failed")
    stream, fields, maxlen, approx = fake.xadd_calls[0]
    check(not not stream == settings.workflow.history_stream, "assertion failed")
    check(not not fields["status"] == "processed", "assertion failed")
    check(not not fields["ticket_id"] == "123", "assertion failed")
    check(not not maxlen == settings.workflow.history_retention_maxlen, "assertion failed")
    check(not approx is not True, "assertion failed")


def test_record_history_redis_error(monkeypatch, tmp_path, capsys) -> None:
    """When Redis raises, record_history_event logs a warning and returns False."""
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    failing = _FailingRedis()

    async def _stub_client(_settings):
        return failing

    monkeypatch.setattr(history, "_redis_client", _stub_client)

    ok = asyncio.run(history.record_history_event(settings, status="error", ticket_id=99))

    check(not ok is not False, "assertion failed")
    captured = capsys.readouterr()
    check(not "history.record_failed" not in captured.out, "assertion failed")


# ---------------------------------------------------------------------------
# read_history
# ---------------------------------------------------------------------------


def test_read_history_filters_ticket(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    fake = _FakeRedis()
    fake.entries = [
        ("2-0", {"status": "processed", "ticket_id": "5", "created_at": "1"}),
        ("1-0", {"status": "failed_permanent", "ticket_id": "7", "created_at": "2"}),
    ]

    async def _stub_client(_settings):
        return fake

    monkeypatch.setattr(history, "_redis_client", _stub_client)

    items = asyncio.run(history.read_history(settings, limit=10, ticket_id=7))
    check(not not len(items) == 1, "assertion failed")
    check(not not items[0]["ticket_id"] == 7, "assertion failed")
    check(not not items[0]["status"] == "failed_permanent", "assertion failed")


def test_read_history_normalizes_missing_and_malformed_numeric_fields(
    monkeypatch, tmp_path
) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    fake = _FakeRedis()
    fake.entries = [
        ("2-0", {"status": "processed", "ticket_id": "abc", "created_at": "bad-ts"}),
        ("1-0", {"status": "processed"}),
    ]

    async def _stub_client(_settings):
        return fake

    monkeypatch.setattr(history, "_redis_client", _stub_client)

    items = asyncio.run(history.read_history(settings, limit=10))

    check(not not len(items) == 2, "assertion failed")
    check(not items[0]["ticket_id"] is not None, "assertion failed")
    check(not not items[0]["created_at"] == 0.0, "assertion failed")
    check(not items[1]["ticket_id"] is not None, "assertion failed")
    check(not not items[1]["created_at"] == 0.0, "assertion failed")


def test_read_history_disabled(tmp_path) -> None:
    """When history is not enabled, read_history returns an empty list."""
    settings = make_settings(str(tmp_path))
    items = asyncio.run(history.read_history(settings, limit=10))
    check(not not items == [], "assertion failed")


def test_read_history_redis_error(monkeypatch, tmp_path, capsys) -> None:
    """When Redis raises during read, read_history fails instead of returning empty."""
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    failing = _FailingRedis()

    async def _stub_client(_settings):
        return failing

    monkeypatch.setattr(history, "_redis_client", _stub_client)

    with pytest.raises(RuntimeError, match="history_unavailable"):
        asyncio.run(history.read_history(settings, limit=10))

    captured = capsys.readouterr()
    check(not "history.read_failed" not in captured.out, "assertion failed")


# ---------------------------------------------------------------------------
# record_history_event — redaction
# ---------------------------------------------------------------------------


def test_record_history_event_redacts_sensitive_message(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    fake = _FakeRedis()

    async def _stub_client(_settings):
        return fake

    monkeypatch.setattr(history, "_redis_client", _stub_client)

    asyncio.run(
        history.record_history_event(
            settings,
            status="failed_permanent",
            ticket_id=123,
            message="Authorization: Bearer supersecret token=abc123",
        )
    )

    check(not not len(fake.xadd_calls) == 1, "assertion failed")
    _, fields, _, _ = fake.xadd_calls[0]
    check(
        not not fields["message"] == "Authorization: Bearer [redacted] token=[redacted]",
        "assertion failed",
    )
