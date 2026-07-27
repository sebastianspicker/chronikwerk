"""Verifies template date formatting honors the configured timezone."""

from __future__ import annotations

from datetime import UTC, datetime

from chronikwerk.adapters.pdf.template_engine import (
    _format_datetime,
)


def test_format_datetime_uses_timezone() -> None:
    dt = datetime(2025, 6, 15, 14, 30, tzinfo=UTC)
    assert _format_datetime(dt, fmt="%Y-%m-%d %H:%M", timezone="Europe/Berlin") == (
        "2025-06-15 16:30"
    )
