from __future__ import annotations

from test.unit.test_redis_queue import _FakeRedis, check, make_settings, redis_queue


def _settings(tmp_path):
    return make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost:6379"}
        },
    )


def _patch_get_redis(monkeypatch, fake: _FakeRedis) -> None:
    async def fake_get_redis(_settings):  # noqa: ANN001
        return fake

    monkeypatch.setattr(redis_queue, "_get_redis", fake_get_redis)


def _dlq_result(
    *,
    selected: int,
    replayed: int,
    deleted: int,
    skipped: int,
    errors: int,
    not_deleted: int,
) -> dict[str, int]:
    return {
        "selected": selected,
        "replayed": replayed,
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
        "not_deleted": not_deleted,
    }


def test_drain_dlq_empty_returns_zero(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    fake = _FakeRedis()

    _patch_get_redis(monkeypatch, fake)
    result = redis_queue.asyncio.run(redis_queue.drain_dlq(settings))
    check(not not result == {"selected": 0, "deleted": 0, "not_deleted": 0}, "assertion failed")


def test_replay_dlq_empty_returns_zero(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    fake = _FakeRedis()

    _patch_get_redis(monkeypatch, fake)
    result = redis_queue.asyncio.run(redis_queue.replay_dlq(settings))
    check(
        not not result
        == _dlq_result(selected=0, replayed=0, deleted=0, skipped=0, errors=0, not_deleted=0),
        "assertion failed",
    )


def test_replay_dlq_invalid_json_skips_entry(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    fake = _FakeRedis(dlq_entries=[("1-0", {"payload_json": "not-valid-json"})])

    _patch_get_redis(monkeypatch, fake)
    result = redis_queue.asyncio.run(redis_queue.replay_dlq(settings))
    check(
        not not result
        == _dlq_result(selected=1, replayed=0, deleted=0, skipped=1, errors=0, not_deleted=0),
        "assertion failed",
    )


def test_replay_dlq_reports_unconfirmed_delete(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    fake = _FakeRedis(
        dlq_entries=[("1-0", {"payload_json": '{"ticket_id": 7}'})],
        xdel_results=[0],
    )

    async def fake_enqueue(**kwargs):  # noqa: ANN003
        return "2-0"

    _patch_get_redis(monkeypatch, fake)
    monkeypatch.setattr(redis_queue, "enqueue_ticket_job", fake_enqueue)
    result = redis_queue.asyncio.run(redis_queue.replay_dlq(settings))
    check(
        not not result
        == _dlq_result(selected=1, replayed=1, deleted=0, skipped=0, errors=0, not_deleted=1),
        "assertion failed",
    )
    check(not not fake.deleted == [], "assertion failed")


def test_replay_dlq_counts_enqueue_failure(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    fake = _FakeRedis(dlq_entries=[("1-0", {"payload_json": '{"ticket_id": 7}'})])

    async def failing_enqueue(**kwargs):  # noqa: ANN003
        raise RuntimeError("enqueue failed")

    _patch_get_redis(monkeypatch, fake)
    monkeypatch.setattr(redis_queue, "enqueue_ticket_job", failing_enqueue)
    result = redis_queue.asyncio.run(redis_queue.replay_dlq(settings))
    check(
        not not result
        == _dlq_result(selected=1, replayed=0, deleted=0, skipped=0, errors=1, not_deleted=0),
        "assertion failed",
    )
    check(not not fake.deleted == [], "assertion failed")
