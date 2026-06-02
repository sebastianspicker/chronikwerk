from __future__ import annotations

from test.support.process_ticket_cleanup_helpers import (
    DONE_TAG,
    TRIGGER_TAG,
    UTC,
    Any,
    Path,
    SimpleNamespace,
    Snapshot,
    TagList,
    TicketMeta,
    TransientError,
    _assert_error_visibility_failures,
    _CapturingLog,
    _settings,
    _VisibilityFailureClient,
    asyncio,
    cast,
    check,
    datetime,
    freeze_process_ticket_now,
    process_ticket,
    process_ticket_module,
    pytest,
    ticket_stores,
)


def test_apply_error_with_retry_logs_first_exception_before_second_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_exc = RuntimeError("first failure")
    second_exc = RuntimeError("second failure")
    calls: list[object] = []

    async def _failing_apply_error(*args: object, **kwargs: object) -> None:
        calls.append("apply_error")
        if calls.count("apply_error") == 1:
            raise first_exc
        raise second_exc

    async def _record_sleep(delay: float) -> None:
        calls.append(("sleep", delay))

    capture = _CapturingLog()
    monkeypatch.setattr(process_ticket_module, "apply_error", _failing_apply_error)
    monkeypatch.setattr(process_ticket_module.asyncio, "sleep", _record_sleep)
    monkeypatch.setattr(process_ticket_module, "log", capture)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(
            process_ticket_module._apply_error_with_retry(  # noqa: SLF001
                cast(Any, object()),
                ticket_id=321,
                keep_trigger=True,
                trigger_tag=TRIGGER_TAG,
            )
        )

    check(not exc_info.value is not second_exc, "assertion failed")
    check(
        not not capture.warning_events
        == [("apply_error_first_attempt_failed", {"ticket_id": 321, "exc_info": first_exc})],
        "assertion failed",
    )
    check(not not len(calls) == 3, "assertion failed")
    check(not not calls[0] == "apply_error", "assertion failed")
    sleep_call = calls[1]
    if not isinstance(sleep_call, tuple):
        raise AssertionError("assertion failed")
    check(not not sleep_call[0] == "sleep", "assertion failed")
    delay = sleep_call[1]
    check(not not isinstance(delay, (int, float)), "assertion failed")
    check(not not delay >= 0, "assertion failed")
    check(not not calls[2] == "apply_error", "assertion failed")


def test_process_ticket_reports_unrecorded_history_on_success(monkeypatch, tmp_path: Path) -> None:
    ticket_stores._reset_for_tests()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    class _FakeClient:
        added_tags: list[str] = []
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
                title="history unavailable",
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
            body_html: str,
        ) -> SimpleNamespace:
            type(self).articles.append((subject, body_html))
            return SimpleNamespace(id=1)

    async def _render_pdf(*args, **kwargs) -> tuple[bytes, Snapshot, bool, int]:  # noqa: ANN002, ANN003
        snapshot = Snapshot(
            ticket=TicketMeta(id=321, number="12345", title="history unavailable"),
            articles=[],
        )
        return b"%PDF-1.4\n%%EOF\n", snapshot, False, 0

    async def _history_write_failed(*args, **kwargs) -> bool:  # noqa: ANN002, ANN003
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
        "zammad_pdf_archiver.app.jobs.process_ticket.record_history_event",
        _history_write_failed,
    )

    result = asyncio.run(
        process_ticket("d-history-fail-1", {"ticket": {"id": 321}}, _settings(tmp_path))
    )

    check(not not result.status == "processed", "assertion failed")
    check(not result.history_recorded is not False, "assertion failed")
    check(not DONE_TAG not in _FakeClient.added_tags, "assertion failed")
    check(not not len(_FakeClient.articles) == 1, "assertion failed")
    check(not not list(tmp_path.rglob("*.pdf")), "assertion failed")


@pytest.mark.parametrize(
    (
        "error_note_fails",
        "error_tag_fails",
        "expected_error_note_posted",
        "expected_error_tag_applied",
    ),
    [
        (True, False, False, True),
        (False, True, True, False),
        (True, True, False, False),
    ],
)
def test_process_ticket_result_exposes_error_visibility_failures(
    monkeypatch,
    tmp_path: Path,
    error_note_fails: bool,
    error_tag_fails: bool,
    expected_error_note_posted: bool,
    expected_error_tag_applied: bool,
) -> None:
    ticket_stores._reset_for_tests()
    _VisibilityFailureClient.articles = []
    _VisibilityFailureClient.error_note_fails = error_note_fails

    async def _raise_transient(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise TransientError("render-failed")

    async def _apply_error_with_optional_failure(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        if error_tag_fails:
            raise RuntimeError("error tag failed")

    history: list[tuple[str, str | None, str]] = []
    capture = _CapturingLog()

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
        _VisibilityFailureClient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_and_render_pdf",
        _raise_transient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket._apply_error_with_retry",
        _apply_error_with_optional_failure,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket._record_history",
        _record_history,
    )
    monkeypatch.setattr(process_ticket_module, "log", capture)

    delivery_id = f"d-visibility-fail-{error_note_fails}-{error_tag_fails}"
    result = asyncio.run(
        process_ticket(
            delivery_id,
            {"ticket": {"id": 321}},
            _settings(tmp_path),
        )
    )

    _assert_error_visibility_failures(
        result=result,
        client=_VisibilityFailureClient,
        history=history,
        capture=capture,
        delivery_id=delivery_id,
        expected_error_note_posted=expected_error_note_posted,
        expected_error_tag_applied=expected_error_tag_applied,
    )
