from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from test.support.checks import check
from test.support.redis_queue_helpers import (
    Counter,
    FakeRedis,
    redis_queue_settings,
    replayable_dlq_entries,
    track_replay_enqueue,
)
from zammad_pdf_archiver.app.jobs import redis_queue


class TestReplayDlq:
    def test_replay_dlq(self, monkeypatch, tmp_path) -> None:
        settings = redis_queue_settings(tmp_path)
        fake = FakeRedis(dlq_entries=replayable_dlq_entries())
        enqueue_calls, tracking_enqueue = track_replay_enqueue(fake, settings)

        async def _stub_get_redis(_s: Any) -> FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)
        monkeypatch.setattr(redis_queue, "enqueue_ticket_job", tracking_enqueue)

        result = asyncio.run(redis_queue.replay_dlq(settings, limit=10))

        check(
            not not result
            == {
                "selected": 2,
                "replayed": 2,
                "deleted": 2,
                "skipped": 0,
                "errors": 0,
                "not_deleted": 0,
            },
            "assertion failed",
        )

        check(not not len(enqueue_calls) == 2, "assertion failed")
        check(not not enqueue_calls[0]["attempt"] == 0, "assertion failed")
        check(not not enqueue_calls[0]["payload"] == {"ticket_id": 10}, "assertion failed")
        check(not enqueue_calls[0]["delivery_id"] is not None, "assertion failed")
        check(not not enqueue_calls[1]["attempt"] == 0, "assertion failed")
        check(not not enqueue_calls[1]["payload"] == {"ticket_id": 20}, "assertion failed")
        check(not enqueue_calls[1]["delivery_id"] is not None, "assertion failed")
        check(not ("zammad:jobs:dlq", "1-0") not in fake.deleted, "assertion failed")
        check(not ("zammad:jobs:dlq", "2-0") not in fake.deleted, "assertion failed")

    def test_replay_dlq_zero_limit(self, monkeypatch, tmp_path) -> None:
        settings = redis_queue_settings(tmp_path)

        result = asyncio.run(redis_queue.replay_dlq(settings, limit=0))
        check(
            not not result
            == {
                "selected": 0,
                "replayed": 0,
                "deleted": 0,
                "skipped": 0,
                "errors": 0,
                "not_deleted": 0,
            },
            "assertion failed",
        )

    def test_replay_dlq_skips_invalid_payload(self, monkeypatch, tmp_path) -> None:
        settings = redis_queue_settings(tmp_path)
        fake = FakeRedis(
            dlq_entries=[
                ("1-0", {"payload_json": "NOT-JSON", "delivery_id": "d-bad"}),
                ("2-0", {"payload_json": json.dumps({"ticket_id": 7}), "delivery_id": "d-ok"}),
            ]
        )

        enqueue_calls: list[dict[str, Any]] = []

        async def _tracking_enqueue(**kwargs: Any) -> str:
            enqueue_calls.append(kwargs)
            return await fake.xadd(settings.workflow.queue_stream, {"payload_json": "{}"})

        async def _stub_get_redis(_s: Any) -> FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)
        monkeypatch.setattr(redis_queue, "enqueue_ticket_job", _tracking_enqueue)

        result = asyncio.run(redis_queue.replay_dlq(settings, limit=10))

        check(
            not not result
            == {
                "selected": 2,
                "replayed": 1,
                "deleted": 1,
                "skipped": 1,
                "errors": 0,
                "not_deleted": 0,
            },
            "assertion failed",
        )
        check(not not enqueue_calls[0]["payload"] == {"ticket_id": 7}, "assertion failed")
        check(not not ("zammad:jobs:dlq", "1-0") not in fake.deleted, "assertion failed")
        check(not ("zammad:jobs:dlq", "2-0") not in fake.deleted, "assertion failed")

    def test_replay_dlq_reports_delete_failure(self, monkeypatch, tmp_path) -> None:
        settings = redis_queue_settings(tmp_path)
        fake = FakeRedis(
            dlq_entries=[("1-0", {"payload_json": json.dumps({"ticket_id": 7})})],
            xdel_results=[0],
        )

        async def _tracking_enqueue(**kwargs: Any) -> str:
            return await fake.xadd(settings.workflow.queue_stream, {"payload_json": "{}"})

        async def _stub_get_redis(_s: Any) -> FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)
        monkeypatch.setattr(redis_queue, "enqueue_ticket_job", _tracking_enqueue)

        result = asyncio.run(redis_queue.replay_dlq(settings, limit=10))

        check(
            not not result
            == {
                "selected": 1,
                "replayed": 1,
                "deleted": 0,
                "skipped": 0,
                "errors": 0,
                "not_deleted": 1,
            },
            "assertion failed",
        )
        check(not not ("zammad:jobs:dlq", "1-0") not in fake.deleted, "assertion failed")


