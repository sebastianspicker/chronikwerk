"""Schedule bounded ticket work through one application-owned service."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

import structlog

from chronikwerk.operations.admission import AdmissionClosed, JobAdmission
from chronikwerk.operations.history import JobHistory
from chronikwerk.operations.job import TicketJob

TicketProcessor = Callable[[TicketJob], Awaitable[object]]

log = structlog.get_logger(__name__)


class TicketScheduler(Protocol):
    """Scheduling capability consumed by HTTP and administration delivery."""

    def schedule(self, job: TicketJob) -> bool:
        """Schedule one admitted delivery when capacity is available."""
        ...

    def schedule_batch(self, jobs: list[TicketJob]) -> bool:
        """Schedule an all-or-nothing group of admitted deliveries."""
        ...

    def schedule_retry(self, *, ticket_id: int, request_id: str | None) -> bool:
        """Schedule one forced operator retry when capacity is available."""
        ...

    @property
    def accepting(self) -> bool:
        """Return whether new work may still be scheduled."""
        ...

    @property
    def admission(self) -> JobAdmission:
        """Return the bounded admission whose counters operators observe."""
        ...

    @property
    def history(self) -> JobHistory:
        """Return the job history this scheduler records into."""
        ...

    async def aclose(self) -> None:
        """Stop accepting work and drain or cancel tracked jobs."""
        ...


class TicketSchedulingService:
    """Own in-process task admission, tracked tasks, and shutdown for one application.

    All methods must run on the single event loop that serves the application;
    the tracked task set is per instance and is not shared across loops. After
    ``aclose`` the service stays closed: restarting it is unsupported because
    ``JobAdmission`` latches its closing state.
    """

    def __init__(
        self,
        *,
        admission: JobAdmission,
        process_ticket: TicketProcessor,
        history: JobHistory,
        shutdown_timeout_seconds: float,
    ) -> None:
        self._admission = admission
        self._process_ticket = process_ticket
        self._history = history
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._accepting = True
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def accepting(self) -> bool:
        """Return whether new work may still be scheduled."""
        return self._accepting

    @property
    def admission(self) -> JobAdmission:
        """Return the bounded admission whose counters operators observe."""
        return self._admission

    @property
    def history(self) -> JobHistory:
        """Return the job history this scheduler records into."""
        return self._history

    async def aclose(self) -> None:
        """Stop accepting, close admission, then drain or cancel tracked jobs.

        Accepting is cleared before the first await so no request can reserve
        capacity while admission is closing and escape the drain.
        """
        self._accepting = False
        await self._admission.close()
        tasks = {task for task in self._tasks if not task.done()}
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self._shutdown_timeout_seconds,
            )
        except TimeoutError:
            for task in tasks:
                task.cancel()
            # Cancellation finalizers bound their own cleanup time.
            await asyncio.gather(*tasks, return_exceptions=True)

    def schedule(self, job: TicketJob) -> bool:
        """Reserve and create one ticket job."""
        return self.schedule_batch([job])

    def schedule_batch(self, jobs: list[TicketJob]) -> bool:
        """Reserve and create a complete job group without partial admission."""
        if not self._accepting:
            return False
        if jobs and not self._admission.try_reserve(len(jobs)):
            return False

        created = 0
        try:
            for job in jobs:
                self._create_task(job)
                created += 1
        except Exception:
            self._admission.cancel_reservation(len(jobs) - created)
            raise
        return True

    def schedule_retry(self, *, ticket_id: int, request_id: str | None) -> bool:
        """Schedule a forced retry without delivery-ID deduplication."""
        return self.schedule(
            TicketJob(
                ticket_id=ticket_id,
                payload={"ticket_id": ticket_id},
                request_id=request_id,
                force_reprocess=True,
            )
        )

    def _create_task(self, job: TicketJob) -> None:
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._history.record(
            "accepted",
            job.ticket_id,
            delivery_id=job.delivery_id,
            request_id=job.request_id,
        )

    async def _run(self, job: TicketJob) -> None:
        bound: dict[str, object] = {"ticket_id": job.ticket_id}
        if job.delivery_id:
            bound["delivery_id"] = job.delivery_id
        try:
            await self._admission.acquire()
        except AdmissionClosed:
            log.info("ingest.job_cancelled_during_shutdown", ticket_id=job.ticket_id)
            return

        structlog.contextvars.bind_contextvars(**bound)
        try:
            await self._process_ticket(job)
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception(
                "ingest.process_ticket_unhandled_error",
                ticket_id=job.ticket_id,
                delivery_id=job.delivery_id,
            )
        finally:
            structlog.contextvars.unbind_contextvars(*bound.keys())
            await self._admission.release()
