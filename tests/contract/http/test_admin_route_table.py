"""Pin the ordered administration route table, since registration order decides matching."""

from __future__ import annotations

from collections.abc import Iterator

from starlette.routing import BaseRoute

from chronikwerk.web.admin.routes import router

_EXPECTED = [
    ("GET", "/admin/static/admin.css", "admin_css"),
    ("GET", "/admin/static/admin.js", "admin_javascript"),
    ("GET", "/admin/static/chronikwerk-mark.svg", "brand_mark"),
    ("GET", "/admin/static/atkinson-hyperlegible-next.woff2", "font_sans"),
    ("GET", "/admin/static/atkinson-hyperlegible-mono.woff2", "font_mono"),
    ("GET", "/admin/login", "login_page"),
    ("POST", "/admin/login", "login_form"),
    ("POST", "/admin/logout", "logout_form"),
    ("POST", "/admin/locale", "change_locale"),
    ("GET", "/admin", "overview_page"),
    ("GET", "/admin/jobs", "jobs_page"),
    ("GET", "/admin/jobs/{ticket_id}", "ticket_history_page"),
    ("POST", "/admin/jobs/{ticket_id}/retry", "retry_form"),
    ("GET", "/admin/configuration", "configuration_page"),
    ("GET", "/admin/configuration/revisions", "revisions_page"),
    ("POST", "/admin/configuration/revisions/{revision}/restore", "restore_form"),
    ("POST", "/admin/api/v1/session", "create_session"),
    ("DELETE", "/admin/api/v1/session", "delete_session"),
    ("GET", "/admin/api/v1/status", "status_api"),
    ("POST", "/admin/api/v1/status/storage-check", "storage_check_api"),
    ("GET", "/admin/api/v1/jobs", "jobs_api"),
    ("POST", "/admin/api/v1/jobs/{ticket_id}/retry", "retry_api"),
    ("GET", "/admin/api/v1/config", "config_api"),
    ("POST", "/admin/api/v1/config/validate", "validate_config_api"),
    ("PUT", "/admin/api/v1/config/staged", "stage_config_api"),
    ("GET", "/admin/api/v1/config/revisions", "revisions_api"),
    ("POST", "/admin/api/v1/config/revisions/{revision}/restore", "restore_api"),
]


def _flatten(routes: list[BaseRoute]) -> Iterator[BaseRoute]:
    """Yield concrete routes in registration order, expanding included routers."""
    for route in routes:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _flatten(nested.routes)
        else:
            yield route


def test_admin_route_table_order_and_schema_visibility() -> None:
    """Admin routes keep their methods, paths, names, order and schema hiding."""
    routes = list(_flatten(router.routes))
    actual = [
        ("/".join(sorted(route.methods)), route.path, route.name)  # type: ignore[attr-defined]
        for route in routes
    ]
    assert actual == _EXPECTED
    assert not any(route.include_in_schema for route in routes)  # type: ignore[attr-defined]
