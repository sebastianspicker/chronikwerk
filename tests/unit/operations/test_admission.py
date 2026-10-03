"""Verify the public bounded-admission lifecycle."""

from __future__ import annotations

import asyncio

from chronikwerk.operations.admission import AdmissionClosed, JobAdmission
from chronikwerk.operations.history import JobHistory
from chronikwerk.operations.job import TicketJob
from chronikwerk.operations.scheduling import TicketSchedulingService


def test_admission_is_bounded_and_closes_queued_work() -> None:
    async def exercise() -> None:
        admission = JobAdmission(max_pending=1, max_running=1)
        assert admission.try_reserve()
        assert admission.try_reserve()
        assert not admission.try_reserve()
        await admission.acquire()
        assert (admission.pending, admission.running) == (1, 1)
        await admission.close()
        await admission.release()
        try:
            await admission.acquire()
        except AdmissionClosed:
            return
        raise AssertionError("closed admission accepted queued work")

    asyncio.run(exercise())


def test_operator_retry_preserves_archive_job_metadata() -> None:
    async def exercise() -> None:
        captured: list[TicketJob] = []

        async def process(job: TicketJob) -> None:
            captured.append(job)

        scheduler = TicketSchedulingService(
            admission=JobAdmission(max_pending=1, max_running=1),
            process_ticket=process,
            history=JobHistory(),
            shutdown_timeout_seconds=1.0,
        )

        assert scheduler.schedule_retry(ticket_id=123, request_id="request-1")
        current = asyncio.current_task()
        await asyncio.gather(*(task for task in asyncio.all_tasks() if task is not current))

        assert captured == [
            TicketJob(
                ticket_id=123,
                payload={"ticket_id": 123},
                delivery_id=None,
                request_id="request-1",
                force_reprocess=True,
            )
        ]

    asyncio.run(exercise())
