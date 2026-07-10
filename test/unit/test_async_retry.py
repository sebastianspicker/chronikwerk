from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from zammad_pdf_archiver.app.jobs.async_retry import async_retry


def test_succeeds_first_attempt() -> None:
    """Operation succeeds immediately, no retries."""

    async def run() -> None:
        op = AsyncMock(return_value=42)

        with patch("zammad_pdf_archiver.app.jobs.async_retry.asyncio.sleep") as mock_sleep:
            result = await async_retry(op, max_retries=3)

        assert result == 42
        op.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    asyncio.run(run())


def test_succeeds_after_retries() -> None:
    """Operation fails twice then succeeds on the third attempt."""

    async def run() -> None:
        op = AsyncMock(side_effect=[RuntimeError("fail 1"), RuntimeError("fail 2"), "ok"])

        with patch("zammad_pdf_archiver.app.jobs.async_retry.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            result = await async_retry(op, max_retries=3)

        assert result == "ok"
        assert op.await_count == 3
        assert mock_sleep.await_count == 2

    asyncio.run(run())


def test_exhausts_retries() -> None:
    """Operation fails max_retries+1 times, raises last exception."""

    async def run() -> None:
        errors = [RuntimeError(f"fail {i}") for i in range(4)]
        op = AsyncMock(side_effect=errors)

        with (
            patch("zammad_pdf_archiver.app.jobs.async_retry.asyncio.sleep") as mock_sleep,
            pytest.raises(RuntimeError, match="fail 3"),
        ):
            mock_sleep.return_value = None
            await async_retry(op, max_retries=3)

        assert op.await_count == 4
        assert mock_sleep.await_count == 3

    asyncio.run(run())


def test_backoff_timing() -> None:
    """Verify sleep delays follow backoff_base * backoff_factor^attempt."""

    async def run() -> None:
        errors = [RuntimeError(f"fail {i}") for i in range(4)]
        op = AsyncMock(side_effect=errors)

        with (
            patch("zammad_pdf_archiver.app.jobs.async_retry.asyncio.sleep") as mock_sleep,
            pytest.raises(RuntimeError),
        ):
            mock_sleep.return_value = None
            # defaults: backoff_base=0.5, backoff_factor=2.0, max_retries=3
            await async_retry(op, max_retries=3, backoff_base=0.5, backoff_factor=2.0)

        # attempt 0 -> sleep(0.5 * 2.0^0) = 0.5
        # attempt 1 -> sleep(0.5 * 2.0^1) = 1.0
        # attempt 2 -> sleep(0.5 * 2.0^2) = 2.0
        # attempt 3 -> final failure, no sleep
        expected_delays = [0.5, 1.0, 2.0]
        actual_delays = [call.args[0] for call in mock_sleep.await_args_list]
        assert actual_delays == expected_delays

    asyncio.run(run())


def test_zero_retries() -> None:
    """max_retries=0 means only one attempt, no retries."""

    async def run() -> None:
        op = AsyncMock(side_effect=RuntimeError("only once"))

        with (
            patch("zammad_pdf_archiver.app.jobs.async_retry.asyncio.sleep") as mock_sleep,
            pytest.raises(RuntimeError, match="only once"),
        ):
            mock_sleep.return_value = None
            await async_retry(op, max_retries=0)

        op.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    asyncio.run(run())


def test_negative_retries_rejected() -> None:
    async def run() -> None:
        op = AsyncMock()

        with pytest.raises(ValueError, match="max_retries must be >= 0"):
            await async_retry(op, max_retries=-1)

        op.assert_not_called()

    asyncio.run(run())


def test_custom_backoff() -> None:
    """Custom backoff_base and backoff_factor values work correctly."""

    async def run() -> None:
        errors = [RuntimeError(f"fail {i}") for i in range(3)]
        op = AsyncMock(side_effect=errors)

        with (
            patch("zammad_pdf_archiver.app.jobs.async_retry.asyncio.sleep") as mock_sleep,
            pytest.raises(RuntimeError),
        ):
            mock_sleep.return_value = None
            await async_retry(op, max_retries=2, backoff_base=1.0, backoff_factor=3.0)

        # attempt 0 -> sleep(1.0 * 3.0^0) = 1.0
        # attempt 1 -> sleep(1.0 * 3.0^1) = 3.0
        # attempt 2 -> final failure, no sleep
        expected_delays = [1.0, 3.0]
        actual_delays = [call.args[0] for call in mock_sleep.await_args_list]
        assert actual_delays == expected_delays

    asyncio.run(run())


def test_preserves_exception_type() -> None:
    """The original exception type (e.g. ValueError) is preserved, not wrapped."""

    async def run() -> None:
        op = AsyncMock(side_effect=ValueError("bad value"))

        with (
            patch("zammad_pdf_archiver.app.jobs.async_retry.asyncio.sleep") as mock_sleep,
            pytest.raises(ValueError, match="bad value"),
        ):
            mock_sleep.return_value = None
            await async_retry(op, max_retries=2)

    asyncio.run(run())
