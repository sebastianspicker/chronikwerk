from __future__ import annotations

from test.support.process_ticket_cleanup_helpers import (
    Any,
    Path,
    Settings,
    _Observer,
    _settings,
    asyncio,
    cast,
    check,
    process_ticket_module,
    pytest,
)


@pytest.mark.parametrize(
    ("release_behavior", "expected_lock_release_failed"),
    [
        ("ok", False),
        ("false", True),
        ("raise", True),
    ],
)
def test_process_with_ticket_lock_exposes_release_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    release_behavior: str,
    expected_lock_release_failed: bool,
) -> None:
    async def _acquire_ticket(settings: Settings, ticket_id: int) -> bool:  # noqa: ARG001
        return True

    async def _claim_delivery(ctx: Any) -> None:  # noqa: ARG001
        return None

    async def _process_with_client(
        ctx: Any,
        *,
        payload: dict[str, Any],  # noqa: ARG001
    ) -> process_ticket_module.ProcessTicketResult:
        return process_ticket_module.ProcessTicketResult(
            status="processed",
            ticket_id=ctx.ticket_id,
        )

    async def _release_ticket(settings: Settings, ticket_id: int) -> bool:  # noqa: ARG001
        if release_behavior == "raise":
            raise RuntimeError("redis unlock failed")
        return release_behavior == "ok"

    monkeypatch.setattr(process_ticket_module, "try_acquire_ticket", _acquire_ticket)
    monkeypatch.setattr(process_ticket_module, "_claim_delivery_or_skip", _claim_delivery)
    monkeypatch.setattr(
        process_ticket_module,
        "_process_ticket_with_client",
        _process_with_client,
    )
    monkeypatch.setattr(process_ticket_module, "release_ticket", _release_ticket)

    ctx = process_ticket_module._TicketJobContext(  # noqa: SLF001
        settings=_settings(tmp_path),
        ticket_id=321,
        delivery_id="d-lock-release-1",
        request_id="req-lock-release-1",
    )

    result = asyncio.run(
        process_ticket_module._process_with_ticket_lock(  # noqa: SLF001
            ctx,
            payload={"ticket": {"id": 321}},
        )
    )

    check(not not result.status == "processed", "assertion failed")
    check(not not result.ticket_id == 321, "assertion failed")
    check(not result.lock_release_failed is not expected_lock_release_failed, "assertion failed")


@pytest.mark.parametrize(
    ("status", "expected_observations"),
    [
        ("skipped_not_triggered", 0),
        ("processed", 1),
    ],
)
def test_process_ticket_with_client_observes_total_seconds_by_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    expected_observations: int,
) -> None:
    class _FakeClient:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003, ARG002
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

    async def _run_pipeline(**kwargs) -> process_ticket_module.ProcessTicketResult:  # noqa: ANN003
        ctx = kwargs["ctx"]
        return process_ticket_module.ProcessTicketResult(
            status=cast(Any, status),
            ticket_id=ctx.ticket_id,
        )

    observer = _Observer()
    monkeypatch.setattr(process_ticket_module, "AsyncZammadClient", _FakeClient)
    monkeypatch.setattr(process_ticket_module, "_run_ticket_pipeline", _run_pipeline)
    monkeypatch.setattr(process_ticket_module, "total_seconds", observer)

    ctx = process_ticket_module._TicketJobContext(  # noqa: SLF001
        settings=_settings(tmp_path),
        ticket_id=321,
        delivery_id="d-observe-by-status",
        request_id="req-observe-by-status",
    )

    result = asyncio.run(
        process_ticket_module._process_ticket_with_client(ctx, payload={"ticket": {"id": 321}})  # noqa: SLF001
    )

    check(not not result.status == status, "assertion failed")
    check(not not len(observer.observations) == expected_observations, "assertion failed")
