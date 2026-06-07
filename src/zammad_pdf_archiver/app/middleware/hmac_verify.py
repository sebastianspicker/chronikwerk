from __future__ import annotations

import hashlib
import hmac
from typing import Any

import structlog
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from zammad_pdf_archiver.app.constants import DELIVERY_ID_HEADER
from zammad_pdf_archiver.app.middleware.hmac_body import (
    read_body as _read_body,
)
from zammad_pdf_archiver.app.middleware.hmac_body import (
    replay_receive as _replay_receive,
)
from zammad_pdf_archiver.app.middleware.hmac_config import secret_bytes as _secret_bytes
from zammad_pdf_archiver.app.middleware.hmac_responses import (
    drain_and_send as _drain_and_send,
)
from zammad_pdf_archiver.app.middleware.hmac_responses import (
    forbidden as _forbidden,
)
from zammad_pdf_archiver.app.middleware.hmac_responses import (
    missing_delivery_id as _missing_delivery_id,
)
from zammad_pdf_archiver.app.middleware.hmac_responses import (
    send_json_response as _send_json_response,
)
from zammad_pdf_archiver.app.middleware.hmac_responses import (
    service_misconfigured as _service_misconfigured,
)
from zammad_pdf_archiver.app.middleware.hmac_signature import parse_signature
from zammad_pdf_archiver.app.protected_paths import is_ingest_protected_path
from zammad_pdf_archiver.config.settings import Settings

_log = structlog.get_logger(__name__)

_SIGNATURE_HEADER = "X-Hub-Signature"

_ALL_ALGORITHMS: dict[str, tuple[int, Any]] = {
    "sha1": (20, hashlib.sha1),
    "sha256": (32, hashlib.sha256),
}


def _requires_hmac_verification(scope: Scope) -> bool:
    return (
        scope["type"] == "http"
        and scope.get("method") == "POST"
        and is_ingest_protected_path(scope.get("path"))
    )


def _parse_signature(
    value: str,
    allowed_algorithms: dict[str, tuple[int, Any]] | None = None,
) -> tuple[bytes, type, str] | None:
    algos = allowed_algorithms if allowed_algorithms is not None else _ALL_ALGORITHMS
    return parse_signature(value, algos)


class HmacVerifyMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings | None) -> None:
        self.app = app
        self._secret = _secret_bytes(settings)
        if settings is not None:
            webhook = settings.hardening.webhook
            self._allow_unsigned = webhook.allow_unsigned
            self._allow_unsigned_when_no_secret = webhook.allow_unsigned_when_no_secret
            self._require_delivery_id = webhook.require_delivery_id
            if webhook.webhook_reject_sha1:
                self._allowed_algorithms = {
                    key: value for key, value in _ALL_ALGORITHMS.items() if key != "sha1"
                }
            else:
                self._allowed_algorithms = dict(_ALL_ALGORITHMS)
        else:
            self._allow_unsigned = False
            self._allow_unsigned_when_no_secret = False
            self._require_delivery_id = False
            self._allowed_algorithms = dict(_ALL_ALGORITHMS)

    @staticmethod
    def _warn_sha1_deprecation(algo_name: str) -> None:
        """Emit a deprecation warning when SHA-1 is used for HMAC verification."""
        if algo_name == "sha1":
            _log.warning(
                "hmac.sha1_deprecated",
                detail="Webhook signature uses SHA-1 which is deprecated. "
                "Configure the sender to use SHA-256. Set "
                "hardening.webhook.webhook_reject_sha1=true to reject SHA-1.",
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _requires_hmac_verification(scope):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)

        if await self._reject_missing_delivery_id(headers, scope, receive, send):
            return

        if not self._secret:
            await self._handle_missing_secret(scope, receive, send)
            return

        chunks = await self._verified_body_chunks(self._secret, headers, scope, receive, send)
        if chunks is None:
            return

        await self.app(scope, _replay_receive(chunks), send)

    async def _reject_missing_delivery_id(
        self, headers: Headers, scope: Scope, receive: Receive, send: Send
    ) -> bool:
        if not self._require_delivery_id:
            return False

        if (headers.get(DELIVERY_ID_HEADER) or "").strip():
            return False

        await _drain_and_send(_missing_delivery_id(), scope, receive, send)
        return True

    async def _handle_missing_secret(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Running without a secret requires an explicit second opt-in for local/test use.
        if self._allow_unsigned and self._allow_unsigned_when_no_secret:
            await self.app(scope, receive, send)
            return

        await _send_json_response(_service_misconfigured(), scope, receive, send)

    async def _verified_body_chunks(
        self, secret: bytes, headers: Headers, scope: Scope, receive: Receive, send: Send
    ) -> list[bytes] | None:
        signature_raw = headers.get(_SIGNATURE_HEADER)
        if not signature_raw:
            await _drain_and_send(_forbidden(), scope, receive, send)
            return None

        parsed = _parse_signature(signature_raw, self._allowed_algorithms)
        if parsed is None:
            await _drain_and_send(_forbidden(), scope, receive, send)
            return None

        signature, digest_ctor, algo_name = parsed
        self._warn_sha1_deprecation(algo_name)
        mac = hmac.new(secret, digestmod=digest_ctor)
        chunks, disconnected = await _read_body(receive, on_chunk=mac.update)
        if disconnected:
            await _send_json_response(_forbidden(), scope, receive, send)
            return None

        expected = mac.digest()
        if not hmac.compare_digest(signature, expected):
            await _send_json_response(_forbidden(), scope, receive, send)
            return None

        return chunks
