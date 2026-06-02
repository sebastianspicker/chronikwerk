from __future__ import annotations

from test.support.process_ticket_cleanup_helpers import (
    UTC,
    ClientError,
    Path,
    SimpleNamespace,
    Snapshot,
    TagList,
    TicketMeta,
    _assert_done_tag_update_partial_failure,
    _assert_success_acknowledgement_partial_failure,
    _Counter,
    _settings,
    asyncio,
    check,
    datetime,
    freeze_process_ticket_now,
    process_ticket,
    process_ticket_module,
    pytest,
    ticket_stores,
)


def test_process_ticket_reports_partial_when_done_tag_update_fails(
    monkeypatch, tmp_path: Path
) -> None:
    ticket_stores._reset_for_tests()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    class _FakeClient:
        articles: list[tuple[str, str]] = []

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
                title="done update failure",
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
            return TagList(["pdf:sign"])

        async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            return None

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            return None

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

    async def _render_pdf(*args, **kwargs) -> tuple[bytes, Snapshot, bool, int]:  # noqa: ANN002, ANN003
        snapshot = Snapshot(
            ticket=TicketMeta(id=321, number="12345", title="done update failure"),
            articles=[],
        )
        return b"%PDF-1.4\n%%EOF\n", snapshot, False, 0

    async def _raise_done_failure(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise ClientError("Zammad tag add did not confirm success")

    history: list[tuple[str, str | None, str]] = []

    async def _record_history(
        ctx,
        *,
        status: str,
        classification: str | None = None,
        message: str = "",
    ) -> bool:  # noqa: ANN001
        history.append((status, classification, message))
        return False

    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.AsyncZammadClient",
        _FakeClient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_and_render_pdf",
        _render_pdf,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket._apply_done_with_backoff",
        _raise_done_failure,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket._record_history",
        _record_history,
    )

    result = asyncio.run(
        process_ticket("d-done-fail-1", {"ticket": {"id": 321}}, _settings(tmp_path))
    )

    _assert_done_tag_update_partial_failure(
        result=result,
        client=_FakeClient,
        history=history,
        tmp_path=tmp_path,
    )


def test_process_ticket_reports_partial_when_success_acknowledgement_fails(
    monkeypatch, tmp_path: Path
) -> None:
    ticket_stores._reset_for_tests()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    class _FakeClient:
        added_tags: list[str] = []

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
                title="acknowledgement failure",
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
            return TagList(["pdf:sign"])

        async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            return None

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            type(self).added_tags.append(tag)

        async def list_articles(self, ticket_id: int) -> list[SimpleNamespace]:  # noqa: ARG002
            return []

        async def create_internal_article(
            self,
            ticket_id: int,
            subject: str,
            body_html: str,  # noqa: ARG002
        ) -> SimpleNamespace:
            raise RuntimeError("note failed")

    async def _render_pdf(*args, **kwargs) -> tuple[bytes, Snapshot, bool, int]:  # noqa: ANN002, ANN003
        snapshot = Snapshot(
            ticket=TicketMeta(id=321, number="12345", title="acknowledgement failure"),
            articles=[],
        )
        return b"%PDF-1.4\n%%EOF\n", snapshot, False, 0

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
        _render_pdf,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket._record_history",
        _record_history,
    )

    result = asyncio.run(
        process_ticket("d-ack-fail-1", {"ticket": {"id": 321}}, _settings(tmp_path))
    )

    _assert_success_acknowledgement_partial_failure(
        result=result,
        client=_FakeClient,
        history=history,
        tmp_path=tmp_path,
    )


def test_process_ticket_exposes_partial_archive_result_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ticket_stores._reset_for_tests()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    class _FakeClient:
        articles: list[tuple[str, str]] = []

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
                title="partial archive",
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
            return TagList(["pdf:sign"])

        async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            return None

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            return None

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

    async def _render_partial_archive(
        client,
        ticket,
        tags,
        ticket_id: int,
        settings,  # noqa: ANN001, ARG001
    ) -> tuple[bytes, Snapshot, bool, int]:
        snapshot = Snapshot(
            ticket=TicketMeta(id=ticket_id, number=ticket.number, title=ticket.title),
            articles=[],
        )
        return b"%PDF-1.4\n%%EOF\n", snapshot, True, 2

    partial_counter = _Counter()

    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.AsyncZammadClient",
        _FakeClient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_and_render_pdf",
        _render_partial_archive,
    )
    monkeypatch.setattr(process_ticket_module, "processed_partial_total", partial_counter)

    result = asyncio.run(
        process_ticket("d-partial-archive-1", {"ticket": {"id": 321}}, _settings(tmp_path))
    )

    check(not not result.status == "processed", "assertion failed")
    check(not result.articles_capped is not True, "assertion failed")
    check(not not result.attachments_skipped == 2, "assertion failed")
    check(not not partial_counter.count == 1, "assertion failed")
    check(not not len(_FakeClient.articles) == 1, "assertion failed")
    check(not "PDF archived" not in _FakeClient.articles[0][0], "assertion failed")
    check(not not list(tmp_path.rglob("*.pdf")), "assertion failed")

