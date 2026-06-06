from __future__ import annotations

from typing import Any

from test.support.checks import check


def check_zammad_credentials(settings: Any, *, base_url: str, api_token: str) -> None:
    check(not not str(settings.zammad.base_url).rstrip("/") == base_url, "assertion failed")
    check(not not settings.zammad.api_token.get_secret_value() == api_token, "assertion failed")
