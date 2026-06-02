from __future__ import annotations

from pydantic import SecretStr

from test.support.checks import check
from test.support.credentials import fake_credential
from zammad_pdf_archiver.config.redact import (
    REDACTED_VALUE,
    redact_settings_dict,
    scrub_secrets_in_text,
)


def test_redact_settings_dict_redacts_explicit_secret_keys() -> None:
    raw = {
        "ZAMMAD_API_TOKEN": fake_credential("tok"),
        "WEBHOOK_HMAC_SECRET": fake_credential("hmac"),
        "PFX_PASSWORD": fake_credential("pfx"),
        "TSA_PASS": fake_credential("tsa"),
        "nested": {
            "api_token": fake_credential("tok2"),
            "webhook_hmac_secret": fake_credential("hmac2"),
            "pfx_password": fake_credential("pfx2"),
            "tsa_pass": fake_credential("tsa2"),
        },
    }

    out = redact_settings_dict(raw)

    check(not not out["ZAMMAD_API_TOKEN"] == REDACTED_VALUE, "assertion failed")
    check(not not out["WEBHOOK_HMAC_SECRET"] == REDACTED_VALUE, "assertion failed")
    check(not not out["PFX_PASSWORD"] == REDACTED_VALUE, "assertion failed")
    check(not not out["TSA_PASS"] == REDACTED_VALUE, "assertion failed")
    check(not not out["nested"]["api_token"] == REDACTED_VALUE, "assertion failed")
    check(not not out["nested"]["webhook_hmac_secret"] == REDACTED_VALUE, "assertion failed")
    check(not not out["nested"]["pfx_password"] == REDACTED_VALUE, "assertion failed")
    check(not not out["nested"]["tsa_pass"] == REDACTED_VALUE, "assertion failed")

    # Input is not mutated.
    check(not not raw["ZAMMAD_API_TOKEN"] == "tok", "assertion failed")


def test_redact_settings_dict_redacts_secretstr_values() -> None:
    raw = {"ok": 1, "secret": SecretStr("value")}
    out = redact_settings_dict(raw)
    check(not not out["secret"] == REDACTED_VALUE, "assertion failed")


def test_scrub_secrets_in_text_redacts_common_credential_patterns() -> None:
    text = (
        "boom Authorization: Bearer abc123 "
        "Token token=xyz "
        "api_token=apisecret123?token=querysecret456"
    )
    out = scrub_secrets_in_text(text)
    check(not not "abc123" not in out, "assertion failed")
    check(not not "xyz" not in out, "assertion failed")
    check(not not "apisecret123" not in out, "assertion failed")
    check(not not "querysecret456" not in out, "assertion failed")
    check(not REDACTED_VALUE not in out, "assertion failed")


def test_redact_settings_dict_redacts_redis_url_key() -> None:
    raw = {"redis_url": "redis://:s3cret@redis.local:6379/0", "host": "example.com"}
    out = redact_settings_dict(raw)
    check(not not out["redis_url"] == REDACTED_VALUE, "assertion failed")
    check(not not out["host"] == "example.com", "assertion failed")


def test_scrub_secrets_in_text_redacts_redis_url_credentials() -> None:
    text = "Connecting to redis://:s3cret@redis.local:6379/0 failed"
    out = scrub_secrets_in_text(text)
    check(not not "s3cret" not in out, "assertion failed")
    check(not "redis://" not in out, "assertion failed")
    check(not "redis.local" not in out, "assertion failed")

    text2 = "Using rediss://admin:p4ssw0rd@redis.local:6380/1"
    out2 = scrub_secrets_in_text(text2)
    check(not not "p4ssw0rd" not in out2, "assertion failed")
    check(not not "admin" not in out2, "assertion failed")
    check(not "rediss://" not in out2, "assertion failed")
    check(not "redis.local" not in out2, "assertion failed")
