from __future__ import annotations

from test.support.process_ticket_cleanup_helpers import (
    PROCESSING_TAG,
    TRIGGER_TAG,
    ClientError,
    Path,
    PermanentError,
    TransientError,
    _assert_error_transition_cleanup,
    _assert_processing_cleanup_failure,
    _patch_process_ticket_client,
    _patch_process_ticket_render_pdf,
    _patch_process_ticket_sleep,
    _settings,
    _SimpleProcessTicketClient,
    asyncio,
    process_ticket,
    pytest,
    ticket_stores,
)


class _CleanupClient(_SimpleProcessTicketClient):
    tags: set[str] = {TRIGGER_TAG}
    tag_ops: list[tuple[str, str]] = []

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        cls.tags = {TRIGGER_TAG}
        cls.tag_ops = []

    async def list_tags(self, ticket_id: int):  # noqa: ANN201, ARG002
        from test.support.process_ticket_cleanup_helpers import TagList

        return TagList(sorted(type(self).tags))

    async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
        type(self).tag_ops.append(("remove", tag))
        type(self).tags.discard(tag)

    async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
        type(self).tag_ops.append(("add", tag))
        type(self).tags.add(tag)


def _recording_history():
    history: list[tuple[str, str | None, str]] = []

    async def _record_history(
        ctx,
        *,
        status: str,
        classification: str | None = None,
        message: str = "",
    ) -> bool:  # noqa: ANN001
        history.append((status, classification, message))
        return True

    return history, _record_history


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_classification", "trigger_should_remain"),
    [
        (
            TransientError("render failed"),
            "failed_transient",
            "Transient",
            True,
        ),
        (
            PermanentError("archive path invalid"),
            "failed_permanent",
            "Permanent",
            False,
        ),
    ],
)
def test_process_ticket_error_transition_cleans_processing_tag_and_posts_note(
    monkeypatch,
    tmp_path: Path,
    exc: Exception,
    expected_status: str,
    expected_classification: str,
    trigger_should_remain: bool,
) -> None:
    ticket_stores._reset_for_tests()

    class _FakeClient(_CleanupClient):
        ticket_title = "cleanup"

    async def _raise_transient(*args, **kwargs):  # noqa: ANN002, ANN003
        raise exc

    history, _record_history = _recording_history()
    _patch_process_ticket_client(monkeypatch, _FakeClient)
    _patch_process_ticket_render_pdf(monkeypatch, _raise_transient)
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket._record_history",
        _record_history,
    )

    settings = _settings(tmp_path)
    payload = {"ticket": {"id": 321}}

    result = asyncio.run(process_ticket("d-cleanup-tags-1", payload, settings))

    _assert_error_transition_cleanup(
        result=result,
        client=_FakeClient,
        history=history,
        exc=exc,
        expected_status=expected_status,
        expected_classification=expected_classification,
        trigger_should_remain=trigger_should_remain,
    )


def test_process_ticket_exposes_processing_tag_cleanup_failure(monkeypatch, tmp_path: Path) -> None:
    ticket_stores._reset_for_tests()

    class _FakeClient(_CleanupClient):
        ticket_title = "cleanup failure"
        processing_remove_attempts = 0

        async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            if tag == PROCESSING_TAG:
                type(self).processing_remove_attempts += 1
                raise ClientError("processing tag remove failed")
            await super().remove_tag(ticket_id, tag)

    async def _raise_transient(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise TransientError("render failed")

    sleep_delays = _patch_process_ticket_sleep(monkeypatch)
    history, _record_history = _recording_history()
    _patch_process_ticket_client(monkeypatch, _FakeClient)
    _patch_process_ticket_render_pdf(monkeypatch, _raise_transient)
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket._record_history",
        _record_history,
    )

    result = asyncio.run(
        process_ticket("d-cleanup-failure-1", {"ticket": {"id": 321}}, _settings(tmp_path))
    )

    _assert_processing_cleanup_failure(
        result=result,
        client=_FakeClient,
        history=history,
        sleep_delays=sleep_delays,
    )
