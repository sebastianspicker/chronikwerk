from __future__ import annotations

from typing import Any, NoReturn

import httpx
from pydantic import TypeAdapter, ValidationError

from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from zammad_pdf_archiver.adapters.zammad.models import Article, TagList


def tag_list_from_response(response: Any, *, ticket_id: int) -> TagList:
    # Zammad may return either a raw JSON array or an object wrapper depending on version.
    if isinstance(response, dict) and "tags" in response:
        tags_value = response["tags"]
    else:
        tags_value = response

    try:
        tags = TypeAdapter(list[str]).validate_python(tags_value)
    except ValidationError as exc:
        raise ClientError(
            f"Zammad tags response format unexpected for ticket {ticket_id}: {exc!s}"
        ) from exc
    return TagList(tags)


def article_list_from_response(response: Any) -> list[Article]:
    items = TypeAdapter(list[dict[str, Any]]).validate_python(response)
    return [Article.model_validate(item) for item in items]


def require_tag_mutation_success(
    response: Any,
    *,
    operation: str,
    ticket_id: int,
    tag: str,
) -> None:
    if isinstance(response, dict) and response.get("success") is True:
        return
    raise ClientError(
        f"Zammad tag {operation} for ticket {ticket_id} and tag {tag!r} did not confirm success"
    )


def raise_for_status(response: httpx.Response) -> NoReturn:
    status = response.status_code
    url = str(response.request.url)

    if status in (401, 403):
        raise AuthError(f"Zammad auth failed (status={status}) at {url}")
    if status == 404:
        raise NotFoundError(f"Zammad resource not found (status=404) at {url}")
    if status == 429:
        raise RateLimitError(f"Zammad rate limit (status=429) at {url}")
    if 300 <= status < 400:
        location = response.headers.get("Location", "")
        raise ClientError(f"Unexpected redirect {status} from Zammad at {url}: {location}")
    if status >= 500:
        raise ServerError(f"Zammad server error (status={status}) at {url}")
    if status >= 400:
        raise ClientError(f"Zammad client error (status={status}) at {url}")

    raise ClientError(f"Unexpected Zammad HTTP status={status} at {url}")
