"""Fetch ticket resources from Zammad with bounded retries and typed failures."""

# DECISION: Governed by docs/adr/0006-zammad-outbound-transport-trust-boundary.md.
# Preserve the configured connection boundary and private test-runtime exception.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, NoReturn, TypedDict, Unpack

import httpx
from pydantic import TypeAdapter, ValidationError

from chronikwerk.adapters.zammad import _transport
from chronikwerk.adapters.zammad.errors import ClientError
from chronikwerk.adapters.zammad.models import Article, TagList, Ticket
from chronikwerk.config.settings import ZammadConnection

_MAX_RESPONSE_BODY_BYTES = _transport._MAX_RESPONSE_BODY_BYTES
_parse_retry_after_seconds = _transport._parse_retry_after_seconds
_RetryPolicy = _transport._RetryPolicy
_ZammadRuntimeOptions = _transport._ZammadRuntimeOptions
_ZammadTransport = _transport._ZammadTransport
_ZammadTransportOptions = _transport._ZammadTransportOptions


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


@dataclass(frozen=True, slots=True)
class _ConnectionArguments:
    """Inputs that determine whether direct or configured construction is used."""

    base_url: str | None
    api_token: str | None
    connection: ZammadConnection | None
    options: ZammadClientOptions | None
    keyword_options: _ZammadClientOptionKeywords


@dataclass(frozen=True, slots=True)
class _ResolvedConnectionArguments:
    """Direct client construction inputs after an optional configured connection resolves."""

    base_url: str | None
    api_token: str | None
    options: ZammadClientOptions | None


@dataclass(frozen=True, slots=True)
class _JsonRequest:
    """One JSON request delegated to the Zammad transport."""

    method: Literal["GET", "POST"]
    path: str
    params: dict[str, str] | None = None
    json: Any | None = None
    max_retries: int | None = None


def _resolve_client_options(
    options: ZammadClientOptions | None,
    keyword_options: _ZammadClientOptionKeywords,
) -> ZammadClientOptions:
    if options is not None and keyword_options:
        names = ", ".join(sorted(keyword_options))
        raise TypeError(f"options cannot be combined with transport keywords: {names}")
    return options or ZammadClientOptions(**keyword_options)


def _resolve_connection_arguments(
    arguments: _ConnectionArguments,
) -> _ResolvedConnectionArguments:
    if arguments.connection is None:
        return _ResolvedConnectionArguments(
            base_url=arguments.base_url,
            api_token=arguments.api_token,
            options=arguments.options,
        )
    if arguments.base_url is not None or arguments.api_token is not None:
        raise TypeError("connection cannot be combined with base_url or api_token")
    if arguments.options is not None or arguments.keyword_options:
        raise TypeError("connection cannot be combined with transport options")
    return _ResolvedConnectionArguments(
        base_url=arguments.connection.origin,
        api_token=arguments.connection.api_token.get_secret_value(),
        options=ZammadClientOptions(
            timeout_seconds=arguments.connection.timeout_seconds,
            verify_tls=True,
            trust_env=arguments.connection.trust_environment,
            allow_insecure_http=arguments.connection.allow_insecure_http,
            allow_private_networks=arguments.connection.allow_private_origin,
        ),
    )


def _require_safe_direct_transport(
    transport_options: ZammadClientOptions,
    runtime: _ZammadRuntimeOptions | None,
    *,
    configured_connection: bool,
) -> None:
    if runtime is not None or configured_connection:
        return
    if transport_options.verify_tls and not transport_options.allow_insecure_http:
        return
    raise ValueError("Unsafe Zammad transport options require the private injected test runtime")


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
        connection_arguments = _ConnectionArguments(
            base_url=base_url,
            api_token=api_token,
            connection=connection,
            options=options,
            keyword_options=keyword_options,
        )
        configured_connection = connection_arguments.connection is not None
        resolved_connection = _resolve_connection_arguments(connection_arguments)
        if resolved_connection.base_url is None or resolved_connection.api_token is None:
            raise TypeError("base_url and api_token are required when connection is not provided")
        url = httpx.URL(resolved_connection.base_url)
        if not url.scheme or not url.host:
            raise ValueError("base_url must include scheme and host, e.g. https://zammad.example")

        # Ensure a trailing slash to make httpx base_url joining unambiguous.
        base_path = url.path.rstrip("/") + "/"
        self._base_url = url.copy_with(path=base_path)

        transport_options = _resolve_client_options(
            resolved_connection.options, connection_arguments.keyword_options
        )
        _require_safe_direct_transport(
            transport_options,
            _runtime,
            configured_connection=configured_connection,
        )
        runtime = _runtime or _ZammadRuntimeOptions()
        self._transport = _ZammadTransport(
            _ZammadTransportOptions(
                base_url=self._base_url,
                policy_url=resolved_connection.base_url,
                api_token=resolved_connection.api_token,
                timeout_seconds=transport_options.timeout_seconds,
                verify_tls=transport_options.verify_tls,
                trust_env=transport_options.trust_env,
                allow_insecure_http=transport_options.allow_insecure_http,
                allow_private_networks=transport_options.allow_private_networks,
                max_response_body_bytes=_MAX_RESPONSE_BODY_BYTES,
            ),
            runtime,
        )

    @property
    def _dns_timeout_seconds(self) -> float:
        return self._transport.dns_timeout_seconds

    @property
    def _allow_insecure_http(self) -> bool:
        return self._transport.allow_insecure_http

    @property
    def _allow_private_networks(self) -> bool:
        return self._transport.allow_private_networks

    @property
    def _http(self) -> httpx.AsyncClient:
        return self._transport.http_client

    async def aclose(self) -> None:
        """Close the underlying HTTP client if it was created by this instance."""
        await self._transport.aclose()

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
        resp = await self._request_json(_JsonRequest("GET", f"api/v1/tickets/{ticket_id}"))
        return Ticket.model_validate(resp)

    async def list_tags(self, ticket_id: int) -> TagList:
        """Fetch all tags for a ticket."""
        resp = await self._request_json(
            _JsonRequest(
                "GET",
                "api/v1/tags",
                params={"object": "Ticket", "o_id": str(ticket_id)},
            )
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
            _JsonRequest(
                "POST",
                "api/v1/tags/add",
                json={"object": "Ticket", "o_id": ticket_id, "item": tag},
            )
        )

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        """Remove a tag from a ticket (idempotent)."""
        # Using POST keeps this client compatible with the documented `/tags/remove` endpoint.
        await self._request_json(
            _JsonRequest(
                "POST",
                "api/v1/tags/remove",
                json={"object": "Ticket", "o_id": ticket_id, "item": tag},
            )
        )

    async def create_internal_article(
        self, ticket_id: int, subject: str, body_html: str
    ) -> Article:
        """Create an internal (non-customer-visible) article on a ticket."""
        resp = await self._request_json(
            _JsonRequest(
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
        )
        return Article.model_validate(resp)

    async def list_articles(self, ticket_id: int) -> list[Article]:
        """List all articles belonging to a ticket."""
        resp = await self._request_json(
            _JsonRequest("GET", f"api/v1/ticket_articles/by_ticket/{ticket_id}")
        )
        items = TypeAdapter(list[dict[str, Any]]).validate_python(resp)
        return [Article.model_validate(item) for item in items]

    async def _request_json(self, request: _JsonRequest) -> Any:
        return await self._transport.request_json(
            request.method,
            request.path,
            params=request.params,
            json=request.json,
            max_retries=request.max_retries,
        )

    def _raise_for_status(self, response: httpx.Response) -> NoReturn:
        self._transport.raise_for_status(response)
