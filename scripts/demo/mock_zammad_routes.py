from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from scripts.demo.mock_zammad_store import NewArticle, iso_now
from scripts.demo.mock_zammad_tag_routes import register_tag_routes

__all__ = [
    "register_demo_routes",
    "register_tag_routes",
    "register_ticket_routes",
]


def register_demo_routes(app: FastAPI, state: Any) -> None:
    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "time": iso_now(), "tickets": state.store.state()["ticket_count"]}

    @app.post("/__demo/reset")
    async def demo_reset() -> dict[str, Any]:
        return state.store.reset()

    @app.get("/__demo/state")
    async def demo_state() -> dict[str, Any]:
        return state.store.state()


def register_ticket_routes(app: FastAPI, state: Any, auth: Any) -> None:
    @app.get("/api/v1/tickets/{ticket_id}")
    async def get_ticket(ticket_id: int, _: None = Depends(auth)) -> dict[str, Any]:
        try:
            return state.store.get_ticket(ticket_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc

    @app.get("/api/v1/ticket_articles/by_ticket/{ticket_id}")
    async def list_articles(ticket_id: int, _: None = Depends(auth)) -> list[dict[str, Any]]:
        try:
            return state.store.list_articles(ticket_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc

    @app.post("/api/v1/ticket_articles")
    async def create_article(payload: NewArticle, _: None = Depends(auth)) -> dict[str, Any]:
        try:
            return state.store.add_article(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc
