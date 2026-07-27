"""Verifies configuration and log redaction cover common secret encodings."""

from __future__ import annotations

import json

from pydantic import SecretStr

from chronikwerk.config.redact import (
    REDACTED_VALUE,
    redact_settings_dict,
    scrub_secrets_in_text,
)


def test_redact_settings_dict_redacts_explicit_secret_keys() -> None:
    raw = {
        "ZAMMAD__API_TOKEN": "tok",
        "ZAMMAD__WEBHOOK_HMAC_SECRET": "hmac",
        "PFX_PASSWORD": "pfx",
        "TSA_PASS": "tsa",
        "REDIS_URL": "redis://:pass@redis:6379/0",
        "nested": {
            "api_token": "tok2",
            "webhook_hmac_secret": "hmac2",
            "pfx_password": "pfx2",
            "tsa_pass": "tsa2",
        },
    }

    out = redact_settings_dict(raw)

    assert out["ZAMMAD__API_TOKEN"] == REDACTED_VALUE
    assert out["ZAMMAD__WEBHOOK_HMAC_SECRET"] == REDACTED_VALUE
    assert out["PFX_PASSWORD"] == REDACTED_VALUE
    assert out["TSA_PASS"] == REDACTED_VALUE
    assert out["REDIS_URL"] == REDACTED_VALUE
    assert out["nested"]["api_token"] == REDACTED_VALUE
    assert out["nested"]["webhook_hmac_secret"] == REDACTED_VALUE
    assert out["nested"]["pfx_password"] == REDACTED_VALUE
    assert out["nested"]["tsa_pass"] == REDACTED_VALUE

    # Input is not mutated.
    assert raw["ZAMMAD__API_TOKEN"] == "tok"


def test_redact_settings_dict_redacts_secretstr_values() -> None:
    raw = {"ok": 1, "secret": SecretStr("value")}
    out = redact_settings_dict(raw)
    assert out["secret"] == REDACTED_VALUE


def test_scrub_secrets_in_text_redacts_common_credential_patterns() -> None:
    text = (
        "boom Authorization: Bearer abc123 "
        "Token token=xyz "
        "api_token=apisecret123?token=querysecret456 "
        "client_token=compound-token private_key=compound-key"
    )
    out = scrub_secrets_in_text(text)
    assert "abc123" not in out
    assert "xyz" not in out
    assert "apisecret123" not in out
    assert "querysecret456" not in out
    assert "compound-token" not in out
    assert "compound-key" not in out
    assert REDACTED_VALUE in out


def test_scrub_secrets_in_text_redacts_quoted_credential_patterns() -> None:
    text = (
        "{'api_token': 'python-repr-secret', 'refresh_token': 'refresh-secret'} "
        'Authorization=\'Bearer auth-secret\' {"access_token": "json-secret"}'
    )

    out = scrub_secrets_in_text(text)

    for secret in ("python-repr-secret", "refresh-secret", "auth-secret", "json-secret"):
        assert secret not in out
    assert out.count(REDACTED_VALUE) == 4


def test_scrub_secrets_in_text_redacts_escaped_json_and_python_style_values() -> None:
    json_text = json.dumps(
        {
            "password": 'json-before"json-after\\json-tail',
            "client_secret": 'client-json-before"client-json-after\\client-json-tail',
        }
    )
    python_text = (
        r"{'client_token': 'python-before\'python-after\\python-tail', "
        r"'private_key': 'key-before\'key-after\\key-tail'}"
    )

    out = scrub_secrets_in_text(f"{json_text} {python_text}")

    for fragment in (
        "json-before",
        "json-after",
        "json-tail",
        "client-json-before",
        "client-json-after",
        "client-json-tail",
        "python-before",
        "python-after",
        "python-tail",
        "key-before",
        "key-after",
        "key-tail",
    ):
        assert fragment not in out
    assert out.count(REDACTED_VALUE) == 4


def test_scrub_secrets_in_text_redacts_prefixed_sensitive_quoted_keys() -> None:
    values_by_key = {
        "oauth_client_secret": "oauth-secret-value",
        "prefix_password_suffix": "password-value",
        "prefix_token_suffix": "token-value",
        "prefix_authorization_suffix": "authorization-value",
        "prefix_api_key_suffix": "api-key-value",
        "prefix_apikey_suffix": "apikey-value",
        "prefix_redis_url_suffix": "redis-url-value",
        "prefix_private_key_suffix": "private-key-value",
    }
    text = " ".join(f'"{key}": "{value}"' for key, value in values_by_key.items())

    out = scrub_secrets_in_text(text)

    for value in values_by_key.values():
        assert value not in out
    assert out.count(REDACTED_VALUE) == len(values_by_key)
    assert '"oauth_client_secret": "[redacted]"' in out


def test_scrub_secrets_in_text_redacts_sensitive_quoted_keys_with_arbitrary_syntax() -> None:
    values_by_key = {
        "my.password": "dotted-password",
        "9password": "numeric-password",
        "my password": "spaced-password",
        "pässword_token": "unicode-token",
    }
    text = " ".join(f'"{key}": "{value}"' for key, value in values_by_key.items())
    text += r' "my\u005fpassword": "escaped-key-password"'
    text += ' "my.dotted key": "visible-control"'

    out = scrub_secrets_in_text(text)

    for value in values_by_key.values():
        assert value not in out
    assert "escaped-key-password" not in out
    assert '"my.dotted key": "visible-control"' in out


def test_scrub_secrets_in_text_redacts_unterminated_quoted_sensitive_values() -> None:
    double_quoted = r'{"oauth_client_secret": "double-truncated-secret'
    single_quoted = r"{'private_key': 'single-truncated-secret"

    for text, fragment in (
        (double_quoted, "double-truncated-secret"),
        (single_quoted, "single-truncated-secret"),
    ):
        out = scrub_secrets_in_text(text)
        assert fragment not in out
        assert out.endswith(REDACTED_VALUE)
        assert ": " in out
