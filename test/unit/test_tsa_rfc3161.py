from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx

from test.support.checks import check
from test.support.credentials import fake_credential
from zammad_pdf_archiver.config.settings import (
    SigningSettings,
    SigningTimestampRfc3161Settings,
    SigningTimestampSettings,
)
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError

pytest.importorskip("pyhanko", reason="TSA adapter requires pyHanko")

import zammad_pdf_archiver.adapters.signing.tsa_rfc3161 as tsa_module  # noqa: E402
from zammad_pdf_archiver.adapters.signing.tsa_rfc3161 import build_timestamper  # noqa: E402


class _CapturingLog:
    def __init__(self) -> None:
        self.debug_events: list[tuple[str, dict[str, Any]]] = []

    def debug(self, event: str, **kwargs: Any) -> None:
        self.debug_events.append((event, kwargs))


def _tsa_req() -> Any:
    from asn1crypto import tsp

    return tsp.TimeStampReq(
        {
            "version": 1,
            "message_imprint": {
                "hash_algorithm": {"algorithm": "sha256"},
                "hashed_message": b"\x00" * 32,
            },
            "nonce": 1,
            "cert_req": True,
        }
    )


def _make_signing(tsa_url: str) -> SigningSettings:
    return SigningSettings(
        enabled=False,
        timestamp=SigningTimestampSettings(
            enabled=True,
            rfc3161=SigningTimestampRfc3161Settings(tsa_url=tsa_url),  # type: ignore[arg-type]
        ),
    )


@pytest.mark.parametrize("status", [500, 503, 599])
def test_tsa_http_5xx_is_transient(status: int) -> None:
    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing(tsa_url)
    timestamper = build_timestamper(signing)

    with respx.mock:
        respx.post(tsa_url).mock(return_value=httpx.Response(status))
        with pytest.raises(TransientError, match="RFC3161 TSA returned HTTP"):
            asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))


@pytest.mark.parametrize("status", [400, 401, 404])
def test_tsa_http_4xx_is_permanent(status: int) -> None:
    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing(tsa_url)
    timestamper = build_timestamper(signing)

    with respx.mock:
        respx.post(tsa_url).mock(return_value=httpx.Response(status))
        with pytest.raises(PermanentError, match=f"RFC3161 TSA returned HTTP {status}"):
            asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))


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

    captured: dict[str, Any] = {}

    class _DummyAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["trust_env"] = kwargs.get("trust_env")

        async def __aenter__(self) -> _DummyAsyncClient:
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        async def post(
            self,
            url: str,
            *,
            content: bytes,
            headers: dict[str, str],
            **kwargs: Any,
        ) -> httpx.Response:  # noqa: ARG002
            return httpx.Response(500)

    monkeypatch.setattr(httpx, "AsyncClient", _DummyAsyncClient)

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
    captured: dict[str, Any] = {}

    class _DummyClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["verify"] = kwargs.get("verify")

        async def __aenter__(self) -> _DummyClient:
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        async def post(
            self, url: str, *, content: bytes, headers: dict[str, str], **kw: Any
        ) -> httpx.Response:
            return httpx.Response(500)

    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)

    with pytest.raises(TransientError):
        asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))

    check(not not captured["verify"] == str(ca_bundle), "assertion failed")


def test_tsa_rejection_status_raises_permanent() -> None:
    """TSA response with rejected status raises PermanentError with status in message."""
    from unittest.mock import MagicMock, patch

    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing(tsa_url)
    timestamper = build_timestamper(signing)

    mock_status_info = MagicMock()
    mock_status_info.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "status": MagicMock(native="rejection"),
            "status_string": MagicMock(native="Not authorized"),
        }[key]
    )

    mock_resp = MagicMock()
    mock_resp.__getitem__ = MagicMock(side_effect=lambda key: {"status": mock_status_info}[key])

    with respx.mock:
        respx.post(tsa_url).mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "application/timestamp-reply"},
                content=b"\x30\x03\x30\x01\x02",  # minimal placeholder body
            )
        )
        from asn1crypto import tsp as _tsp

        with patch.object(_tsp.TimeStampResp, "load", return_value=mock_resp):
            with pytest.raises(PermanentError, match="rejected"):
                asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))


def test_tsa_status_string_access_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed TSA status detail is logged while preserving PermanentError behavior."""
    from unittest.mock import MagicMock, patch

    class _BrokenStatusString:
        @property
        def native(self) -> str:
            raise ValueError("status string unavailable")

    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing(tsa_url)
    timestamper = build_timestamper(signing)
    capture = _CapturingLog()
    monkeypatch.setattr(tsa_module, "log", capture)

    mock_status_info = MagicMock()
    mock_status_info.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "status": MagicMock(native="rejection"),
            "status_string": _BrokenStatusString(),
        }[key]
    )

    mock_resp = MagicMock()
    mock_resp.__getitem__ = MagicMock(side_effect=lambda key: {"status": mock_status_info}[key])

    with respx.mock:
        respx.post(tsa_url).mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "application/timestamp-reply"},
                content=b"\x30\x03\x30\x01\x02",
            )
        )
        from asn1crypto import tsp as _tsp

        with patch.object(_tsp.TimeStampResp, "load", return_value=mock_resp):
            with pytest.raises(PermanentError, match="rejection"):
                asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))

    check(not not len(capture.debug_events) == 1, "assertion failed")
    event, fields = capture.debug_events[0]
    check(not not event == "tsa_status_string_unavailable", "assertion failed")
    check(not not set(fields) == {"exc_info"}, "assertion failed")
    check(
        not not isinstance(capture.debug_events[0][1]["exc_info"], ValueError), "assertion failed"
    )
