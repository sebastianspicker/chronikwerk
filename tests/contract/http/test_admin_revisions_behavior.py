"""Pin the administration revision listing and restore behavior over HTTP."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from chronikwerk.web.app import create_app
from tests.support.settings_factory import make_settings

_TOKEN = "admin-token-0123456789abcdef0123456789"
_MISSING = "0" * 64


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    """Create a logged-in administration client and return it with its CSRF token."""
    root = tmp_path.resolve()
    settings = make_settings(
        str(root),
        secret="test-webhook-secret-0123456789abcdef",
        overrides={
            "admin": {
                "enabled": True,
                "access_token": _TOKEN,
                "state_dir": str(root / "admin-state"),
            }
        },
    )
    client = TestClient(create_app(settings), base_url="https://testserver")
    login = client.post("/admin/login", data={"access_token": _TOKEN}, follow_redirects=False)
    assert login.status_code == 303
    page = client.get("/admin/configuration")
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.text)
    assert match is not None
    return client, match.group(1)


def _stage(client: TestClient, csrf: str, locale: str) -> dict[str, object]:
    """Stage a PDF locale overlay on top of the current revision and return its metadata."""
    current = client.get("/admin/api/v1/config").json()["revision"]
    response = client.put(
        "/admin/api/v1/config/staged",
        headers={"X-CSRF-Token": csrf, "If-Match": current},
        json={"overlay": {"pdf": {"locale": locale}}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _locale(client: TestClient) -> object:
    """Return the effective pdf.locale value reported by the configuration read model."""
    fields = client.get("/admin/api/v1/config").json()["fields"]
    return next(field["value"] for field in fields if field["path"] == "pdf.locale")


def test_revisions_are_empty_before_staging_and_listed_after(tmp_path: Path) -> None:
    """The revision API and page list a revision only once a change has been staged."""
    client, csrf = _client(tmp_path)
    assert client.get("/admin/api/v1/config/revisions").json()["items"] == []

    staged = _stage(client, csrf, "en-GB")

    listing = client.get("/admin/api/v1/config/revisions").json()
    assert listing["revision"] == staged["revision"]
    assert [item["revision"] for item in listing["items"]] == [staged["revision"]]
    assert listing["items"][0]["changed_paths"] == ["pdf.locale"]
    assert listing["items"][0]["previous_revision"] == staged["previous_revision"]
    page = client.get("/admin/configuration/revisions")
    assert page.status_code == 200
    assert str(staged["revision"]) in page.text


def test_revision_listing_requires_a_session(tmp_path: Path) -> None:
    """Anonymous callers get a 401 JSON envelope from the revision API."""
    client, _csrf = _client(tmp_path)
    anonymous = TestClient(client.app, base_url="https://testserver")
    response = anonymous.get("/admin/api/v1/config/revisions")
    assert response.status_code == 401
    assert response.json()["code"] == "admin_session_required"


def test_restore_api_stages_a_new_revision_with_the_restored_overlay(tmp_path: Path) -> None:
    """Restoring an older revision stages a new revision that carries the older overlay."""
    client, csrf = _client(tmp_path)
    first = _stage(client, csrf, "en-GB")
    second = _stage(client, csrf, "de-DE")
    assert _locale(client) == "de-DE"

    response = client.post(
        f"/admin/api/v1/config/revisions/{first['revision']}/restore",
        headers={"X-CSRF-Token": csrf, "If-Match": str(second["revision"])},
        json={},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "revision",
        "previous_revision",
        "created_at",
        "request_id",
        "changed_paths",
        "restart_required",
    }
    assert body["restart_required"] is True
    assert body["previous_revision"] == second["revision"]
    assert body["revision"] not in {first["revision"], second["revision"]}
    config = client.get("/admin/api/v1/config").json()
    assert config["staged_revision"] == body["revision"]
    assert _locale(client) == "en-GB"
    items = client.get("/admin/api/v1/config/revisions").json()["items"]
    assert [item["revision"] for item in items][0] == body["revision"]


def test_restore_api_rejects_unknown_malformed_and_stale_requests(tmp_path: Path) -> None:
    """Unknown and malformed revisions are 422 failures; a stale If-Match is a 409 conflict."""
    client, csrf = _client(tmp_path)
    first = _stage(client, csrf, "en-GB")
    second = _stage(client, csrf, "de-DE")
    headers = {"X-CSRF-Token": csrf, "If-Match": str(second["revision"])}

    unknown = client.post(
        f"/admin/api/v1/config/revisions/{_MISSING}/restore", headers=headers, json={}
    )
    malformed = client.post("/admin/api/v1/config/revisions/zz/restore", headers=headers, json={})
    stale = client.post(
        f"/admin/api/v1/config/revisions/{first['revision']}/restore",
        headers={"X-CSRF-Token": csrf, "If-Match": "stale"},
        json={},
    )

    for response in (unknown, malformed):
        assert response.status_code == 422
        assert response.json()["code"] == "config_restore_failed"
    assert stale.status_code == 409
    assert stale.json()["code"] == "config_revision_conflict"
    assert unknown.json()["errors"][0]["message"] == "Revision file not found"
    assert malformed.json()["errors"][0]["message"] == "Invalid revision identifier"
    assert _locale(client) == "de-DE"


def test_restore_api_requires_csrf(tmp_path: Path) -> None:
    """A restore without the CSRF header is refused before touching any revision."""
    client, csrf = _client(tmp_path)
    first = _stage(client, csrf, "en-GB")
    second = _stage(client, csrf, "de-DE")

    response = client.post(
        f"/admin/api/v1/config/revisions/{first['revision']}/restore",
        headers={"If-Match": str(second["revision"])},
        json={},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"
    assert _locale(client) == "de-DE"


def test_restore_form_redirects_to_configuration_on_success(tmp_path: Path) -> None:
    """A valid acknowledged form restore redirects to the configuration page."""
    client, csrf = _client(tmp_path)
    first = _stage(client, csrf, "en-GB")
    second = _stage(client, csrf, "de-DE")

    response = client.post(
        f"/admin/configuration/revisions/{first['revision']}/restore",
        data={
            "csrf_token": csrf,
            "security_acknowledged": "true",
            "expected_revision": str(second["revision"]),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/configuration"
    assert _locale(client) == "en-GB"


def test_restore_form_redirects_with_flags_for_failures(tmp_path: Path) -> None:
    """Form restores report missing acknowledgement, bad revisions and bad CSRF by redirect."""
    client, csrf = _client(tmp_path)
    first = _stage(client, csrf, "en-GB")
    base = "/admin/configuration/revisions"

    unacknowledged = client.post(
        f"{base}/{first['revision']}/restore", data={"csrf_token": csrf}, follow_redirects=False
    )
    unknown = client.post(
        f"{base}/{_MISSING}/restore",
        data={"csrf_token": csrf, "security_acknowledged": "true", "expected_revision": "x"},
        follow_redirects=False,
    )
    bad_csrf = client.post(
        f"{base}/{first['revision']}/restore",
        data={"csrf_token": "wrong", "security_acknowledged": "true"},
        follow_redirects=False,
    )

    assert unacknowledged.headers["location"] == f"{base}?acknowledgement_required=true"
    assert unknown.headers["location"] == f"{base}?restore_error=true"
    assert bad_csrf.headers["location"] == "/admin/login"
    assert {r.status_code for r in (unacknowledged, unknown, bad_csrf)} == {303}
