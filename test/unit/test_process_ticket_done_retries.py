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


class _DoneRetryClient(_SimpleProcessTicketClient):
    done_attempts: int


def _render_pdf_with_title(title: str):
    async def _render_pdf(*args, **kwargs) -> tuple[bytes, Snapshot, bool, int]:  # noqa: ANN002, ANN003
        return _pdf_render_result(title=title)

    return _render_pdf


def _run_done_retry_case(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    client_cls: type[_DoneRetryClient],
    delivery_id: str,
    title: str,
):
    ticket_stores._reset_for_tests()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    sleep_delays = _patch_process_ticket_sleep(monkeypatch)
    _patch_process_ticket_client(monkeypatch, client_cls)
    _patch_process_ticket_render_pdf(monkeypatch, _render_pdf_with_title(title))

    result = asyncio.run(
        process_ticket(delivery_id, {"ticket": {"id": 321}}, _settings(tmp_path))
    )
    return result, sleep_delays


def _check_done_update_partial_result(
    *,
    result,
    client_cls: type[_DoneRetryClient],
    attempts: int,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    check(not not result.status == "processed_done_update_failed", "assertion failed")
    check(not not result.classification == "Partial", "assertion failed")
    check(not result.error_note_posted is not True, "assertion failed")
    check(not not client_cls.done_attempts == attempts, "assertion failed")
    check(not not len(client_cls.articles) == 1, "assertion failed")
    check(not "done_tag_update_failed" not in client_cls.articles[0][1], "assertion failed")
    check(not not list(tmp_path.rglob("*.pdf")), "assertion failed")


def test_process_ticket_retries_done_tag_before_success_note(monkeypatch, tmp_path: Path) -> None:
    class _FakeClient(_DoneRetryClient):
        done_attempts = 0
        ticket_title = "done retry success"

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            if tag == DONE_TAG:
                type(self).done_attempts += 1
                if type(self).done_attempts == 1:
                    raise ClientError("temporary done tag failure")
            type(self).added_tags.append(tag)

    result, sleep_delays = _run_done_retry_case(
        monkeypatch,
        tmp_path,
        client_cls=_FakeClient,
        delivery_id="d-done-retry-success-1",
        title="done retry success",
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
    class _FakeClient(_DoneRetryClient):
        done_attempts = 0
        ticket_title = "done retry exhausted"

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            if tag == DONE_TAG:
                type(self).done_attempts += 1
                raise TransientError("temporary done tag failure")

    result, sleep_delays = _run_done_retry_case(
        monkeypatch,
        tmp_path,
        client_cls=_FakeClient,
        delivery_id="d-done-retry-exhausted-1",
        title="done retry exhausted",
    )

    _check_done_update_partial_result(
        result=result,
        client_cls=_FakeClient,
        attempts=4,
        tmp_path=tmp_path,
    )
    _assert_increasing_delays(sleep_delays, count=3)


def test_apply_done_backoff_stops_on_permanent_after_transient(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _FakeClient(_DoneRetryClient):
        done_attempts = 0
        ticket_title = "done retry permanent"

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            if tag != DONE_TAG:
                return None
            type(self).done_attempts += 1
            if type(self).done_attempts == 1:
                raise TransientError("temporary done tag failure")
            raise PermanentError("permanent done tag failure")

    result, sleep_delays = _run_done_retry_case(
        monkeypatch,
        tmp_path,
        client_cls=_FakeClient,
        delivery_id="d-done-retry-permanent-1",
        title="done retry permanent",
    )

    _check_done_update_partial_result(
        result=result,
        client_cls=_FakeClient,
        attempts=2,
        tmp_path=tmp_path,
    )
    _assert_nonnegative_delays(sleep_delays, count=1)


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
