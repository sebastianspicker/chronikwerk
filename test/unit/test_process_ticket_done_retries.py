from __future__ import annotations

from test.support.process_ticket_cleanup_helpers import (
    DONE_TAG,
    ERROR_TAG,
    UTC,
    ClientError,
    Path,
    PermanentError,
    SimpleNamespace,
    Snapshot,
    TagList,
    TicketMeta,
    TransientError,
    _assert_increasing_delays,
    _assert_nonnegative_delays,
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


def test_process_ticket_retries_done_tag_before_success_note(monkeypatch, tmp_path: Path) -> None:
    ticket_stores._reset_for_tests()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    class _FakeClient:
        added_tags: list[str] = []
        articles: list[tuple[str, str]] = []
        done_attempts = 0

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
                title="done retry success",
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
            if tag == DONE_TAG:
                type(self).done_attempts += 1
                if type(self).done_attempts == 1:
                    raise ClientError("temporary done tag failure")
            type(self).added_tags.append(tag)

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
            ticket=TicketMeta(id=321, number="12345", title="done retry success"),
            articles=[],
        )
        return b"%PDF-1.4\n%%EOF\n", snapshot, False, 0

    sleep_delays: list[float] = []

    async def _record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.AsyncZammadClient",
        _FakeClient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_and_render_pdf",
        _render_pdf,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.asyncio.sleep",
        _record_sleep,
    )

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

    class _FakeClient:
        done_attempts = 0
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
                title="done retry exhausted",
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
            if tag == DONE_TAG:
                type(self).done_attempts += 1
                raise TransientError("temporary done tag failure")

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
            ticket=TicketMeta(id=321, number="12345", title="done retry exhausted"),
            articles=[],
        )
        return b"%PDF-1.4\n%%EOF\n", snapshot, False, 0

    sleep_delays: list[float] = []

    async def _record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.AsyncZammadClient",
        _FakeClient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_and_render_pdf",
        _render_pdf,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.asyncio.sleep",
        _record_sleep,
    )

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

    class _FakeClient:
        done_attempts = 0
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
                title="done retry permanent",
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
            if tag != DONE_TAG:
                return None
            type(self).done_attempts += 1
            if type(self).done_attempts == 1:
                raise TransientError("temporary done tag failure")
            raise PermanentError("permanent done tag failure")

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
            ticket=TicketMeta(id=321, number="12345", title="done retry permanent"),
            articles=[],
        )
        return b"%PDF-1.4\n%%EOF\n", snapshot, False, 0

    sleep_delays: list[float] = []

    async def _record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.AsyncZammadClient",
        _FakeClient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_and_render_pdf",
        _render_pdf,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.asyncio.sleep",
        _record_sleep,
    )

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

    class _FakeClient:
        articles: list[tuple[str, str]] = []
        error_attempts = 0

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
                title="error retry exhausted",
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
            if tag == ERROR_TAG:
                type(self).error_attempts += 1
                raise ClientError("temporary error tag failure")

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

