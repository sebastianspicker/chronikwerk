from __future__ import annotations

def build_sidecar_summary() -> dict[str, str]:
    return {"scope": "sidecar", "status": "ready"}

# current lane: sidecar
def sidecar_task() -> dict[str, str]:
    return {"scope": "sidecar", "status": "ready"}
