from __future__ import annotations

from fastapi import FastAPI

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.observability.logger import configure_logging


def create_asgi_app() -> FastAPI:
    settings = load_settings()
    configure_logging(
        log_level=settings.observability.log_level,
        log_format=settings.observability.log_format,
    )
    return create_app(settings)


app = create_asgi_app()  # pragma: no cover
