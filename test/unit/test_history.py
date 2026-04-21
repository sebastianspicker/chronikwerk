from __future__ import annotations

import asyncio
import json

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import history
from zammad_pdf_archiver.app.jobs.history import (
    _bounded_message,
    _history_enabled,
    _to_float,
    _to_int,
)


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

    async def xrevrange(self, stream: str, max: str, min: str, count: int):  # noqa: A002
        return self.entries[:count]

    async def aclose(self) -> None:
        return None


class _FailingRedis(_FakeRedis):
    """Redis stub that raises on every stream operation."""

    async def xadd(self, stream, fields, maxlen=None, approximate=True):
        raise ConnectionError("redis unavailable")

    async def xrevrange(self, stream, max, min, count):  # noqa: A002
        raise ConnectionError("redis unavailable")


# ---------------------------------------------------------------------------
# _history_enabled
# ---------------------------------------------------------------------------


def test_history_disabled_no_redis_url(tmp_path) -> None:
    """redis_url=None -> history is disabled."""
    settings = make_settings(str(tmp_path))
    assert _history_enabled(settings) is False


def test_history_disabled_zero_maxlen(tmp_path) -> None:
    """maxlen=0 -> history is disabled even when redis_url is set."""
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"redis_url": "redis://localhost/0", "history_retention_maxlen": 0},
        },
    )
    assert _history_enabled(settings) is False


def test_history_enabled_with_redis(tmp_path) -> None:
    """redis_url set and maxlen>0 -> history is enabled."""
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    assert _history_enabled(settings) is True


# ---------------------------------------------------------------------------
# _bounded_message
# ---------------------------------------------------------------------------


def test_bounded_message_short() -> None:
    """A message under 500 chars is returned unchanged (after strip/scrub)."""
    msg = "short message"
    assert _bounded_message(msg) == "short message"


def test_bounded_message_long() -> None:
    """A message over 500 chars is truncated to exactly 500."""
    msg = "a" * 600
    result = _bounded_message(msg)
    assert len(result) == 500
    assert result == "a" * 500


# ---------------------------------------------------------------------------
# _to_int / _to_float
# ---------------------------------------------------------------------------


def test_to_int_valid() -> None:
    assert _to_int("42") == 42


def test_to_int_invalid() -> None:
    assert _to_int("abc", default=99) == 99


def test_to_int_none() -> None:
    assert _to_int(None, default=7) == 7


def test_to_int_empty_string() -> None:
    assert _to_int("", default=5) == 5


def test_to_float_valid() -> None:
    assert _to_float("3.14") == 3.14


def test_to_float_invalid() -> None:
    assert _to_float("xyz", default=1.5) == 1.5


def test_to_float_none() -> None:
    assert _to_float(None) == 0.0


def test_to_float_empty_string() -> None:
    assert _to_float("", default=2.5) == 2.5


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
    assert ok is False


def test_record_history_disabled(tmp_path) -> None:
    """When history is not enabled (no redis_url), record returns False immediately."""
    settings = make_settings(str(tmp_path))
    ok = asyncio.run(
        history.record_history_event(settings, status="processed", ticket_id=1)
    )
    assert ok is False


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
    assert ok is True
    assert len(fake.xadd_calls) == 1
    stream, fields, maxlen, approx = fake.xadd_calls[0]
    assert stream == settings.workflow.history_stream
    assert fields["status"] == "processed"
    assert fields["ticket_id"] == "123"
    assert maxlen == settings.workflow.history_retention_maxlen
    assert approx is True


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

    ok = asyncio.run(
        history.record_history_event(settings, status="error", ticket_id=99)
    )

    assert ok is False
    captured = capsys.readouterr()
    assert "history.record_failed" in captured.out


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
    assert len(items) == 1
    assert items[0]["ticket_id"] == 7
    assert items[0]["status"] == "failed_permanent"


def test_read_history_disabled(tmp_path) -> None:
    """When history is not enabled, read_history returns an empty list."""
    settings = make_settings(str(tmp_path))
    items = asyncio.run(history.read_history(settings, limit=10))
    assert items == []


def test_read_history_redis_error(monkeypatch, tmp_path, capsys) -> None:
    """When Redis raises during read, read_history logs a warning and returns []."""
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    failing = _FailingRedis()

    async def _stub_client(_settings):
        return failing

    monkeypatch.setattr(history, "_redis_client", _stub_client)

    items = asyncio.run(history.read_history(settings, limit=10))

    assert items == []
    captured = capsys.readouterr()
    assert "history.read_failed" in captured.out


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

    assert len(fake.xadd_calls) == 1
    _, fields, _, _ = fake.xadd_calls[0]
    assert fields["message"] == "Authorization: Bearer [redacted] token=[redacted]"


# ---------------------------------------------------------------------------
# read_history_json
# ---------------------------------------------------------------------------


def test_read_history_json_disabled(tmp_path) -> None:
    """When history is disabled (no redis_url), read_history_json returns empty JSON."""
    settings = make_settings(str(tmp_path))
    raw = asyncio.run(history.read_history_json(settings, limit=10))
    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert payload["count"] == 0
    assert payload["items"] == []


def test_read_history_json_success(monkeypatch, tmp_path) -> None:
    """With history entries in Redis, read_history_json returns populated JSON."""
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    fake = _FakeRedis()
    fake.entries = [
        ("3-0", {"status": "processed", "ticket_id": "10", "created_at": "100.0"}),
        ("2-0", {"status": "skipped", "ticket_id": "20", "created_at": "99.0"}),
    ]

    async def _stub_client(_settings):
        return fake

    monkeypatch.setattr(history, "_redis_client", _stub_client)

    raw = asyncio.run(history.read_history_json(settings, limit=50))
    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert payload["count"] == 2
    assert len(payload["items"]) == 2
    assert payload["items"][0]["ticket_id"] == 10
    assert payload["items"][0]["status"] == "processed"
    assert payload["items"][1]["ticket_id"] == 20
    assert payload["items"][1]["status"] == "skipped"
