"""Aggregate multilingual HTML and JSON routes for the admin application."""

from __future__ import annotations

from fastapi import APIRouter

from chronikwerk.web.admin import _config_routes, _page_routes, _revision_routes, _status_routes

__all__ = ["router"]

# Include order is route matching order: pages, configuration, revisions, status, then APIs.
router = APIRouter(include_in_schema=False)
router.include_router(_page_routes.router)
router.include_router(_config_routes.page_router)
router.include_router(_revision_routes.page_router)
router.include_router(_status_routes.router)
router.include_router(_config_routes.api_router)
router.include_router(_revision_routes.api_router)
