"""Pin all-or-nothing scheduling and shutdown rejection of the real scheduling service."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from chronikwerk.operations.admission import JobAdmission
from chronikwerk.operations.history import JobHistory
from chronikwerk.operations.job import TicketJob
from chronikwerk.operations.scheduling import TicketProcessor, TicketSchedulingService
from chronikwerk.web.app import create_app
from tests.support.hmac_test_helpers import sign_body
from tests.support.settings_factory import make_settings


def _jobs(count: int) -> list[TicketJob]:
    """Build distinct ticket jobs with delivery identifiers."""
    return [
        TicketJob(ticket_id=index + 1, payload={"ticket_id": index + 1}, delivery_id=f"d-{index}")
        for index in range(count)
    ]


def _job(ticket_id: int) -> TicketJob:
    """Build one ticket job without a delivery identifier."""
    return TicketJob(ticket_id=ticket_id, payload={"ticket_id": ticket_id})


def _service(
    admission: JobAdmission,
    process: TicketProcessor,
    *,
    shutdown_timeout_seconds: float = 1.0,
) -> TicketSchedulingService:
    """Build a scheduling service with its own volatile history."""
    return TicketSchedulingService(
        admission=admission,
        process_ticket=process,
        history=JobHistory(),
        shutdown_timeout_seconds=shutdown_timeout_seconds,
    )


async def _drain() -> None:
    """Await every other task on the loop so scheduled jobs finish."""
    current = asyncio.current_task()
    await asyncio.gather(*(task for task in asyncio.all_tasks() if task is not current))


def test_batch_exceeding_capacity_is_rejected_without_processing() -> None:
    async def scenario() -> None:
        calls: list[int] = []

        async def process(job: TicketJob) -> None:
            calls.append(job.payload["ticket_id"])

        admission = JobAdmission(max_pending=1, max_running=1)
        service = _service(admission, process)

        assert service.schedule_batch(_jobs(3)) is False
        await _drain()

        assert calls == []
        assert (admission.pending, admission.running) == (0, 0)

    asyncio.run(scenario())


def test_batch_within_capacity_processes_every_job() -> None:
    async def scenario() -> None:
        calls: list[tuple[str | None, int]] = []

        async def process(job: TicketJob) -> None:
            calls.append((job.delivery_id, job.payload["ticket_id"]))

        admission = JobAdmission(max_pending=1, max_running=1)
        service = _service(admission, process)

        assert service.schedule_batch(_jobs(2)) is True
        await _drain()

        assert sorted(calls) == [("d-0", 1), ("d-1", 2)]
        assert (admission.pending, admission.running) == (0, 0)

    asyncio.run(scenario())


def test_failing_processor_releases_capacity() -> None:
    async def scenario() -> None:
        async def process(_job: TicketJob) -> None:
            raise RuntimeError("synthetic failure")

        admission = JobAdmission(max_pending=0, max_running=1)
        service = _service(admission, process)

        assert service.schedule(_job(1)) is True
        await _drain()

        assert (admission.pending, admission.running) == (0, 0)
        assert service.schedule(_job(2)) is True
        await _drain()

    asyncio.run(scenario())


def test_service_rejects_work_while_shutting_down() -> None:
    async def scenario() -> None:
        async def process(_job: TicketJob) -> None:
            raise AssertionError("no work may start during shutdown")

        service = _service(JobAdmission(max_pending=1, max_running=1), process)
        await service.aclose()

        assert service.accepting is False
        assert service.schedule(_job(1)) is False
        assert service.schedule_batch(_jobs(1)) is False
        assert service.schedule_retry(ticket_id=1, request_id=None) is False

    asyncio.run(scenario())


class _GatedCloseAdmission(JobAdmission):
    """Admission whose close suspends until the test opens the gate."""

    def __init__(self) -> None:
        super().__init__(max_pending=1, max_running=1)
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()

    async def close(self) -> None:
        """Pause inside close so the test can interleave a schedule call."""
        self.close_started.set()
        await self.allow_close.wait()
        await super().close()


def test_schedule_during_close_is_rejected_before_admission_closes() -> None:
    """A job offered while aclose is still awaiting admission never reaches the processor."""

    async def scenario() -> None:
        calls: list[TicketJob] = []

        async def process(job: TicketJob) -> None:
            calls.append(job)

        admission = _GatedCloseAdmission()
        service = _service(admission, process)
        closing = asyncio.create_task(service.aclose())
        await asyncio.wait_for(admission.close_started.wait(), 1)

        assert not admission.closing
        assert service.schedule(_job(1)) is False
        assert admission.pending == 0
        admission.allow_close.set()
        await closing
        await _drain()

        assert calls == []
        assert (admission.pending, admission.running) == (0, 0)

    asyncio.run(scenario())


def test_close_cancels_overdue_job_and_awaits_its_finalizer() -> None:
    """A running job that outlives the shutdown timeout is cancelled and finalized."""

    async def scenario() -> None:
        events: list[str] = []
        started = asyncio.Event()

        async def process(_job: TicketJob) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                events.append("job finalized")

        admission = JobAdmission(max_pending=1, max_running=1)
        service = _service(admission, process, shutdown_timeout_seconds=0.01)
        assert service.schedule(_job(1)) is True
        await asyncio.wait_for(started.wait(), 1)

        await service.aclose()

        assert events == ["job finalized"]
        assert (admission.pending, admission.running) == (0, 0)

    asyncio.run(scenario())


def test_queued_job_never_starts_after_close() -> None:
    """A reservation still waiting for a worker slot is dropped when the service closes."""

    async def scenario() -> None:
        started: list[int] = []
        release = asyncio.Event()

        async def process(job: TicketJob) -> None:
            started.append(job.ticket_id)
            await release.wait()

        admission = JobAdmission(max_pending=1, max_running=1)
        service = _service(admission, process)
        assert service.schedule(_job(1)) is True
        assert service.schedule(_job(2)) is True
        while not started:
            await asyncio.sleep(0)
        assert (admission.pending, admission.running) == (1, 1)

        closing = asyncio.create_task(service.aclose())
        while admission.pending:
            await asyncio.sleep(0)
        release.set()
        await closing

        assert started == [1]
        assert (admission.pending, admission.running) == (0, 0)

    asyncio.run(scenario())


def test_ingest_returns_shutting_down_after_lifespan_exit(tmp_path) -> None:
    secret = "test-secret"
    body = b'{"ticket_id":1}'
    settings = make_settings(str(tmp_path), secret=secret)

    async def process(_job: TicketJob) -> None:
        raise AssertionError("no work may start after shutdown")

    scheduler = _service(
        JobAdmission(
            max_pending=settings.admission.max_pending,
            max_running=settings.admission.max_running,
        ),
        process,
    )
    client = TestClient(create_app(settings, scheduler=scheduler))

    with client:
        pass

    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": sign_body(body, secret),
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Service is shutting down", "code": "shutting_down"}


def test_app_reads_the_history_its_scheduler_records_into() -> None:
    """Operator views show the scheduler's history, and a mismatched history is rejected."""
    settings = make_settings("/tmp/chronikwerk-test")

    async def process(_job: TicketJob) -> None:
        return None

    scheduler = _service(JobAdmission(max_pending=1, max_running=1), process)
    app = create_app(settings, scheduler=scheduler)

    assert app.state.history is scheduler.history
    with pytest.raises(ValueError, match="scheduler's history"):
        create_app(settings, scheduler=scheduler, history=JobHistory())
