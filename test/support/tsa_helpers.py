"""Shared RFC3161 TSA test helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import respx

from zammad_pdf_archiver.config.settings import (
    SigningSettings,
    SigningTimestampRfc3161Settings,
    SigningTimestampSettings,
)


class CapturingDebugLog:
    def __init__(self) -> None:
        self.debug_events: list[tuple[str, dict[str, Any]]] = []

    def debug(self, event: str, **kwargs: Any) -> None:
        self.debug_events.append((event, kwargs))


def tsa_req() -> Any:
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


def make_signing(tsa_url: str) -> SigningSettings:
    return SigningSettings(
        enabled=False,
        timestamp=SigningTimestampSettings(
            enabled=True,
            rfc3161=SigningTimestampRfc3161Settings(tsa_url=tsa_url),  # type: ignore[arg-type]
        ),
    )


def mock_tsa_response(
    tsa_url: str,
    *,
    status_code: int = 200,
    content_type: str = "application/timestamp-reply",
    content: bytes = b"\x30\x03\x30\x01\x02",
) -> None:
    respx.post(tsa_url).mock(
        return_value=httpx.Response(
            status_code,
            headers={"Content-Type": content_type},
            content=content,
        )
    )


def capturing_failing_async_client(captured: dict[str, Any]) -> type:
    class _DummyClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(
                {
                    "trust_env": kwargs.get("trust_env"),
                    "verify": kwargs.get("verify"),
                }
            )

        async def __aenter__(self) -> _DummyClient:
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

    return _DummyClient


def mock_status_response(status_string: Any) -> MagicMock:
    mock_status_info = MagicMock()
    mock_status_info.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "status": MagicMock(native="rejection"),
            "status_string": status_string,
        }[key]
    )

    mock_resp = MagicMock()
    mock_resp.__getitem__ = MagicMock(side_effect=lambda key: {"status": mock_status_info}[key])
    return mock_resp
