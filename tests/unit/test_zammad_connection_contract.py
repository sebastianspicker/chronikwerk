"""Verify the portable Zammad connection contract at its configuration boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr

from chronikwerk.adapters.zammad.client import AsyncZammadClient
from chronikwerk.config.load import load_settings
from chronikwerk.config.managed import ManagedConfigStore
from chronikwerk.config.settings import (
    ZAMMAD_CONNECTION_CONTRACT_VERSION,
    Settings,
    ZammadConnection,
)
from chronikwerk.config.validate import ConfigValidationError, validate_settings

_WEBHOOK_SECRET = "test-webhook-secret-0123456789abcdef"
_CANONICAL_AND_LEGACY_KEYS = (
    "ZAMMAD_ORIGIN",
    "ZAMMAD_API_TOKEN",
    "ZAMMAD_TIMEOUT_SECONDS",
    "ZAMMAD_ALLOW_PRIVATE_ORIGIN",
    "ZAMMAD_TRUST_ENV",
    "ZAMMAD__BASE_URL",
    "ZAMMAD__API_TOKEN",
    "ZAMMAD__TIMEOUT_SECONDS",
    "HARDENING__TRANSPORT__ALLOW_PRIVATE_NETWORKS",
    "HARDENING__TRANSPORT__TRUST_ENV",
)


@pytest.fixture(autouse=True)
def _clear_connection_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove portable and legacy Zammad aliases before each contract test."""
    for key in _CANONICAL_AND_LEGACY_KEYS:
        monkeypatch.delenv(key, raising=False)