def test_process_messages_dlqs_unexpected_handler_exception(monkeypatch, tmp_path) -> None:
    settings = redis_queue_settings(tmp_path)
    fake = FakeRedis()
    failed_counter = Counter()

    async def _raise_handler(*args: Any, **kwargs: Any) -> float:  # noqa: ARG001
        raise RuntimeError("handler broke")

    history_calls: list[dict[str, Any]] = []

    async def _record_history_event(*args: Any, **kwargs: Any) -> bool:
        history_calls.append(kwargs)
        return True

    monkeypatch.setattr(redis_queue, "_handle_envelope", _raise_handler)
    monkeypatch.setattr(redis_queue, "record_history_event", _record_history_event)
    monkeypatch.setattr(redis_queue, "queue_failed_total", failed_counter)

    asyncio.run(
        redis_queue._process_messages(
            fake,
            settings=settings,
            messages=[
                (
                    "1-0",
                    {
                        "payload_json": json.dumps({"ticket_id": 99}),
                        "delivery_id": "d-handler-fail",
                    },
                )
            ],
        )
    )

    check(not not fake.dlq_entries, "assertion failed")
    _, dlq_fields = fake.dlq_entries[0]
    check(not not dlq_fields["reason"] == "handler_exception", "assertion failed")
    check(not "RuntimeError: handler broke" not in dlq_fields["error"], "assertion failed")
    check(not ("zammad:jobs", "archiver", "1-0") not in fake.acked, "assertion failed")
    check(not ("zammad:jobs", "1-0") not in fake.deleted, "assertion failed")
    check(not not history_calls[0]["status"] == "failed_transient", "assertion failed")
    check(not not history_calls[0]["classification"] == "Transient", "assertion failed")
    check(not not failed_counter.count == 1, "assertion failed")


def test_process_messages_does_not_count_or_ack_when_dlq_push_fails(
    monkeypatch,
    tmp_path,
) -> None:
    settings = redis_queue_settings(tmp_path)
    fake = FakeRedis()
    failed_counter = Counter()

    async def _raise_handler(*args: Any, **kwargs: Any) -> float:  # noqa: ARG001
        raise RuntimeError("handler broke")

    async def _failing_push_dlq(*args: Any, **kwargs: Any) -> None:  # noqa: ARG001
        raise RuntimeError("dlq down")

    monkeypatch.setattr(redis_queue, "_handle_envelope", _raise_handler)
    monkeypatch.setattr(redis_queue, "_push_dlq", _failing_push_dlq)
    monkeypatch.setattr(redis_queue, "queue_failed_total", failed_counter)

    with pytest.raises(RuntimeError, match="dlq down"):
        asyncio.run(
            redis_queue._process_messages(
                fake,
                settings=settings,
                messages=[
                    (
                        "1-0",
                        {
                            "payload_json": json.dumps({"ticket_id": 99}),
                            "delivery_id": "d-handler-fail",
                        },
                    )
                ],
            )
        )

    check(not not failed_counter.count == 0, "assertion failed")
    check(not not fake.acked == [], "assertion failed")
    check(not not fake.deleted == [], "assertion failed")
