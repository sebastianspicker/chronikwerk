"""Verifies outbound transport rejects unsafe URLs and DNS results."""

from __future__ import annotations

import asyncio
import socket
import threading

import pytest

from chronikwerk.config.transport import (
    validate_url_policy,
    validate_url_policy_async,
)
from chronikwerk.domain.errors import PermanentError, TransientError


@pytest.mark.parametrize(
    "url", ["https://127.0.0.1/api", "https://[::1]/api", "https://192.0.2.1/api"]
)
def test_non_global_ip_literals_are_rejected(url: str) -> None:
    with pytest.raises(PermanentError):
        validate_url_policy(url)


def test_private_network_override_allows_internal_literal() -> None:
    validate_url_policy(
        "http://127.0.0.1:8080/health",
        allow_insecure_http=True,
        allow_private_networks=True,
    )


def test_embedded_url_credentials_are_rejected_without_leaking_them() -> None:
    secret = "super-secret-password"

    with pytest.raises(PermanentError) as exc:
        validate_url_policy(f"https://alice:{secret}@example.com/api")

    assert "must not include credentials" in str(exc.value)
    assert secret not in str(exc.value)


def test_dns_resolution_is_fail_closed_and_checks_every_address(monkeypatch) -> None:
    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
        ]

    monkeypatch.setattr("chronikwerk.config.transport.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(PermanentError):
        validate_url_policy("https://example.test", resolve_dns=True)


def test_temporary_dns_resolution_error_is_transient(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise socket.gaierror(socket.EAI_AGAIN, "resolver unavailable")

    monkeypatch.setattr("chronikwerk.config.transport.socket.getaddrinfo", fail)
    with pytest.raises(TransientError, match="temporarily unavailable"):
        validate_url_policy("https://example.test", resolve_dns=True)


def test_unknown_hostname_is_permanent(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "name not known")

    monkeypatch.setattr("chronikwerk.config.transport.socket.getaddrinfo", fail)
    with pytest.raises(PermanentError, match="could not be resolved"):
        validate_url_policy("https://example.test", resolve_dns=True)


def test_async_dns_resolution_has_a_bounded_timeout(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocked(*_args, **_kwargs):
        started.set()
        release.wait(timeout=2.0)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr("chronikwerk.config.transport.socket.getaddrinfo", blocked)

    async def run() -> None:
        try:
            with pytest.raises(TransientError, match="DNS resolution timed out"):
                await validate_url_policy_async(
                    "https://example.test",
                    timeout_seconds=0.01,
                )
            assert started.is_set()
        finally:
            release.set()

    asyncio.run(run())
