"""Initialize application dependencies from validated runtime settings."""

from __future__ import annotations

from fastapi import FastAPI

from chronikwerk.app.server import create_app
from chronikwerk.config.load import load_settings
from chronikwerk.config.settings import Settings
from chronikwerk.observability.logger import configure_logging


def build_runtime_application() -> tuple[Settings, FastAPI]:
    """Load configuration, configure logging, and construct the application."""
    settings = load_settings()
    configure_logging(
        log_level=settings.observability.log_level,
        log_format=settings.observability.log_format,
    )
    return settings, create_app(settings)
