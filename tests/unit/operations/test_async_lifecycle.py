"""Check cancellation and shutdown through public operational boundaries."""

from __future__ import annotations

import asyncio
import threading

import pytest

from chronikwerk.concurrency import run_sync_cancellation_safe
from chronikwerk.configuration.models import Settings
from chronikwerk.operations.admission import JobAdmission
from chronikwerk.operations.history import JobHistory
from chronikwerk.operations.job import TicketJob
from chronikwerk.operations.scheduling import TicketProcessor, TicketSchedulingService
from chronikwerk.web.app import create_app
from tests.support.settings_factory import make_settings


def _tracking_scheduler(
    settings: Settings, process: TicketProcessor, tasks: list[asyncio.Task[object]]
) -> TicketSchedulingService:
    """Build a real scheduler whose jobs expose their scheduler-owned task."""

    async def tracked(job: TicketJob) -> object:
        task = asyncio.current_task()
        assert task is not None
        tasks.append(task)
        return await process(job)

    return TicketSchedulingService(
        admission=JobAdmission(
            max_pending=settings.admission.max_pending,
            max_running=settings.admission.max_running,
        ),
        process_ticket=tracked,
        history=JobHistory(),
        shutdown_timeout_seconds=settings.admission.shutdown_timeout_seconds,
    )


@pytest.mark.parametrize("fails", [False, True])
def test_repeated_cancellation_waits_for_blocking_work(fails: bool) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def work() -> None:
        started.set()
        assert release.wait(5)
        finished.set()
        if fails:
            raise ValueError("synthetic worker failure")

    async def scenario() -> None:
        task = asyncio.create_task(run_sync_cancellation_safe(work))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert not finished.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        assert finished.is_set()
        if fails:
            assert "ValueError" in " ".join(error.value.__notes__)

    asyncio.run(scenario())


def test_lifespan_closes_resources_after_cancelled_job_finalizers(tmp_path) -> None:
    events: list[str] = []
    settings = make_settings(
        str(tmp_path), overrides={"admission": {"shutdown_timeout_seconds": 0.01}}
    )

    async def cleanup() -> None:
        events.append("client closed")

    started = asyncio.Event()

    async def job(_job: TicketJob) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            events.append("job finalized")

    async def scenario() -> None:
        tasks: list[asyncio.Task[object]] = []
        scheduler = _tracking_scheduler(settings, job, tasks)
        app = create_app(settings, scheduler=scheduler, cleanup=cleanup)
        async with app.router.lifespan_context(app):
            assert scheduler.schedule(TicketJob(ticket_id=1, payload={"ticket_id": 1}))
            await asyncio.wait_for(started.wait(), 1)
        (task,) = tasks
        assert task.cancelled()
        assert events == ["job finalized", "client closed"]

    asyncio.run(scenario())


def test_shutdown_waits_for_real_pipeline_tag_cleanup(tmp_path) -> None:
    from typing import cast

    from chronikwerk.archiving.processor import build_ticket_processor
    from chronikwerk.composition import _archive_runtime_options
    from chronikwerk.operations.guards import TicketGuards
    from chronikwerk.operations.history import JobHistory
    from chronikwerk.zammad.dto import TagList, Ticket
    from chronikwerk.zammad.gateway import AsyncZammadClient

    settings = make_settings(
        str(tmp_path), overrides={"admission": {"shutdown_timeout_seconds": 0.01}}
    )
    events = []
    started = asyncio.Event()

    class Client:
        closed = False
        processing_started = False

        async def get_ticket(self, ticket_id):
            return Ticket(id=ticket_id, number="synthetic")

        async def list_tags(self, _ticket_id):
            return TagList(["pdf:sign"])

        async def remove_tag(self, _ticket_id, tag):
            if not self.processing_started:
                self.processing_started = True
                started.set()
                await asyncio.Event().wait()
            await asyncio.sleep(0)
            assert not self.closed
            events.append(("remove", tag))

        async def add_tag(self, _ticket_id, tag):
            await asyncio.sleep(0)
            assert not self.closed
            events.append(("add", tag))

        async def aclose(self):
            self.closed = True
            events.append(("close", "client"))

    client = Client()

    async def scenario():
        process = build_ticket_processor(
            _archive_runtime_options(settings),
            client=cast(AsyncZammadClient, client),
            guards=TicketGuards(delivery_id_ttl_seconds=0),
            history=JobHistory(),
        )
        tasks: list[asyncio.Task[object]] = []
        scheduler = _tracking_scheduler(settings, process, tasks)
        app = create_app(settings, scheduler=scheduler, cleanup=client.aclose)
        async with app.router.lifespan_context(app):
            assert scheduler.schedule(TicketJob(ticket_id=731, payload={"ticket_id": 731}))
            await asyncio.wait_for(started.wait(), 1)
        (task,) = tasks
        assert task.cancelled()
        assert events == [
            ("remove", "pdf:processing"),
            ("remove", "pdf:signed"),
            ("add", "pdf:sign"),
            ("add", "pdf:error"),
            ("close", "client"),
        ]

    asyncio.run(scenario())
