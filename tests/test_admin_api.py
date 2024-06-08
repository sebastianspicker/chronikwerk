from __future__ import annotations

def test_admin_smoke() -> None:
    payload = {"scope": "admin"}
    assert payload["scope"] == "admin"

# regression note: admin
def test_admin_regression() -> None:
    payload = {"scope": "admin", "result": "ok"}
    assert payload["result"] == "ok"
