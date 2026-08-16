"""Verifies rendering forwards article limits, copy safety, and signing transport settings."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chronikwerk.adapters.signing.sign_pdf import SignedPdf
from chronikwerk.app.jobs import ticket_renderer
from chronikwerk.config.settings import Settings
from chronikwerk.domain.snapshot_models import Snapshot


def _snapshot(article_count: int) -> Snapshot:
    """Build a representative ticket snapshot fixture."""
    return Snapshot.model_validate(
        {
            "ticket": {
                "id": 1,
                "number": "T1",
                "title": "Article limit forwarding",
                "created_at": "2024-01-01T10:00:00Z",
                "updated_at": "2024-01-01T10:00:00Z",
                "customer": {"name": "Customer", "email": "customer@example.invalid"},
                "owner": {"login": "agent", "name": "Agent"},
                "tags": [],
                "custom_fields": {},
            },
            "articles": [
                {
                    "id": article_id,
                    "created_at": "2024-01-01T10:00:00Z",
                    "internal": False,
                    "sender": "customer@example.invalid",
                    "subject": f"Article {article_id}",
                    "body_html": "<p>Hello</p>",
                    "body_text": "Hello",
                    "attachments": [],
                }
                for article_id in range(article_count)
            ],
        }
    )


def _settings(**sections: dict[str, Any]) -> Settings:
    """Build the shared renderer settings baseline with scenario-specific sections."""
    mapping: dict[str, Any] = {
        "zammad": {
            "base_url": "https://zammad.example.invalid",
            "api_token": "token",
        },
        "storage": {"root": "/tmp/archive"},
    }
    mapping.update(sections)
    return Settings.from_mapping(mapping)


def test_build_and_render_pdf_forwards_unlimited_article_setting(monkeypatch) -> None:
    settings = _settings(pdf={"max_articles": 0})
    snapshot = _snapshot(251)
    seen: dict[str, Any] = {}

    async def fake_build_snapshot(*_args: Any, **_kwargs: Any) -> Snapshot:
        return snapshot

    async def fake_render_pdf(snapshot_arg: Snapshot, **kwargs: Any) -> bytes:
        seen["snapshot"] = snapshot_arg
        seen["max_articles"] = kwargs["max_articles"]
        return b"%PDF"

    monkeypatch.setattr(ticket_renderer, "build_snapshot", fake_build_snapshot)
    monkeypatch.setattr(ticket_renderer, "render_pdf", fake_render_pdf)

    rendered = asyncio.run(
        ticket_renderer.build_and_render_pdf(
            client=None,  # type: ignore[arg-type]
            ticket_id=1,
            ticket={},  # type: ignore[arg-type]
            tags=[],  # type: ignore[arg-type]
            settings=settings,
        )
    )

    assert rendered.pdf_bytes == b"%PDF"
    assert rendered.snapshot is snapshot
    assert rendered.signing_cert_fingerprint is None
    assert seen == {"snapshot": snapshot, "max_articles": 0}


def test_cap_articles_uses_snapshot_copy_without_mutating_original() -> None:
    settings = _settings(pdf={"max_articles": 1, "article_limit_mode": "cap_and_continue"})
    snapshot = _snapshot(2)

    capped = ticket_renderer._cap_articles_if_configured(
        snapshot,
        ticket_id=1,
        settings=settings,
    )

    assert capped is not snapshot
    assert capped.ticket is snapshot.ticket
    assert len(capped.articles) == 1
    assert len(snapshot.articles) == 2
    assert capped.articles_total == 2
    assert capped.articles_omitted == 1


@pytest.mark.parametrize("trust_env", [True, False])
def test_signing_forwards_transport_trust_env(monkeypatch, trust_env: bool) -> None:
    settings = _settings(
        signing={"enabled": True, "pfx_path": "/tmp/test.pfx"},
        hardening={"transport": {"trust_env": trust_env}},
    )
    seen: dict[str, Any] = {}

    async def fake_build_snapshot(*_args: Any, **_kwargs: Any) -> Snapshot:
        return _snapshot(1)

    async def fake_render_pdf(*_args: Any, **_kwargs: Any) -> bytes:
        return b"%PDF"

    def fake_sign_pdf(pdf: bytes, *, signing: Any, trust_env: bool, **_kwargs: Any) -> Any:
        seen["trust_env"] = trust_env
        return SignedPdf(pdf + b"-signed", "signer-fingerprint")

    monkeypatch.setattr(ticket_renderer, "build_snapshot", fake_build_snapshot)
    monkeypatch.setattr(ticket_renderer, "render_pdf", fake_render_pdf)
    monkeypatch.setattr(ticket_renderer, "sign_pdf_with_provenance", fake_sign_pdf)

    rendered = asyncio.run(
        ticket_renderer.build_and_render_pdf(
            client=None,  # type: ignore[arg-type]
            ticket_id=1,
            ticket={},  # type: ignore[arg-type]
            tags=[],  # type: ignore[arg-type]
            settings=settings,
        )
    )

    assert rendered.pdf_bytes == b"%PDF-signed"
    assert rendered.signing_cert_fingerprint == "signer-fingerprint"
    assert seen == {"trust_env": trust_env}
