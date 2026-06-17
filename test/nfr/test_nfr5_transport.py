from __future__ import annotations

import pytest

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError, validate_settings


def test_nfr5_rejects_plain_http_base_url() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "http://zammad.local",
                "api_token": "t",
                "webhook_hmac_secret": "s",
            },
            "storage": {"root": "/mnt/archive"},
        }
    )

    with pytest.raises(ConfigValidationError):
        validate_settings(settings)
