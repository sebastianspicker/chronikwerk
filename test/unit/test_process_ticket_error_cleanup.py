from __future__ import annotations

from test.support.process_ticket_cleanup_helpers import (
    PROCESSING_TAG,
    TRIGGER_TAG,
    ClientError,
    Path,
    PermanentError,
    SimpleNamespace,
    TagList,
    TransientError,
    _assert_error_transition_cleanup,
    _assert_processing_cleanup_failure,
    _settings,
    asyncio,
    process_ticket,
    pytest,
    ticket_stores,
)


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

    class _FakeClient:
        articles: list[tuple[str, str]] = []
        tags: set[str] = {TRIGGER_TAG}
        tag_ops: list[tuple[str, str]] = []

        def __init__(self, **kwargs) -> None:  # noqa: ANN003, ARG002
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def get_ticket(self, ticket_id: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=ticket_id,
                number="12345",
                title="cleanup",
                owner=SimpleNamespace(login="owner.user"),
                updated_by=SimpleNamespace(login="agent.user"),
                preferences=SimpleNamespace(
                    custom_fields={
                        "archive_path": "Support > Team",
                        "archive_user_mode": "owner",
                    }
                ),
            )

        async def list_tags(self, ticket_id: int) -> TagList:  # noqa: ARG002
            return TagList(sorted(type(self).tags))

        async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            type(self).tag_ops.append(("remove", tag))
            type(self).tags.discard(tag)

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            type(self).tag_ops.append(("add", tag))
            type(self).tags.add(tag)

        async def list_articles(self, ticket_id: int) -> list[SimpleNamespace]:  # noqa: ARG002
            return []

        async def create_internal_article(
            self,
            ticket_id: int,
            subject: str,
            body_html: str,
        ) -> SimpleNamespace:
            type(self).articles.append((subject, body_html))
            return SimpleNamespace(id=1)

    async def _raise_transient(*args, **kwargs):  # noqa: ANN002, ANN003
        raise exc

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

    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.AsyncZammadClient",
        _FakeClient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_and_render_pdf",
        _raise_transient,
    )
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

    class _FakeClient:
        articles: list[tuple[str, str]] = []
        tags: set[str] = {TRIGGER_TAG}
        processing_remove_attempts = 0

        def __init__(self, **kwargs) -> None:  # noqa: ANN003, ARG002
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def get_ticket(self, ticket_id: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=ticket_id,
                number="12345",
                title="cleanup failure",
                owner=SimpleNamespace(login="owner.user"),
                updated_by=SimpleNamespace(login="agent.user"),
                preferences=SimpleNamespace(
                    custom_fields={
                        "archive_path": "Support > Team",
                        "archive_user_mode": "owner",
                    }
                ),
            )

        async def list_tags(self, ticket_id: int) -> TagList:  # noqa: ARG002
            return TagList(sorted(type(self).tags))

        async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            if tag == PROCESSING_TAG:
                type(self).processing_remove_attempts += 1
                raise ClientError("processing tag remove failed")
            type(self).tags.discard(tag)

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            type(self).tags.add(tag)

        async def list_articles(self, ticket_id: int) -> list[SimpleNamespace]:  # noqa: ARG002
            return []

        async def create_internal_article(
            self,
            ticket_id: int,
            subject: str,
            body_html: str,
        ) -> SimpleNamespace:
            type(self).articles.append((subject, body_html))
            return SimpleNamespace(id=1)

    async def _raise_transient(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise TransientError("render failed")

    sleep_delays: list[float] = []

    async def _record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

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

    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.AsyncZammadClient",
        _FakeClient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_and_render_pdf",
        _raise_transient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.asyncio.sleep",
        _record_sleep,
    )
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

