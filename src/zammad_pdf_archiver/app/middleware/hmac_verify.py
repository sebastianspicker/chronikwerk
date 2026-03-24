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
from zammad_pdf_archiver.app.constants import DELIVERY_ID_HEADER, INGEST_PROTECTED_PATHS
from zammad_pdf_archiver.app.responses import api_error
from zammad_pdf_archiver.config.settings import Settings

_log = structlog.get_logger(__name__)

_SIGNATURE_HEADER = "X-Hub-Signature"

_ALL_ALGORITHMS: dict[str, tuple[int, Any]] = {
    "sha1": (hashlib.sha1().digest_size, hashlib.sha1),
    "sha256": (hashlib.sha256().digest_size, hashlib.sha256),
}


def _build_allowed_algorithms(*, reject_sha1: bool) -> dict[str, tuple[int, Any]]:
    if reject_sha1:
        return {k: v for k, v in _ALL_ALGORITHMS.items() if k != "sha1"}
    return dict(_ALL_ALGORITHMS)


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
    If disconnected is True, client disconnected during read (Bug #28: treat as auth failure).
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
            self._allowed_algorithms = _build_allowed_algorithms(
                reject_sha1=webhook.webhook_reject_sha1,
            )
        else:
            self._allow_unsigned = False
            self._allow_unsigned_when_no_secret = False
            self._require_delivery_id = False
            self._allowed_algorithms = dict(_ALL_ALGORITHMS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method") != "POST" or scope.get("path") not in INGEST_PROTECTED_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)

        # Require non-empty delivery id (missing or blank header → 400).
        if self._require_delivery_id and not (headers.get(DELIVERY_ID_HEADER) or "").strip():
            await drain_stream(receive)
            await _missing_delivery_id()(scope, receive, send)
            return

        if not self._secret:
            # Bug #12: require explicit allow_unsigned_when_no_secret to allow without secret.
            if self._allow_unsigned and self._allow_unsigned_when_no_secret:
                await self.app(scope, receive, send)
            else:
                await _service_misconfigured()(scope, receive, send)
            return

        signature_raw = headers.get(_SIGNATURE_HEADER)
        if not signature_raw:
            await drain_stream(receive)
            await _forbidden()(scope, receive, send)
            return

        parsed = _parse_signature(signature_raw, self._allowed_algorithms)
        if parsed is None:
            await drain_stream(receive)
            await _forbidden()(scope, receive, send)
            return

        signature, digest_ctor, algo_name = parsed
        if algo_name == "sha1":
            _log.warning(
                "hmac.sha1_deprecated",
                detail="Webhook signature uses SHA-1 which is deprecated. "
                "Configure the sender to use SHA-256. Set "
                "hardening.webhook.webhook_reject_sha1=true to reject SHA-1.",
            )
        mac = hmac.new(self._secret, digestmod=digest_ctor)
        chunks, disconnected = await _read_body(receive, on_chunk=mac.update)
        if disconnected:
            await _forbidden()(scope, receive, send)
            return

        expected = mac.digest()
        if not hmac.compare_digest(signature, expected):
            await _forbidden()(scope, receive, send)
            return

        await self.app(scope, _replay_receive(chunks), send)
