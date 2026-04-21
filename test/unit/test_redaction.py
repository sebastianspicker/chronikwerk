from __future__ import annotations

from pydantic import SecretStr

from zammad_pdf_archiver.config.redact import (
    REDACTED_VALUE,
    redact_settings_dict,
    scrub_secrets_in_text,
)


def test_redact_settings_dict_redacts_explicit_secret_keys() -> None:
    raw = {
        "ZAMMAD_API_TOKEN": "tok",
        "WEBHOOK_HMAC_SECRET": "hmac",
        "PFX_PASSWORD": "pfx",
        "TSA_PASS": "tsa",
        "nested": {
            "api_token": "tok2",
            "webhook_hmac_secret": "hmac2",
            "pfx_password": "pfx2",
            "tsa_pass": "tsa2",
        },
    }

    out = redact_settings_dict(raw)

    assert out["ZAMMAD_API_TOKEN"] == REDACTED_VALUE
    assert out["WEBHOOK_HMAC_SECRET"] == REDACTED_VALUE
    assert out["PFX_PASSWORD"] == REDACTED_VALUE
    assert out["TSA_PASS"] == REDACTED_VALUE
    assert out["nested"]["api_token"] == REDACTED_VALUE
    assert out["nested"]["webhook_hmac_secret"] == REDACTED_VALUE
    assert out["nested"]["pfx_password"] == REDACTED_VALUE
    assert out["nested"]["tsa_pass"] == REDACTED_VALUE

    # Input is not mutated.
    assert raw["ZAMMAD_API_TOKEN"] == "tok"


def test_redact_settings_dict_redacts_secretstr_values() -> None:
    raw = {"ok": 1, "secret": SecretStr("value")}
    out = redact_settings_dict(raw)
    assert out["secret"] == REDACTED_VALUE


def test_scrub_secrets_in_text_redacts_common_credential_patterns() -> None:
    text = (
        "boom Authorization: Bearer abc123 "
        "Token token=xyz "
        "api_token=apisecret123?token=querysecret456"
    )
    out = scrub_secrets_in_text(text)
    assert "abc123" not in out
    assert "xyz" not in out
    assert "apisecret123" not in out
    assert "querysecret456" not in out
    assert REDACTED_VALUE in out


def test_redact_settings_dict_redacts_redis_url_key() -> None:
    raw = {"redis_url": "redis://:s3cret@redis.local:6379/0", "host": "example.com"}
    out = redact_settings_dict(raw)
    assert out["redis_url"] == REDACTED_VALUE
    assert out["host"] == "example.com"


def test_scrub_secrets_in_text_redacts_redis_url_credentials() -> None:
    text = "Connecting to redis://:s3cret@redis.local:6379/0 failed"
    out = scrub_secrets_in_text(text)
    assert "s3cret" not in out
    assert "redis://" in out
    assert "redis.local" in out

    text2 = "Using rediss://admin:p4ssw0rd@redis.local:6380/1"
    out2 = scrub_secrets_in_text(text2)
    assert "p4ssw0rd" not in out2
    assert "admin" not in out2
    assert "rediss://" in out2
    assert "redis.local" in out2
