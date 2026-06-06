"""Shared helpers for CLI command tests."""

from __future__ import annotations

import argparse
import json

from test.support.settings_factory import make_settings
from zammad_pdf_archiver import cli

REDIS_WORKFLOW = {"workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}}
INPROCESS_WORKFLOW = {"workflow": {"execution_backend": "inprocess"}}


def args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def settings(tmp_path, *, workflow: dict | None = None):
    return make_settings(str(tmp_path), overrides=workflow)


def patch_load_settings(monkeypatch, loaded_settings) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: loaded_settings)


def patch_load_error(monkeypatch, error: Exception) -> None:
    def _raise():
        raise error

    monkeypatch.setattr(cli, "load_settings", _raise)


def captured_json(capsys):
    return json.loads(capsys.readouterr().out)
