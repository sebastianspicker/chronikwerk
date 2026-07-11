"""Package-backed Jinja rendering for administration pages."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from zammad_pdf_archiver.i18n import normalize_locale, translate


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        loader=PackageLoader("zammad_pdf_archiver", "templates/admin"),
        autoescape=select_autoescape(["html", "xml"]),
    )


def render_admin_template(template_name: str, *, locale: str, **context: Any) -> str:
    selected = normalize_locale(locale)

    def gettext(key: str, **values: Any) -> str:
        return translate(selected, key, **values)

    return (
        _environment()
        .get_template(template_name)
        .render(
            locale=selected,
            _=gettext,
            **context,
        )
    )
