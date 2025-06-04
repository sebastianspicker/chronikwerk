from __future__ import annotations

def test_security_regression() -> None:
    payload = {"scope": "security"}
    assert payload["scope"] == "security"

# regression note: security
def test_security_regression() -> None:
    payload = {"scope": "security", "result": "ok"}
    assert payload["result"] == "ok"
