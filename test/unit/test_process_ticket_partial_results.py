from __future__ import annotations

from test.support.process_ticket_cleanup_helpers import (
    UTC,
    ClientError,
    Path,
    SimpleNamespace,
    Snapshot,
    TicketMeta,
    _assert_done_tag_update_partial_failure,
    _assert_success_acknowledgement_partial_failure,
    _Counter,
    _patch_process_ticket_client,
    _patch_process_ticket_render_pdf,
    _pdf_render_result,
    _recording_history,
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


def _setup_partial_ticket_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client_cls: type[_SimpleProcessTicketClient],
    render_pdf,
    record_history=None,
) -> None:  # noqa: ANN001
    ticket_stores._reset_for_tests()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    _patch_process_ticket_client(monkeypatch, client_cls)
    _patch_process_ticket_render_pdf(monkeypatch, render_pdf)
    if record_history is not None:
        monkeypatch.setattr(
            "zammad_pdf_archiver.app.jobs.process_ticket._record_history",
            record_history,
        )


def _render_pdf_with_title(title: str):
    async def _render_pdf(*args, **kwargs) -> tuple[bytes, Snapshot, bool, int]:  # noqa: ANN002, ANN003
        return _pdf_render_result(title=title)

    return _render_pdf


def test_process_ticket_reports_partial_when_done_tag_update_fails(
    monkeypatch, tmp_path: Path
) -> None:
    class _FakeClient(_SimpleProcessTicketClient):
        ticket_title = "done update failure"

    async def _raise_done_failure(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise ClientError("Zammad tag add did not confirm success")

    history, _record_history = _recording_history(False)

    _setup_partial_ticket_run(
        monkeypatch,
        client_cls=_FakeClient,
        render_pdf=_render_pdf_with_title("done update failure"),
        record_history=_record_history,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket._apply_done_with_backoff",
        _raise_done_failure,
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
    class _FakeClient(_SimpleProcessTicketClient):
        ticket_title = "acknowledgement failure"

        async def create_internal_article(
            self,
            ticket_id: int,
            subject: str,
            body_html: str,  # noqa: ARG002
        ) -> SimpleNamespace:
            raise RuntimeError("note failed")

    history, _record_history = _recording_history(True)

    _setup_partial_ticket_run(
        monkeypatch,
        client_cls=_FakeClient,
        render_pdf=_render_pdf_with_title("acknowledgement failure"),
        record_history=_record_history,
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
    class _FakeClient(_SimpleProcessTicketClient):
        ticket_title = "partial archive"

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

    _setup_partial_ticket_run(
        monkeypatch,
        client_cls=_FakeClient,
        render_pdf=_render_partial_archive,
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
