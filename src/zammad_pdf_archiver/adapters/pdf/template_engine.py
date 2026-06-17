from __future__ import annotations

from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader, pass_context, select_autoescape

from zammad_pdf_archiver.domain.snapshot_models import Snapshot

_TEMPLATE_NAME = "default"
_TEMPLATE_FILE = "ticket.html"



@lru_cache(maxsize=1)
def _env_for() -> Environment:
    env = Environment(
        loader=PackageLoader("zammad_pdf_archiver", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    _register_filters(env)
    return env


def _register_filters(env: Environment) -> None:
    @pass_context
    def datetime_filter(ctx: dict[str, Any], value: Any, fmt: str = "%d.%m.%Y %H:%M") -> str:
        return _format_datetime(
            value,
            fmt=fmt,
            timezone=str(ctx.get("pdf_timezone") or "Europe/Berlin"),
        )

    env.filters["datetime"] = datetime_filter
    env.filters["format_dt_local"] = datetime_filter


def _format_datetime(value: Any, *, fmt: str, timezone: str) -> str:
    if value is None:
        return ""
    try:
        target_tz = ZoneInfo(timezone)
        localized = value.astimezone(target_tz)
        return localized.strftime(fmt)
    except Exception:
        return value.strftime(fmt) if hasattr(value, "strftime") else str(value)


def render_html(
    snapshot: Snapshot,
    *,
    locale: str = "de_DE",
    timezone: str = "Europe/Berlin",
) -> str:
    template = _env_for().get_template(f"{_TEMPLATE_NAME}/{_TEMPLATE_FILE}")
    return template.render(
        snapshot=snapshot,
        ticket=snapshot.ticket,
        articles=snapshot.articles,
        pdf_locale=locale,
        pdf_timezone=timezone,
    )
