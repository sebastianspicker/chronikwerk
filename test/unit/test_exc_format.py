from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue
from zammad_pdf_archiver.config.redact import REDACTED_VALUE
from zammad_pdf_archiver.domain.exc_format import bounded_exc_message


@dataclass
class _FakeRedis:
    xadds: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.xadds.append((stream, fields))
        return "1-0"


def test_bounded_exc_message_scrubs_secret_patterns() -> None:
    msg = bounded_exc_message(
        RuntimeError("Authorization: Bearer abc123 token=qwerty api_token=topsecret")
    )

    check(not "RuntimeError:" not in msg, "assertion failed")
    check(not not "abc123" not in msg, "assertion failed")
    check(not not "qwerty" not in msg, "assertion failed")
    check(not not "topsecret" not in msg, "assertion failed")
    check(not REDACTED_VALUE not in msg, "assertion failed")


def test_bounded_exc_message_truncates_to_max_len() -> None:
    check(not not bounded_exc_message("x" * 100, max_len=17) == "x" * 17, "assertion failed")


def test_enqueue_ticket_job_scrubs_last_error(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost/0",
            }
        },
    )
    fake = _FakeRedis()

    async def _stub_get_redis(_settings: Any) -> _FakeRedis:
        return fake

    monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)

    asyncio.run(
        redis_queue.enqueue_ticket_job(
            delivery_id="d-secret",
            payload={"ticket_id": 123},
            settings=settings,
            last_error="Authorization: Bearer abc123 token=qwerty api_token=topsecret",
        )
    )

    _, fields = fake.xadds[0]
    check(not not "abc123" not in fields["last_error"], "assertion failed")
    check(not not "qwerty" not in fields["last_error"], "assertion failed")
    check(not not "topsecret" not in fields["last_error"], "assertion failed")
    check(not REDACTED_VALUE not in fields["last_error"], "assertion failed")
