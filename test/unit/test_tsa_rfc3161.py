from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.tsa_helpers import capturing_failing_async_client as _capturing_client
from test.support.tsa_helpers import make_signing as _make_signing
from test.support.tsa_helpers import tsa_req as _tsa_req
from zammad_pdf_archiver.config.settings import (
    SigningSettings,
    SigningTimestampRfc3161Settings,
    SigningTimestampSettings,
)
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError

pytest.importorskip("pyhanko", reason="TSA adapter requires pyHanko")

from zammad_pdf_archiver.adapters.signing.tsa_rfc3161 import build_timestamper  # noqa: E402


def _assert_tsa_status_raises(
    status: int, error_type: type[PermanentError | TransientError], match: str
) -> None:
    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing(tsa_url)
    timestamper = build_timestamper(signing)

    with respx.mock:
        respx.post(tsa_url).mock(return_value=httpx.Response(status))
        with pytest.raises(error_type, match=match):
            asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))


@pytest.mark.parametrize("status", [500, 503, 599])
def test_tsa_http_5xx_is_transient(status: int) -> None:
    _assert_tsa_status_raises(status, TransientError, "RFC3161 TSA returned HTTP")


@pytest.mark.parametrize("status", [400, 401, 404])
def test_tsa_http_4xx_is_permanent(status: int) -> None:
    _assert_tsa_status_raises(status, PermanentError, f"RFC3161 TSA returned HTTP {status}")


def test_tsa_wrong_content_type_is_permanent() -> None:
    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing(tsa_url)
    timestamper = build_timestamper(signing)

    with respx.mock:
        respx.post(tsa_url).mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "text/plain"},
                content=b"not a tsp response",
            )
        )
        with pytest.raises(PermanentError, match="unexpected Content-Type"):
            asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))


@pytest.mark.parametrize("trust_env", [False, True])
def test_tsa_http_client_respects_transport_trust_env(
    monkeypatch: pytest.MonkeyPatch, trust_env: bool
) -> None:
    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing(tsa_url)
    timestamper = build_timestamper(signing, trust_env=trust_env)

    captured: dict[str, object] = {}
    monkeypatch.setattr(httpx, "AsyncClient", _capturing_client(captured))

    with pytest.raises(TransientError, match="RFC3161 TSA returned HTTP 500"):
        asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))

    check(not captured["trust_env"] is not trust_env, "assertion failed")


def _make_signing_no_url() -> SigningSettings:
    """SigningSettings with timestamp enabled but no TSA URL."""
    return SigningSettings(
        enabled=False,
        timestamp=SigningTimestampSettings(
            enabled=True,
            rfc3161=SigningTimestampRfc3161Settings(tsa_url=None),
        ),
    )


def _make_signing_with_auth(user: str | None, password: str | None) -> SigningSettings:
    return SigningSettings(
        enabled=False,
        timestamp=SigningTimestampSettings(
            enabled=True,
            rfc3161=SigningTimestampRfc3161Settings(
                tsa_url="https://tsa.test/rfc3161",  # type: ignore[arg-type]
                user=user,
                password=password,  # type: ignore[arg-type]
            ),
        ),
    )


def test_build_timestamper_raises_when_tsa_url_missing() -> None:
    """Missing TSA URL raises a Pydantic validation error at settings construction time."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="tsa_url is missing"):
        _make_signing_no_url()


def test_build_timestamper_raises_when_user_without_password() -> None:
    """User set but no password → PermanentError."""
    signing = _make_signing_with_auth(user="user", password=None)
    with pytest.raises(Exception, match="TSA basic auth requires both"):
        build_timestamper(signing)


def test_build_timestamper_raises_when_password_without_user() -> None:
    """Password set but no user → PermanentError."""
    signing = _make_signing_with_auth(user=None, password=fake_credential("secret"))
    with pytest.raises(Exception, match="TSA basic auth requires both"):
        build_timestamper(signing)


def test_build_timestamper_returns_timestamper_with_valid_config() -> None:
    """build_timestamper succeeds with minimal valid config."""
    signing = _make_signing("https://tsa.test/rfc3161")
    timestamper = build_timestamper(signing)
    check(not not timestamper is not None, "assertion failed")


def test_tsa_malformed_response_body_is_permanent() -> None:
    """TSA returns correct Content-Type but garbage body → PermanentError."""
    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing(tsa_url)
    timestamper = build_timestamper(signing)

    with respx.mock:
        respx.post(tsa_url).mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "application/timestamp-reply"},
                content=b"\xff\xfe\x00\x01garbage not asn1",
            )
        )
        with pytest.raises(Exception, match="not a valid TimeStampResp"):
            asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))


def test_tsa_ca_bundle_path_is_used_as_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ca_bundle_path is passed as the 'verify' argument to httpx.AsyncClient."""
    from pathlib import Path

    tsa_url = "https://tsa.test/rfc3161"
    ca_bundle = Path("/fake/ca-bundle.pem")

    signing = SigningSettings(
        enabled=False,
        timestamp=SigningTimestampSettings(
            enabled=True,
            rfc3161=SigningTimestampRfc3161Settings(
                tsa_url=tsa_url,  # type: ignore[arg-type]
                ca_bundle_path=ca_bundle,
            ),
        ),
    )
    timestamper = build_timestamper(signing)
    captured: dict[str, object] = {}
    monkeypatch.setattr(httpx, "AsyncClient", _capturing_client(captured))

    with pytest.raises(TransientError):
        asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))

    check(not not captured["verify"] == str(ca_bundle), "assertion failed")
