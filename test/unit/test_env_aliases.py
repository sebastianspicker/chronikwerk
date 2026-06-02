from __future__ import annotations

from pathlib import Path

import pytest

from test.support.checks import check
from zammad_pdf_archiver.config.load import load_settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "CONFIG_PATH",
        # Required keys
        "ZAMMAD_BASE_URL",
        "ZAMMAD_API_TOKEN",
        "STORAGE_ROOT",
        # Webhook auth default (fail closed unless secret configured)
        "WEBHOOK_HMAC_SECRET",
        "HARDENING_WEBHOOK_ALLOW_UNSIGNED",
        "HARDENING_WEBHOOK_ALLOW_UNSIGNED_WHEN_NO_SECRET",
        # Current env keys
        "PDF_TEMPLATE_VARIANT",
        "PDF_LOCALE",
        "PDF_TIMEZONE",
        "SIGNING_REASON",
        "SIGNING_LOCATION",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_env_aliases_from_env_example_are_honored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_env(monkeypatch)

    monkeypatch.setenv("ZAMMAD_BASE_URL", "https://zammad.example.local")
    monkeypatch.setenv("ZAMMAD_API_TOKEN", "test-token")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("HARDENING_WEBHOOK_ALLOW_UNSIGNED", "true")
    monkeypatch.setenv("HARDENING_WEBHOOK_ALLOW_UNSIGNED_WHEN_NO_SECRET", "true")

    monkeypatch.setenv("PDF_TEMPLATE_VARIANT", "minimal")
    monkeypatch.setenv("PDF_LOCALE", "en_US")
    monkeypatch.setenv("PDF_TIMEZONE", "UTC")
    monkeypatch.setenv("SIGNING_REASON", "Unit Test Reason")
    monkeypatch.setenv("SIGNING_LOCATION", "Unit Test Location")

    settings = load_settings()
    check(not not settings.pdf.template_variant == "minimal", "assertion failed")
    check(not not settings.pdf.locale == "en_US", "assertion failed")
    check(not not settings.pdf.timezone == "UTC", "assertion failed")
    check(not not settings.signing.pades.reason == "Unit Test Reason", "assertion failed")
    check(not not settings.signing.pades.location == "Unit Test Location", "assertion failed")