def _yaml(tmp_path: Path, *, state_dir: Path | None = None) -> Path:
    """Write the minimal YAML fixture used by connection-loader scenarios."""
    path = tmp_path / "config.yaml"
    admin_lines = () if state_dir is None else ("admin:", f"  state_dir: {state_dir}")
    path.write_text(
        "\n".join(
            (
                "zammad:",
                "  base_url: https://zammad.from-yaml.example",
                "  api_token: yaml-token",
                f"  webhook_hmac_secret: {_WEBHOOK_SECRET}",
                "  timeout_seconds: 3",
                "storage:",
                f"  root: {tmp_path}",
                "hardening:",
                "  transport:",
                "    allow_private_networks: false",
                "    trust_env: false",
                *admin_lines,
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def test_contract_version_is_explicit() -> None:
    assert ZAMMAD_CONNECTION_CONTRACT_VERSION == 2


def test_canonical_environment_builds_the_connection_and_overrides_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ZAMMAD_ORIGIN", "https://Zammad.From-Env.example/")
    monkeypatch.setenv("ZAMMAD_API_TOKEN", "canonical-token")
    monkeypatch.setenv("ZAMMAD_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("ZAMMAD_ALLOW_PRIVATE_ORIGIN", "true")
    monkeypatch.setenv("ZAMMAD_TRUST_ENV", "1")

    connection = load_settings(config_path=_yaml(tmp_path)).zammad_connection

    assert connection.origin == "https://zammad.from-env.example"
    assert connection.api_root == "https://zammad.from-env.example/api/v1"
    assert connection.api_token.get_secret_value() == "canonical-token"
    assert connection.timeout_seconds == 12.5
    assert connection.allow_private_origin is True
    assert connection.trust_environment is True


def test_canonical_environment_supplies_required_connection_without_yaml_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("\n".join(("storage:", f"  root: {tmp_path}", "")), encoding="utf-8")
    monkeypatch.setenv("ZAMMAD_ORIGIN", "https://zammad.example")
    monkeypatch.setenv("ZAMMAD_API_TOKEN", "canonical-token")
    monkeypatch.setenv("ZAMMAD__WEBHOOK_HMAC_SECRET", _WEBHOOK_SECRET)

    settings = load_settings(config_path=config)

    assert settings.zammad_connection.origin == "https://zammad.example"
    assert settings.zammad_connection.api_token.get_secret_value() == "canonical-token"


def test_canonical_environment_precedes_managed_connection_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_dir = tmp_path / "admin"
    store = ManagedConfigStore(state_dir)
    store.stage(
        {"zammad": {"timeout_seconds": 30}},
        expected_revision=store.current_revision(),
        request_id="connection-contract",
    )
    monkeypatch.setenv("ZAMMAD_TIMEOUT_SECONDS", "12.5")

    settings = load_settings(config_path=_yaml(tmp_path, state_dir=state_dir))

    assert settings.zammad_connection.timeout_seconds == 12.5


def test_semantically_equal_canonical_and_legacy_environment_aliases_are_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ZAMMAD_ORIGIN", "https://zammad.example/")
    monkeypatch.setenv("ZAMMAD__BASE_URL", "https://ZAMMAD.example")
    monkeypatch.setenv("ZAMMAD_API_TOKEN", "same-token")
    monkeypatch.setenv("ZAMMAD__API_TOKEN", "same-token")
    monkeypatch.setenv("ZAMMAD_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("ZAMMAD__TIMEOUT_SECONDS", "10.0")
    monkeypatch.setenv("ZAMMAD_ALLOW_PRIVATE_ORIGIN", "yes")
    monkeypatch.setenv("HARDENING__TRANSPORT__ALLOW_PRIVATE_NETWORKS", "true")

    settings = load_settings(config_path=_yaml(tmp_path))

    assert settings.zammad_connection.origin == "https://zammad.example"
    assert settings.zammad_connection.allow_private_origin is True


def test_conflicting_aliases_fail_without_revealing_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "canonical-token-must-not-appear"
    monkeypatch.setenv("ZAMMAD_API_TOKEN", secret)
    monkeypatch.setenv("ZAMMAD__API_TOKEN", "different-token")

    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=_yaml(tmp_path))

    message = str(exc.value)
    assert "ZAMMAD_API_TOKEN" in message
    assert "ZAMMAD__API_TOKEN" in message
    assert secret not in message
    assert "different-token" not in message


def test_loader_rejects_whitespace_api_token_without_disclosing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = " token-with-whitespace "
    monkeypatch.setenv("ZAMMAD_API_TOKEN", token)

    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=_yaml(tmp_path))

    assert any(issue.path == "zammad.api_token" for issue in exc.value.issues)
    assert token not in str(exc.value)


@pytest.mark.parametrize(
    "origin",
    (
        "http://zammad.example",
        "https://user:password@zammad.example",
        "https://zammad.example/api/v1",
        "https://zammad.example?query=value",
        "https://zammad.example#fragment",
        "https://a..example",
        "https://bad-.example",
        "https://999.999.999.999",
        "https://[:]/",
        "https://zammad.example:0",
        "https://zammad.example:65536",
    ),
)
def test_connection_rejects_non_origin_urls_without_echoing_input(origin: str) -> None:
    with pytest.raises(ValueError) as exc:
        ZammadConnection(origin=origin, api_token=SecretStr("token"))

    assert origin not in str(exc.value)


@pytest.mark.parametrize("timeout", ("0", "-1", "not-a-number", "inf", "nan", "1e999"))
def test_canonical_timeout_parsing_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, timeout: str
) -> None:
    monkeypatch.setenv("ZAMMAD_TIMEOUT_SECONDS", timeout)

    with pytest.raises(ConfigValidationError):
        load_settings(config_path=_yaml(tmp_path))


@pytest.mark.parametrize("token", ("", "   ", "token with space"))
def test_connection_rejects_empty_or_whitespace_tokens_without_echoing_them(token: str) -> None:
    with pytest.raises(ValueError) as exc:
        ZammadConnection(origin="https://zammad.example", api_token=SecretStr(token))

    if token:
        assert token not in str(exc.value)


def test_connection_canonicalizes_hostname_and_ipv6_variants() -> None:
    hostname = ZammadConnection(origin="HTTPS://Zammad.Example./", api_token=SecretStr("token"))
    ipv6 = ZammadConnection(origin="https://[2001:0db8::1]:8443/", api_token=SecretStr("token"))

    assert hostname.origin == "https://zammad.example"
    assert ipv6.origin == "https://[2001:db8::1]:8443"


def test_connection_repr_redacts_the_api_token() -> None:
    token = "token-that-must-stay-redacted"
    connection = ZammadConnection(origin="https://zammad.example", api_token=SecretStr(token))

    assert token not in repr(connection)


@pytest.mark.parametrize("timeout", (float("inf"), float("nan"), 0.0, -1.0))
def test_connection_rejects_non_finite_or_non_positive_timeouts(timeout: float) -> None:
    with pytest.raises(ValueError):
        ZammadConnection(
            origin="https://zammad.example",
            api_token=SecretStr("token"),
            timeout_seconds=timeout,
        )


def test_canonical_boolean_parsing_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ZAMMAD_TRUST_ENV", "sometimes")

    with pytest.raises(ConfigValidationError):
        load_settings(config_path=_yaml(tmp_path))


def test_connection_drives_the_client_transport(tmp_path: Path) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example/",
                "api_token": "test-token",
                "webhook_hmac_secret": _WEBHOOK_SECRET,
                "timeout_seconds": 7.0,
            },
            "storage": {"root": tmp_path},
            "hardening": {"transport": {"allow_private_networks": True, "trust_env": True}},
        }
    )
    validate_settings(settings)
    connection = settings.zammad_connection
    client = AsyncZammadClient(connection=connection)

    assert connection.origin == "https://zammad.example"
    assert connection.api_root == "https://zammad.example/api/v1"
    assert client._dns_timeout_seconds == 5.0
    assert client._allow_insecure_http is False
    assert client._allow_private_networks is True
    assert client._http.headers["Authorization"] == "Token token=test-token"
    assert client._http.follow_redirects is False
    assert client._http.timeout.read == 7.0
    asyncio.run(client.aclose())


def test_explicit_insecure_transport_opt_in_drives_the_connection(tmp_path: Path) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "http://zammad.test:9090",
                "api_token": "test-token",
                "webhook_hmac_secret": _WEBHOOK_SECRET,
            },
            "storage": {"root": tmp_path},
            "hardening": {
                "transport": {
                    "allow_insecure_http": True,
                    "allow_private_networks": True,
                }
            },
        }
    )

    validate_settings(settings)
    connection = settings.zammad_connection
    client = AsyncZammadClient(connection=connection)

    assert connection.origin == "http://zammad.test:9090"
    assert connection.allow_insecure_http is True
    assert client._allow_insecure_http is True
    asyncio.run(client.aclose())


def test_unsafe_direct_transport_options_require_the_private_test_runtime() -> None:
    with pytest.raises(ValueError, match="private injected test runtime"):
        AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="token",
            verify_tls=False,
        )
