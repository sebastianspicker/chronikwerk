"""Verify runtime bootstrap wiring without starting the ASGI server."""

from __future__ import annotations

from fastapi import FastAPI

from chronikwerk import bootstrap
from chronikwerk.config.settings import Settings
from tests.support.settings_factory import make_settings


def test_build_runtime_application_wires_validated_settings(monkeypatch, tmp_path) -> None:
    settings = make_settings(str(tmp_path))
    application = FastAPI()
    logging_calls: list[dict[str, str | None]] = []
    app_settings: list[Settings] = []

    monkeypatch.setattr(bootstrap, "load_settings", lambda: settings)
    monkeypatch.setattr(
        bootstrap,
        "configure_logging",
        lambda **kwargs: logging_calls.append(kwargs),
    )

    def create_app(candidate: Settings) -> FastAPI:
        app_settings.append(candidate)
        return application

    monkeypatch.setattr(bootstrap, "create_app", create_app)

    loaded_settings, loaded_application = bootstrap.build_runtime_application()

    assert loaded_settings is settings
    assert loaded_application is application
    assert app_settings == [settings]
    assert logging_calls == [
        {
            "log_level": settings.observability.log_level,
            "log_format": settings.observability.log_format,
        }
    ]
