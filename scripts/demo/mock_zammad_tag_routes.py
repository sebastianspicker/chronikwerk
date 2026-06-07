from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel


class TagMutation(BaseModel):
    object: str
    o_id: int
    item: str


def register_tag_routes(app: FastAPI, state: Any, auth: Any) -> None:
    @app.get("/api/v1/tags")
    async def get_tags(
        object_type: str | None = Query(default=None, alias="object"),
        o_id: int | None = None,
        _: None = Depends(auth),
    ) -> dict[str, Any]:
        if object_type != "Ticket" or o_id is None:
            raise HTTPException(status_code=400, detail="invalid_tag_query")
        return {"tags": state.store.get_tags(o_id)}

    @app.post("/api/v1/tags/add")
    async def add_tag(payload: TagMutation, _: None = Depends(auth)) -> dict[str, Any]:
        if payload.object != "Ticket":
            raise HTTPException(status_code=400, detail="unsupported_object")
        try:
            return state.store.set_tag(payload.o_id, tag=payload.item, present=True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc

    @app.post("/api/v1/tags/remove")
    async def remove_tag(payload: TagMutation, _: None = Depends(auth)) -> dict[str, Any]:
        if payload.object != "Ticket":
            raise HTTPException(status_code=400, detail="unsupported_object")
        try:
            return state.store.set_tag(payload.o_id, tag=payload.item, present=False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc
