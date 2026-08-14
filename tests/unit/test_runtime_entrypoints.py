"""Characterize the public runtime and ASGI entrypoint contracts."""

from __future__ import annotations

import runpy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from chronikwerk import bootstrap, runtime


def test_runtime_main_starts_configured_application(monkeypatch) -> None:
    application = object()
    settings = SimpleNamespace(
        server=SimpleNamespace(host="127.0.0.1", port=8123),
    )
    run_server = Mock()
    monkeypatch.setattr(runtime, "build_runtime_application", lambda: (settings, application))
    monkeypatch.setattr(runtime.uvicorn, "run", run_server)

    assert runtime.main() == 0
    run_server.assert_called_once_with(
        application,
        host="127.0.0.1",
        port=8123,
        log_config=None,
    )


def test_asgi_module_binds_bootstrapped_settings_and_application(monkeypatch) -> None:
    application = object()
    settings = object()
    monkeypatch.setattr(
        bootstrap,
        "build_runtime_application",
        lambda: (settings, application),
    )

    module_globals = runpy.run_module("chronikwerk.asgi")

    assert module_globals["settings"] is settings
    assert module_globals["app"] is application


def test_module_entrypoint_exits_with_runtime_result(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "main", lambda: 23)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("chronikwerk.__main__", run_name="__main__")

    assert exc_info.value.code == 23
