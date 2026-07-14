"""Unit tests for http_util shared helpers."""

from __future__ import annotations

import httpx

from zammad_pdf_archiver.adapters.http_util import (
    pin_request_url,
    timeouts_for,
)


def test_timeouts_for_uses_capped_connect_timeout() -> None:
    t = timeouts_for(30.0)
    assert t.connect == 5.0
    assert t.read == 30.0
    assert t.write == 30.0
    assert t.pool == 5.0


def test_timeouts_for_short_timeout_uses_total_as_connect() -> None:
    t = timeouts_for(2.0)
    assert t.connect == 2.0
    assert t.read == 2.0


def test_pin_request_url_preserves_host_and_tls_identity() -> None:
    url, headers, extensions = pin_request_url(
        httpx.URL("https://example.com:8443/api"),
        "93.184.216.34",
    )

    assert str(url) == "https://93.184.216.34:8443/api"
    assert headers == {"Host": "example.com:8443"}
    assert extensions == {"sni_hostname": "example.com"}
