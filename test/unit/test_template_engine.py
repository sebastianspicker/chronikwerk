from __future__ import annotations

from datetime import UTC, datetime

from zammad_pdf_archiver.adapters.pdf.template_engine import (
    _format_datetime,
    render_html,
)
from zammad_pdf_archiver.domain.snapshot_models import Snapshot, TicketMeta


def test_render_html_uses_default_template() -> None:
    html = render_html(Snapshot(ticket=TicketMeta(id=1, number="T1", title="Title"), articles=[]))
    assert "T1" in html


def test_format_datetime_uses_timezone() -> None:
    dt = datetime(2025, 6, 15, 14, 30, tzinfo=UTC)
    assert _format_datetime(dt, fmt="%Y-%m-%d %H:%M", timezone="Europe/Berlin") == (
        "2025-06-15 16:30"
    )
