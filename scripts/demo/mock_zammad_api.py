from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.demo.mock_zammad_routes import (
    register_demo_routes,
    register_tag_routes,
    register_ticket_routes,
)
from scripts.demo.mock_zammad_store import DemoStore


class AppConfig(BaseModel):
    dataset_path: Path
    api_token: str = Field(min_length=1)


class AppState:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = DemoStore(config.dataset_path)


def _auth_dependency(state: AppState):
    def _verify(authorization: str | None = Header(default=None)) -> None:
        expected = f"Token token={state.config.api_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

    return _verify


def create_app(*, dataset_path: Path, api_token: str) -> FastAPI:
    config = AppConfig(dataset_path=dataset_path, api_token=api_token)
    state = AppState(config)
    app = FastAPI(title="mock-zammad-api", version="1.0")
    auth = _auth_dependency(state)

    register_demo_routes(app, state)
    register_ticket_routes(app, state, auth)
    register_tag_routes(app, state, auth)
    return app


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local mock Zammad API service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--dataset",
        default="examples/demo/mock_university_dataset.json",
        help="Path to dataset JSON",
    )
    parser.add_argument("--token", default="demo-token", help="Expected API token")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    dataset_path = Path(args.dataset).expanduser().resolve()
    if not dataset_path.is_file():
        raise SystemExit(f"Dataset not found: {dataset_path}")

    app = create_app(dataset_path=dataset_path, api_token=args.token)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
