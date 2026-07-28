"""Fetch ticket resources from Zammad with bounded retries and typed failures."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, NoReturn, TypedDict, Unpack

import httpx
from pydantic import TypeAdapter, ValidationError

from chronikwerk.adapters.http_util import (
    ResponseBodyTooLargeError,
    UnsupportedResponseEncodingError,
    buffered_response,
    pin_request_url,
    read_response_body_limited,
    timeouts_for,
)
from chronikwerk.adapters.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from chronikwerk.adapters.zammad.models import Article, TagList, Ticket
from chronikwerk.config.settings import ZammadConnection
from chronikwerk.config.transport import (
    validate_url_policy,
    validate_url_policy_async,
)

_MAX_RESPONSE_BODY_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _RetryPolicy:
    # "retry up to 3 times" => 1 initial attempt + 3 retries = 4 total attempts.
    max_retries: int = 3
    backoff_base_seconds: float = 0.2

    def backoff_seconds(self, attempt: int) -> float:
        """Return the exponential backoff delay in seconds for a zero-based retry attempt."""
        # attempt is 0-based for *retry count* (i.e., after the first failure).
        return self.backoff_base_seconds * (2**attempt)


@dataclass(frozen=True, slots=True)
class _ZammadRuntimeOptions:
    retry_policy: _RetryPolicy | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    http_client: httpx.AsyncClient | None = None
    allow_private_networks: bool = False


@dataclass(frozen=True, slots=True)
class ZammadClientOptions:
    """Configure the backwards-compatible direct client constructor."""

    timeout_seconds: float = 10.0
    verify_tls: bool = True
    trust_env: bool = False
    allow_insecure_http: bool = False
    allow_private_networks: bool = False


class _ZammadClientOptionKeywords(TypedDict, total=False):
    timeout_seconds: float
    verify_tls: bool
    trust_env: bool
    allow_insecure_http: bool
    allow_private_networks: bool


def _resolve_client_options(
    options: ZammadClientOptions | None,
    keyword_options: _ZammadClientOptionKeywords,
) -> ZammadClientOptions:
    if options is not None and keyword_options:
        names = ", ".join(sorted(keyword_options))
        raise TypeError(f"options cannot be combined with transport keywords: {names}")
    return options or ZammadClientOptions(**keyword_options)


def _resolve_connection_arguments(
    *,
    base_url: str | None,
    api_token: str | None,
    connection: ZammadConnection | None,
    options: ZammadClientOptions | None,
    keyword_options: _ZammadClientOptionKeywords,
) -> tuple[str | None, str | None, ZammadClientOptions | None]:
    if connection is None:
        return base_url, api_token, options
    if base_url is not None or api_token is not None:
        raise TypeError("connection cannot be combined with base_url or api_token")
    if options is not None or keyword_options:
        raise TypeError("connection cannot be combined with transport options")
    return (
        connection.origin,
        connection.api_token.get_secret_value(),
        ZammadClientOptions(
            timeout_seconds=connection.timeout_seconds,
            verify_tls=True,
            trust_env=connection.trust_environment,
            allow_private_networks=connection.allow_private_origin,
        ),
    )


def _require_safe_direct_transport(
    transport_options: ZammadClientOptions, runtime: _ZammadRuntimeOptions | None
) -> None:
    if runtime is None and (
        not transport_options.verify_tls or transport_options.allow_insecure_http
    ):
        raise ValueError(
            "Unsafe Zammad transport options require the private injected test runtime"
        )


@dataclass(frozen=True, slots=True)
class _RequestAttempt:
    retry_count: int
    max_attempts: int
    max_retries: int


class AsyncZammadClient:
    """Async HTTP client for the Zammad REST API with retry and error mapping."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        connection: ZammadConnection | None = None,
        options: ZammadClientOptions | None = None,
        _runtime: _ZammadRuntimeOptions | None = None,
        **keyword_options: Unpack[_ZammadClientOptionKeywords],
    ) -> None:
        base_url, api_token, options = _resolve_connection_arguments(
            base_url=base_url,
            api_token=api_token,
            connection=connection,
            options=options,
            keyword_options=keyword_options,
        )
        if base_url is None or api_token is None:
            raise TypeError("base_url and api_token are required when connection is not provided")
        url = httpx.URL(base_url)
        if not url.scheme or not url.host:
            raise ValueError("base_url must include scheme and host, e.g. https://zammad.example")

        # Ensure a trailing slash to make httpx base_url joining unambiguous.
        base_path = url.path.rstrip("/") + "/"
        self._base_url = url.copy_with(path=base_path)

        transport_options = _resolve_client_options(options, keyword_options)
        _require_safe_direct_transport(transport_options, _runtime)
        runtime = _runtime or _ZammadRuntimeOptions()
        self._sleep = runtime.sleep
        self._retry = runtime.retry_policy or _RetryPolicy()
        self._dns_timeout_seconds = min(5.0, float(transport_options.timeout_seconds))
        self._allow_insecure_http = transport_options.allow_insecure_http
        # An injected runtime may explicitly opt into private test fixtures;
        # production-owned clients use the safe constructor default.
        self._allow_private_networks = (
            transport_options.allow_private_networks or runtime.allow_private_networks
        )
        validate_url_policy(
            base_url,
            allow_insecure_http=transport_options.allow_insecure_http,
            allow_private_networks=self._allow_private_networks,
        )

        self._owns_http_client = runtime.http_client is None
        self._http = runtime.http_client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Token token={api_token}",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            timeout=timeouts_for(transport_options.timeout_seconds),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0,
            ),
            verify=transport_options.verify_tls,
            trust_env=transport_options.trust_env,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client if it was created by this instance."""
        if self._owns_http_client:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncZammadClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        await self.aclose()

    async def get_ticket(self, ticket_id: int) -> Ticket:
        """Fetch a single ticket by ID."""
        resp = await self._request_json("GET", f"api/v1/tickets/{ticket_id}")
        return Ticket.model_validate(resp)

    async def list_tags(self, ticket_id: int) -> TagList:
        """Fetch all tags for a ticket."""
        resp = await self._request_json(
            "GET",
            "api/v1/tags",
            params={"object": "Ticket", "o_id": str(ticket_id)},
        )

        # Zammad may return either a raw JSON array or an object wrapper depending on version.
        if isinstance(resp, dict) and "tags" in resp:
            tags_value = resp["tags"]
        else:
            tags_value = resp

        try:
            tags = TypeAdapter(list[str]).validate_python(tags_value)
        except ValidationError as exc:
            raise ClientError(
                f"Zammad tags response format unexpected for ticket {ticket_id}: {exc!s}"
            ) from exc
        return TagList(tags)

    async def add_tag(self, ticket_id: int, tag: str) -> None:
        """Add a tag to a ticket (idempotent)."""
        await self._request_json(
            "POST",
            "api/v1/tags/add",
            json={"object": "Ticket", "o_id": ticket_id, "item": tag},
        )

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        """Remove a tag from a ticket (idempotent)."""
        # Using POST keeps this client compatible with the documented `/tags/remove` endpoint.
        await self._request_json(
            "POST",
            "api/v1/tags/remove",
            json={"object": "Ticket", "o_id": ticket_id, "item": tag},
        )

    async def create_internal_article(
        self, ticket_id: int, subject: str, body_html: str
    ) -> Article:
        """Create an internal (non-customer-visible) article on a ticket."""
        resp = await self._request_json(
            "POST",
            "api/v1/ticket_articles",
            json={
                "ticket_id": ticket_id,
                "subject": subject,
                "body": body_html,
                "content_type": "text/html",
                "internal": True,
            },
            max_retries=0,
        )
        return Article.model_validate(resp)

    async def list_articles(self, ticket_id: int) -> list[Article]:
        """List all articles belonging to a ticket."""
        resp = await self._request_json("GET", f"api/v1/ticket_articles/by_ticket/{ticket_id}")
        items = TypeAdapter(list[dict[str, Any]]).validate_python(resp)
        return [Article.model_validate(item) for item in items]

    async def _request_json(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any | None = None,
        max_retries: int | None = None,
    ) -> Any:
        response = await self._request(
            method, path, params=params, json=json, max_retries=max_retries
        )
        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover
            raise ClientError(
                "Invalid JSON from Zammad "
                f"(status={response.status_code}) at {response.request.url!s}"
            ) from exc

    async def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        max_retries: int | None = None,
    ) -> httpx.Response:
        # Total attempts = 1 initial + max_retries
        retries = self._retry.max_retries if max_retries is None else max(0, max_retries)
        max_attempts = retries + 1
        retry_count = 0

        while True:
            try:
                response, retry_delay = await self._request_once(
                    method,
                    path,
                    params=params,
                    json=json,
                    headers=headers,
                    attempt=_RequestAttempt(
                        retry_count=retry_count,
                        max_attempts=max_attempts,
                        max_retries=retries,
                    ),
                )
            except httpx.TimeoutException as exc:
                retry_count = await self._retry_after_timeout_or_transport(
                    retry_count=retry_count,
                    max_attempts=max_attempts,
                    max_retries=retries,
                    exc=exc,
                    timeout_path=path,
                )
                continue
            except httpx.TransportError as exc:
                retry_count = await self._retry_after_timeout_or_transport(
                    retry_count=retry_count,
                    max_attempts=max_attempts,
                    max_retries=retries,
                    exc=exc,
                )
                continue

            if retry_delay is not None:
                await self._sleep(retry_delay)
                retry_count += 1
                continue
            if response is not None:
                return response

    async def _request_once(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None,
        json: Any | None,
        headers: dict[str, str] | None,
        attempt: _RequestAttempt,
    ) -> tuple[httpx.Response | None, float | None]:
        resolved_address = await validate_url_policy_async(
            str(self._base_url),
            allow_insecure_http=self._allow_insecure_http,
            allow_private_networks=self._allow_private_networks,
            timeout_seconds=self._dns_timeout_seconds,
        )
        request_url, pin_headers, extensions = pin_request_url(
            self._base_url.join(path),
            resolved_address,
        )
        request_headers = {**(headers or {}), **pin_headers}
        async with self._http.stream(
            method,
            request_url,
            params=params,
            json=json,
            headers=request_headers or None,
            extensions=extensions or None,
        ) as streamed_response:
            retry_delay = self._retry_delay_for_response(
                streamed_response,
                retry_count=attempt.retry_count,
                max_attempts=attempt.max_attempts,
                max_retries=attempt.max_retries,
            )
            if retry_delay is not None:
                return None, retry_delay
            if 200 <= streamed_response.status_code < 300:
                return await self._buffer_success_response(streamed_response), None
            self._raise_for_status(streamed_response)

    @staticmethod
    async def _buffer_success_response(response: httpx.Response) -> httpx.Response:
        try:
            content = await read_response_body_limited(
                response,
                max_bytes=_MAX_RESPONSE_BODY_BYTES,
            )
        except ResponseBodyTooLargeError as exc:
            raise ClientError(
                "Zammad response body exceeded the "
                f"{_MAX_RESPONSE_BODY_BYTES}-byte limit "
                f"(status={response.status_code}) at {response.request.url!s}"
            ) from exc
        except UnsupportedResponseEncodingError as exc:
            raise ClientError(
                "Zammad returned a compressed response despite "
                "Accept-Encoding: identity "
                f"(status={response.status_code}) at {response.request.url!s}"
            ) from exc
        return buffered_response(response, content)

    async def _retry_after_timeout_or_transport(
        self,
        *,
        retry_count: int,
        max_attempts: int,
        max_retries: int,
        exc: Exception,
        timeout_path: str | None = None,
    ) -> int:
        if retry_count >= max_retries:
            if isinstance(exc, httpx.TimeoutException):
                path = timeout_path or "<unknown>"
                raise ServerError(
                    f"Zammad API timeout after {max_attempts} attempts at {path}"
                ) from exc
            raise ServerError(f"Network error after {max_attempts} attempts") from exc
        await self._sleep(self._retry.backoff_seconds(retry_count))
        return retry_count + 1

    def _retry_delay_for_response(
        self,
        response: httpx.Response,
        *,
        retry_count: int,
        max_attempts: int,
        max_retries: int,
    ) -> float | None:
        status = response.status_code
        if status >= 500:
            if retry_count >= max_retries:
                raise ServerError(
                    f"Zammad server error (status={status}) after {max_attempts} attempts"
                )
            return self._retry.backoff_seconds(retry_count)
        if status == 429:
            if retry_count >= max_retries:
                raise RateLimitError(
                    f"Zammad rate limit (status=429) after {max_attempts} attempts"
                )
            retry_after = _parse_retry_after_seconds(response.headers.get("Retry-After"))
            return retry_after or self._retry.backoff_seconds(retry_count)
        return None

    def _raise_for_status(self, response: httpx.Response) -> NoReturn:
        status = response.status_code
        url = str(response.request.url)

        if status in (401, 403):
            raise AuthError(f"Zammad auth failed (status={status}) at {url}")
        if status == 404:
            raise NotFoundError(f"Zammad resource not found (status=404) at {url}")
        if status == 429:
            raise RateLimitError(f"Zammad rate limit (status=429) at {url}")
        if status >= 500:
            raise ServerError(f"Zammad server error (status={status}) at {url}")
        if status >= 400:
            raise ClientError(f"Zammad client error (status={status}) at {url}")

        raise ClientError(f"Unexpected Zammad HTTP status={status} at {url}")


def _parse_retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, 60)
