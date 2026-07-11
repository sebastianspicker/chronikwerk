from __future__ import annotations

from zammad_pdf_archiver.i18n import catalog_keys, normalize_locale


def test_supported_catalogs_have_key_parity() -> None:
    assert catalog_keys("de-DE") == catalog_keys("en-GB")


def test_legacy_locale_forms_are_normalized() -> None:
    assert normalize_locale("de_DE") == "de-DE"
    assert normalize_locale("en_GB") == "en-GB"
    assert normalize_locale("unknown") == "de-DE"
