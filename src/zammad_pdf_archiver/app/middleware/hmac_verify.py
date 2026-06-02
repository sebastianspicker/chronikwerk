from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from typing import Any

import structlog
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from zammad_pdf_archiver.adapters.http_util import drain_stream
from zammad_pdf_archiver.app.constants import DELIVERY_ID_HEADER
from zammad_pdf_archiver.app.protected_paths import is_ingest_protected_path
from zammad_pdf_archiver.app.responses import api_error
from zammad_pdf_archiver.config.settings import Settings

_log = structlog.get_logger(__name__)

_SIGNATURE_HEADER = "X-Hub-Signature"

_ALL_ALGORITHMS: dict[str, tuple[int, Any]] = {
    "sha1": (20, hashlib.sha1),
    "sha256": (32, hashlib.sha256),
}


def _secret_bytes(settings: Settings | None) -> bytes | None:
    if settings is None:
        return None

    secret = settings.zammad.webhook_hmac_secret
    if secret is not None:
        value = secret.get_secret_value()
        if value and value.strip():
            return value.encode("utf-8")

    # Backwards-compatible: allow existing shared secret config.
    legacy = settings.server.webhook_shared_secret
    if legacy is not None:
        value = legacy.get_secret_value()
        if value and value.strip():
            return value.encode("utf-8")

    return None


def _forbidden() -> JSONResponse:
    return api_error(403, "forbidden", code="forbidden")


def _service_misconfigured() -> JSONResponse:
    # Fail closed: running without webhook auth is almost always a production footgun.
    return api_error(503, "webhook_auth_not_configured", code="webhook_auth_not_configured")


def _missing_delivery_id() -> JSONResponse:
    return api_error(400, "missing_delivery_id", code="missing_delivery_id")


async def _send_json_response(
    response: JSONResponse, scope: Scope, receive: Receive, send: Send
) -> None:
    await response(scope, receive, send)


async def _drain_and_send(
    response: JSONResponse, scope: Scope, receive: Receive, send: Send
) -> None:
    await drain_stream(receive)
    await _send_json_response(response, scope, receive, send)


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
    """Parse X-Hub-Signature (sha1=<hex> or sha256=<hex>).
    Returns (digest_bytes, digest_constructor, algorithm_name) or None."""
    algos = allowed_algorithms if allowed_algorithms is not None else _ALL_ALGORITHMS
    try:
        algorithm, hex_digest = value.strip().split("=", 1)
    except ValueError:
        return None

    algo_lower = algorithm.strip().lower()
    if algo_lower not in algos:
        return None

    expected_size, digest_ctor = algos[algo_lower]
    hex_digest = hex_digest.strip()
    try:
        digest = bytes.fromhex(hex_digest)
    except ValueError:
        return None

    if len(digest) != expected_size:
        return None

    return (digest, digest_ctor, algo_lower)


async def _read_body(
    receive: Receive, *, on_chunk: Callable[[bytes], None]
) -> tuple[list[bytes], bool]:
    """
    Read body and update MAC. Returns (chunks, disconnected).
    If disconnected is True, the caller treats the incomplete body as an auth failure.
    """
    chunks: list[bytes] = []
    while True:
        message = await receive()
        message_type = message.get("type")
        if message_type == "http.disconnect":
            return (chunks, True)
        if message_type != "http.request":
            continue

        body = message.get("body", b"")
        if body:
            chunks.append(body)
            on_chunk(body)

        if not message.get("more_body", False):
            return (chunks, False)


def _replay_receive(chunks: list[bytes]) -> Receive:
    idx = 0

    async def receive() -> Message:
        """Replay buffered body chunks as ASGI http.request messages."""
        nonlocal idx
        if idx >= len(chunks):
            return {"type": "http.request", "body": b"", "more_body": False}

        body = chunks[idx]
        idx += 1
        return {"type": "http.request", "body": body, "more_body": idx < len(chunks)}

    return receive


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
