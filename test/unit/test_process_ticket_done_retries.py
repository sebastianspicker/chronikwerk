from __future__ import annotations

from test.support.process_ticket_cleanup_helpers import (
    DONE_TAG,
    ERROR_TAG,
    UTC,
    ClientError,
    Path,
    PermanentError,
    Snapshot,
    TransientError,
    _assert_increasing_delays,
    _assert_nonnegative_delays,
    _patch_process_ticket_client,
    _patch_process_ticket_render_pdf,
    _patch_process_ticket_sleep,
    _pdf_render_result,
    _settings,
    _SimpleProcessTicketClient,
    asyncio,
    check,
    datetime,
    freeze_process_ticket_now,
    process_ticket,
    process_ticket_module,
    pytest,
    ticket_stores,
)


def test_process_ticket_retries_done_tag_before_success_note(monkeypatch, tmp_path: Path) -> None:
    ticket_stores._reset_for_tests()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    class _FakeClient(_SimpleProcessTicketClient):
        done_attempts = 0
        ticket_title = "done retry success"

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            if tag == DONE_TAG:
                type(self).done_attempts += 1
                if type(self).done_attempts == 1:
                    raise ClientError("temporary done tag failure")
            type(self).added_tags.append(tag)

    async def _render_pdf(*args, **kwargs) -> tuple[bytes, Snapshot, bool, int]:  # noqa: ANN002, ANN003
        return _pdf_render_result(title="done retry success")

    sleep_delays = _patch_process_ticket_sleep(monkeypatch)
    _patch_process_ticket_client(monkeypatch, _FakeClient)
    _patch_process_ticket_render_pdf(monkeypatch, _render_pdf)

    result = asyncio.run(
        process_ticket("d-done-retry-success-1", {"ticket": {"id": 321}}, _settings(tmp_path))
    )

    check(not not result.status == "processed", "assertion failed")
    check(not not _FakeClient.done_attempts == 2, "assertion failed")
    _assert_nonnegative_delays(sleep_delays, count=1)
    check(not DONE_TAG not in _FakeClient.added_tags, "assertion failed")
    check(not not ERROR_TAG not in _FakeClient.added_tags, "assertion failed")
    check(not not len(_FakeClient.articles) == 1, "assertion failed")
    check(not "PDF archived" not in _FakeClient.articles[0][0], "assertion failed")
    check(not not list(tmp_path.rglob("*.pdf")), "assertion failed")


def test_apply_done_backoff_exhaustion_returns_partial_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ticket_stores._reset_for_tests()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    class _FakeClient(_SimpleProcessTicketClient):
        done_attempts = 0
        ticket_title = "done retry exhausted"

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            if tag == DONE_TAG:
                type(self).done_attempts += 1
                raise TransientError("temporary done tag failure")

    async def _render_pdf(*args, **kwargs) -> tuple[bytes, Snapshot, bool, int]:  # noqa: ANN002, ANN003
        return _pdf_render_result(title="done retry exhausted")

    sleep_delays = _patch_process_ticket_sleep(monkeypatch)
    _patch_process_ticket_client(monkeypatch, _FakeClient)
    _patch_process_ticket_render_pdf(monkeypatch, _render_pdf)

    result = asyncio.run(
        process_ticket("d-done-retry-exhausted-1", {"ticket": {"id": 321}}, _settings(tmp_path))
    )

    check(not not result.status == "processed_done_update_failed", "assertion failed")
    check(not not result.classification == "Partial", "assertion failed")
    check(not result.error_note_posted is not True, "assertion failed")
    check(not not _FakeClient.done_attempts == 4, "assertion failed")
    _assert_increasing_delays(sleep_delays, count=3)
    check(not not len(_FakeClient.articles) == 1, "assertion failed")
    check(not "done_tag_update_failed" not in _FakeClient.articles[0][1], "assertion failed")
    check(not not list(tmp_path.rglob("*.pdf")), "assertion failed")


def test_apply_done_backoff_stops_on_permanent_after_transient(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ticket_stores._reset_for_tests()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    class _FakeClient(_SimpleProcessTicketClient):
        done_attempts = 0
        ticket_title = "done retry permanent"

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            if tag != DONE_TAG:
                return None
            type(self).done_attempts += 1
            if type(self).done_attempts == 1:
                raise TransientError("temporary done tag failure")
            raise PermanentError("permanent done tag failure")

    async def _render_pdf(*args, **kwargs) -> tuple[bytes, Snapshot, bool, int]:  # noqa: ANN002, ANN003
        return _pdf_render_result(title="done retry permanent")

    sleep_delays = _patch_process_ticket_sleep(monkeypatch)
    _patch_process_ticket_client(monkeypatch, _FakeClient)
    _patch_process_ticket_render_pdf(monkeypatch, _render_pdf)

    result = asyncio.run(
        process_ticket("d-done-retry-permanent-1", {"ticket": {"id": 321}}, _settings(tmp_path))
    )

    check(not not result.status == "processed_done_update_failed", "assertion failed")
    check(not not result.classification == "Partial", "assertion failed")
    check(not result.error_note_posted is not True, "assertion failed")
    check(not not _FakeClient.done_attempts == 2, "assertion failed")
    _assert_nonnegative_delays(sleep_delays, count=1)
    check(not not len(_FakeClient.articles) == 1, "assertion failed")
    check(not "done_tag_update_failed" not in _FakeClient.articles[0][1], "assertion failed")
    check(not not list(tmp_path.rglob("*.pdf")), "assertion failed")


def test_process_ticket_exposes_exhausted_error_tag_retry(monkeypatch, tmp_path: Path) -> None:
    ticket_stores._reset_for_tests()

    class _FakeClient(_SimpleProcessTicketClient):
        error_attempts = 0
        ticket_title = "error retry exhausted"

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            if tag == ERROR_TAG:
                type(self).error_attempts += 1
                raise ClientError("temporary error tag failure")

    async def _raise_transient(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise TransientError("render failed")

    sleep_delays = _patch_process_ticket_sleep(monkeypatch)
    _patch_process_ticket_client(monkeypatch, _FakeClient)
    _patch_process_ticket_render_pdf(monkeypatch, _raise_transient)

    result = asyncio.run(
        process_ticket("d-error-retry-exhausted-1", {"ticket": {"id": 321}}, _settings(tmp_path))
    )

    check(not not result.status == "failed_transient", "assertion failed")
    check(not not result.classification == "Transient", "assertion failed")
    check(not "render failed" not in result.message, "assertion failed")
    check(not result.error_note_posted is not True, "assertion failed")
    check(not result.error_tag_applied is not False, "assertion failed")
    check(not not _FakeClient.error_attempts == 2, "assertion failed")
    _assert_nonnegative_delays(sleep_delays, count=1)
    check(not not len(_FakeClient.articles) == 1, "assertion failed")
    check(not "Transient" not in _FakeClient.articles[0][1], "assertion failed")
    check(not "render failed" not in _FakeClient.articles[0][1], "assertion failed")
