from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, NoReturn, TypeVar

import httpx

from zammad_pdf_archiver.adapters.http_util import timeouts_for
from zammad_pdf_archiver.adapters.zammad.client_responses import (
    article_list_from_response,
    raise_for_status,
    tag_list_from_response,
)
from zammad_pdf_archiver.adapters.zammad.client_responses import (
    require_tag_mutation_success as _require_tag_mutation_success,
)
from zammad_pdf_archiver.adapters.zammad.client_retry import MAX_RETRIES as _MAX_RETRIES
from zammad_pdf_archiver.adapters.zammad.client_retry import (
    parse_retry_after_seconds as _parse_retry_after_seconds,
)
from zammad_pdf_archiver.adapters.zammad.client_retry import (
    retry_after_timeout_or_transport,
    retry_delay_for_response,
)
from zammad_pdf_archiver.adapters.zammad.errors import ClientError
from zammad_pdf_archiver.adapters.zammad.models import Article, TagList, Ticket

_T = TypeVar("_T")

__all__ = [
    "AsyncZammadClient",
    "ZammadClientTransportOptions",
    "_parse_retry_after_seconds",
]


@dataclass(frozen=True)
class ZammadClientTransportOptions:
    timeout_seconds: float = 10.0
    verify_tls: bool = True
    trust_env: bool = False


class AsyncZammadClient:
    """Async HTTP client for the Zammad REST API with retry and error mapping."""

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        transport: ZammadClientTransportOptions | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        transport = transport or ZammadClientTransportOptions()
        url = httpx.URL(base_url)
        if not url.scheme or not url.host:
            raise ValueError("base_url must include scheme and host, e.g. https://zammad.example")

        # Ensure a trailing slash to make httpx base_url joining unambiguous.
        base_path = url.path.rstrip("/") + "/"
        self._base_url = url.copy_with(path=base_path)

        self._sleep = sleep

        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Token token={api_token}",
                "Accept": "application/json",
            },
            timeout=timeouts_for(transport.timeout_seconds),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0,
            ),
            verify=transport.verify_tls,
            trust_env=transport.trust_env,
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

        return tag_list_from_response(resp, ticket_id=ticket_id)

    async def add_tag(self, ticket_id: int, tag: str) -> None:
        """Add a tag to a ticket (idempotent)."""
        resp = await self._request_json(
            "POST",
            "api/v1/tags/add",
            json={"object": "Ticket", "o_id": ticket_id, "item": tag},
        )
        _require_tag_mutation_success(resp, operation="add", ticket_id=ticket_id, tag=tag)

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        """Remove a tag from a ticket (idempotent)."""
        # Using POST keeps this client compatible with the documented `/tags/remove` endpoint.
        resp = await self._request_json(
            "POST",
            "api/v1/tags/remove",
            json={"object": "Ticket", "o_id": ticket_id, "item": tag},
        )
        _require_tag_mutation_success(resp, operation="remove", ticket_id=ticket_id, tag=tag)

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
        )
        return Article.model_validate(resp)

    async def list_articles(self, ticket_id: int) -> list[Article]:
        """List all articles belonging to a ticket."""
        resp = await self._request_json("GET", f"api/v1/ticket_articles/by_ticket/{ticket_id}")
        return article_list_from_response(resp)

    async def get_attachment_content(
        self, ticket_id: int, article_id: int, attachment_id: int
    ) -> bytes:
        """Download attachment binary.
        GET /api/v1/ticket_attachment/{ticket}/{article}/{attachment}."""
        path = f"api/v1/ticket_attachment/{ticket_id}/{article_id}/{attachment_id}"
        response = await self._request("GET", path, headers={"Accept": "*/*"})
        return response.content

    async def _request_json(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any | None = None,
    ) -> Any:
        response = await self._request(method, path, params=params, json=json)
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
    ) -> httpx.Response:
        # Total attempts = 1 initial + retry budget.
        max_attempts = _MAX_RETRIES + 1
        retry_count = 0

        while True:
            try:
                response = await self._http.request(
                    method, path, params=params, json=json, headers=headers
                )
            except httpx.TimeoutException as exc:
                retry_count = await retry_after_timeout_or_transport(
                    retry_count=retry_count,
                    max_attempts=max_attempts,
                    exc=exc,
                    sleep=self._sleep,
                    timeout_path=path,
                )
                continue
            except httpx.TransportError as exc:
                retry_count = await retry_after_timeout_or_transport(
                    retry_count=retry_count,
                    max_attempts=max_attempts,
                    exc=exc,
                    sleep=self._sleep,
                )
                continue

            retry_delay = retry_delay_for_response(
                response,
                retry_count=retry_count,
                max_attempts=max_attempts,
            )
            if retry_delay is not None:
                await self._sleep(retry_delay)
                retry_count += 1
                continue

            if 200 <= response.status_code < 300:
                return response

            self._raise_for_status(response)

    def _raise_for_status(self, response: httpx.Response) -> NoReturn:
        raise_for_status(response)
