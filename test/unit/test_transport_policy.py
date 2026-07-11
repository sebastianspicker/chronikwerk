from __future__ import annotations

import socket

import pytest

from zammad_pdf_archiver.config.transport import validate_url_policy
from zammad_pdf_archiver.domain.errors import PermanentError


@pytest.mark.parametrize("url", ["https://127.0.0.1/api", "https://[::1]/api", "https://192.0.2.1/api"])
def test_non_global_ip_literals_are_rejected(url: str) -> None:
    with pytest.raises(PermanentError):
        validate_url_policy(url)


def test_private_network_override_allows_internal_literal() -> None:
    validate_url_policy(
        "http://127.0.0.1:8080/health",
        allow_insecure_http=True,
        allow_private_networks=True,
    )


def test_dns_resolution_is_fail_closed_and_checks_every_address(monkeypatch) -> None:
    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
        ]

    monkeypatch.setattr("zammad_pdf_archiver.config.transport.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(PermanentError):
        validate_url_policy("https://example.test", resolve_dns=True)


def test_dns_resolution_error_is_rejected(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("resolver unavailable")

    monkeypatch.setattr("zammad_pdf_archiver.config.transport.socket.getaddrinfo", fail)
    with pytest.raises(PermanentError, match="could not be resolved"):
        validate_url_policy("https://example.test", resolve_dns=True)
