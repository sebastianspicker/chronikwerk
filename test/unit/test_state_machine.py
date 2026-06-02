from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from test.support.checks import check
from zammad_pdf_archiver.domain.errors import TransientError
from zammad_pdf_archiver.domain.state_machine import (
    DONE_TAG,
    ERROR_TAG,
    PROCESSING_TAG,
    TRIGGER_TAG,
    apply_done,
    apply_error,
    apply_processing,
    should_process,
)


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    async def add_tag(self, ticket_id: int, tag: str) -> None:
        self.calls.append(("add_tag", ticket_id, tag))

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        self.calls.append(("remove_tag", ticket_id, tag))


class _FailingClient(_StubClient):
    def __init__(self, fail_call: tuple[str, int, str]) -> None:
        super().__init__()
        self.fail_call = fail_call

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        await super().remove_tag(ticket_id, tag)
        call = ("remove_tag", ticket_id, tag)
        if call == self.fail_call:
            raise TransientError("tag transition failed")


def _assert_calls(
    actual: list[tuple[str, int, str]],
    expected: list[tuple[str, int, str]],
) -> None:
    check(not not Counter(actual) == Counter(expected), "assertion failed")


def test_should_process_trigger_present_done_missing() -> None:
    check(
        not should_process([TRIGGER_TAG], trigger_tag=TRIGGER_TAG) is not True, "assertion failed"
    )


def test_should_process_trigger_missing() -> None:
    check(not should_process([], trigger_tag=TRIGGER_TAG) is not False, "assertion failed")


def test_should_process_done_present() -> None:
    check(
        not should_process([TRIGGER_TAG, DONE_TAG], trigger_tag=TRIGGER_TAG) is not False,
        "assertion failed",
    )


def test_should_process_returns_false_when_processing_tag_already_present() -> None:
    check(
        not should_process([TRIGGER_TAG, PROCESSING_TAG], trigger_tag=TRIGGER_TAG) is not False,
        "assertion failed",
    )


def test_should_process_processing_tag_alone() -> None:
    check(
        not should_process([PROCESSING_TAG], trigger_tag=TRIGGER_TAG) is not False,
        "assertion failed",
    )


def test_should_process_none_tags() -> None:
    check(not should_process(None, trigger_tag=TRIGGER_TAG) is not False, "assertion failed")


def test_should_process_custom_trigger_tag() -> None:
    check(
        not should_process(["pdf:archive"], trigger_tag="pdf:archive") is not True,
        "assertion failed",
    )
    check(
        not should_process([TRIGGER_TAG], trigger_tag="pdf:archive") is not False,
        "assertion failed",
    )


def test_should_process_require_trigger_disabled() -> None:
    check(
        not should_process([], trigger_tag=TRIGGER_TAG, require_trigger_tag=False) is not True,
        "assertion failed",
    )
    check(
        not should_process([DONE_TAG], trigger_tag=TRIGGER_TAG, require_trigger_tag=False)
        is not False,
        "assertion failed",
    )


def test_apply_processing_transitions() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_processing(client, 123, trigger_tag=TRIGGER_TAG)
        _assert_calls(
            client.calls,
            [
                ("remove_tag", 123, ERROR_TAG),
                ("remove_tag", 123, TRIGGER_TAG),
                ("add_tag", 123, PROCESSING_TAG),
            ],
        )

    asyncio.run(run())


def test_apply_processing_force_reprocess_removes_done_tag() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_processing(client, 123, trigger_tag=TRIGGER_TAG, force_reprocess=True)
        _assert_calls(
            client.calls,
            [
                ("remove_tag", 123, DONE_TAG),
                ("remove_tag", 123, ERROR_TAG),
                ("remove_tag", 123, TRIGGER_TAG),
                ("add_tag", 123, PROCESSING_TAG),
            ],
        )

    asyncio.run(run())


def test_apply_processing_respects_custom_trigger_tag() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_processing(client, 123, trigger_tag="pdf:archive")
        _assert_calls(
            client.calls,
            [
                ("remove_tag", 123, ERROR_TAG),
                ("remove_tag", 123, "pdf:archive"),
                ("add_tag", 123, PROCESSING_TAG),
            ],
        )

    asyncio.run(run())


def test_apply_done_transitions() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_done(client, 123, trigger_tag=TRIGGER_TAG)
        _assert_calls(
            client.calls,
            [
                ("remove_tag", 123, PROCESSING_TAG),
                ("remove_tag", 123, ERROR_TAG),
                ("remove_tag", 123, TRIGGER_TAG),
                ("add_tag", 123, DONE_TAG),
            ],
        )

    asyncio.run(run())


def test_apply_done_respects_custom_trigger_tag() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_done(client, 123, trigger_tag="pdf:archive")
        _assert_calls(
            client.calls,
            [
                ("remove_tag", 123, PROCESSING_TAG),
                ("remove_tag", 123, ERROR_TAG),
                ("remove_tag", 123, "pdf:archive"),
                ("add_tag", 123, DONE_TAG),
            ],
        )

    asyncio.run(run())


def test_apply_error_transitions_keep_trigger_default() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_error(client, 123, trigger_tag=TRIGGER_TAG)
        _assert_calls(
            client.calls,
            [
                ("remove_tag", 123, PROCESSING_TAG),
                ("remove_tag", 123, DONE_TAG),
                ("add_tag", 123, TRIGGER_TAG),
                ("add_tag", 123, ERROR_TAG),
            ],
        )

    asyncio.run(run())


def test_apply_error_transitions_drop_trigger() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_error(client, 123, trigger_tag=TRIGGER_TAG, keep_trigger=False)
        _assert_calls(
            client.calls,
            [
                ("remove_tag", 123, PROCESSING_TAG),
                ("remove_tag", 123, DONE_TAG),
                ("remove_tag", 123, TRIGGER_TAG),
                ("add_tag", 123, ERROR_TAG),
            ],
        )

    asyncio.run(run())


def test_apply_error_respects_custom_trigger_tag() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_error(client, 123, trigger_tag="pdf:archive")
        _assert_calls(
            client.calls,
            [
                ("remove_tag", 123, PROCESSING_TAG),
                ("remove_tag", 123, DONE_TAG),
                ("add_tag", 123, "pdf:archive"),
                ("add_tag", 123, ERROR_TAG),
            ],
        )

    asyncio.run(run())


def test_apply_processing_propagates_mid_sequence_transient_error() -> None:
    async def run() -> None:
        client = _FailingClient(("remove_tag", 123, TRIGGER_TAG))
        with pytest.raises(TransientError):
            await apply_processing(client, 123, trigger_tag=TRIGGER_TAG)
        _assert_calls(
            client.calls,
            [
                ("remove_tag", 123, ERROR_TAG),
                ("remove_tag", 123, TRIGGER_TAG),
            ],
        )

    asyncio.run(run())
