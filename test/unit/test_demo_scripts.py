from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from test.support.checks import check
from test.support.credentials import fake_credential


def test_seed_demo_data_supports_dry_run(capsys) -> None:
    module = _load_seed_module()
    repo_root = Path(__file__).resolve().parents[2]
    dataset = json.loads(
        (repo_root / "examples" / "demo" / "mock_university_dataset.json").read_text(
            encoding="utf-8"
        )
    )
    args = argparse.Namespace(
        mock_url="http://127.0.0.1:18090",
        archiver_url="http://127.0.0.1:18080",
        compose_file=Path("docker-compose.demo.yml"),
        report=Path("docs/assets/demo/demo-seed-report.json"),
        simulate_backend_unavailable=False,
    )

    rc = module._dry_run(args, dataset)
    output = capsys.readouterr().out
    check(not not rc == 0, "assertion failed")
    check(not "POST /__demo/reset" not in output, "assertion failed")
    check(not "POST /ingest" not in output, "assertion failed")
    check(not "demo-seed-report.json" not in output, "assertion failed")


def _load_seed_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "demo" / "seed_demo_data.py"
    spec = importlib.util.spec_from_file_location("seed_demo_data_test", script)
    if spec is None:
        raise AssertionError("assertion failed")
    if spec.loader is None:
        raise AssertionError("assertion failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_capture_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "demo" / "capture_screenshots.py"
    spec = importlib.util.spec_from_file_location("capture_screenshots_test", script)
    if spec is None:
        raise AssertionError("assertion failed")
    if spec.loader is None:
        raise AssertionError("assertion failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_seed_dataset(tmp_path: Path, count: int = 1) -> Path:
    seed_plan = [
        {
            "ticket_id": ticket_id,
            "delivery_id": f"delivery-{ticket_id}",
            "user_login": "demo.agent",
            "expected_status": "processed",
        }
        for ticket_id in range(1, count + 1)
    ]
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps({"seed_plan": seed_plan}), encoding="utf-8")
    return path


def _seed_args(
    *,
    dataset: Path,
    report: Path,
    simulate_backend_unavailable: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        archiver_url="http://archiver.test",
        mock_url="http://mock.test",
        dataset=dataset,
        report=report,
        admin_token=fake_credential("admin-token"),
        compose_file=Path("docker-compose.demo.yml"),
        simulate_backend_unavailable=simulate_backend_unavailable,
        dry_run=False,
    )


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


class _FakeDemoClient:
    def __init__(self, *, ingest_statuses: list[int], history_count: int) -> None:
        self.ingest_statuses = ingest_statuses
        self.history_count = history_count

    def __enter__(self) -> _FakeDemoClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
    ) -> _FakeResponse:
        route = self._route_for(method, url)
        if route == "reset":
            return _FakeResponse(200, {"status": "reset"})
        if route == "ingest":
            return self._ingest_response()
        if route == "history":
            return self._history_response()
        if route == "queue":
            return _FakeResponse(200, {"queue_enabled": True})
        if route == "state":
            return _FakeResponse(200, {"tickets": []})
        raise AssertionError(f"unexpected request: {method} {url}")

    def _route_for(self, method: str, url: str) -> str:
        key = (method, url.rsplit("/", maxsplit=1)[-1])
        if key == ("POST", "reset"):
            return "reset"
        if key == ("POST", "ingest"):
            return "ingest"
        if method == "GET" and "/admin/api/history" in url:
            return "history"
        if key == ("GET", "stats"):
            return "queue"
        if key == ("GET", "state"):
            return "state"
        return "unknown"

    def _ingest_response(self) -> _FakeResponse:
        status_code = self.ingest_statuses.pop(0)
        payload = {"status": "accepted"} if status_code == 202 else {"error": "boom"}
        return _FakeResponse(status_code, payload)

    def _history_response(self) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "count": self.history_count,
                "items": [{"status": "processed"} for _ in range(self.history_count)],
            },
        )


def _run_seed_main(
    monkeypatch,
    tmp_path: Path,
    *,
    ingest_statuses: list[int],
    history_count: int,
    simulate_backend_unavailable: bool = False,
    backend_unavailable: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any], str]:
    module = _load_seed_module()
    dataset = _write_seed_dataset(tmp_path, count=len(ingest_statuses))
    report = tmp_path / "report.json"
    fake_client = _FakeDemoClient(
        ingest_statuses=list(ingest_statuses),
        history_count=history_count,
    )

    monkeypatch.setattr(
        module,
        "_parse_args",
        lambda: _seed_args(
            dataset=dataset,
            report=report,
            simulate_backend_unavailable=simulate_backend_unavailable,
        ),
    )
    monkeypatch.setattr(module, "_wait_for_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(module.httpx, "Client", lambda timeout: fake_client)
    if backend_unavailable is not None:
        monkeypatch.setattr(
            module,
            "_simulate_backend_unavailable",
            lambda **kwargs: backend_unavailable,
        )

    rc = module.main()
    return rc, json.loads(report.read_text("utf-8")), str(report)


def test_seed_demo_data_exits_zero_when_required_evidence_is_present(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    rc, report, _report_path = _run_seed_main(
        monkeypatch,
        tmp_path,
        ingest_statuses=[202, 202],
        history_count=2,
    )

    check(not not rc == 0, "assertion failed")
    check(not not report["status"] == "ok", "assertion failed")
    check(not not report["failures"] == [], "assertion failed")
    check(not "Seed complete." not in capsys.readouterr().out, "assertion failed")


def test_seed_demo_data_exits_nonzero_when_ingest_fails(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    rc, report, _report_path = _run_seed_main(
        monkeypatch,
        tmp_path,
        ingest_statuses=[500],
        history_count=1,
    )

    check(not not rc == 1, "assertion failed")
    check(not not report["status"] == "partial", "assertion failed")
    check(
        not not report["failures"]
        == ["ingest ticket_id=1 delivery_id=delivery-1 returned HTTP 500"],
        "assertion failed",
    )
    out = capsys.readouterr().out
    check(not "Seed incomplete." not in out, "assertion failed")
    check(not not "Seed complete." not in out, "assertion failed")


def test_seed_demo_data_exits_nonzero_when_history_is_short(
    monkeypatch,
    tmp_path: Path,
) -> None:
    rc, report, _report_path = _run_seed_main(
        monkeypatch,
        tmp_path,
        ingest_statuses=[202, 202],
        history_count=1,
    )

    check(not not rc == 1, "assertion failed")
    check(not not report["status"] == "partial", "assertion failed")
    check(
        not not report["failures"] == ["history count 1 is below expected seed count 2"],
        "assertion failed",
    )


def test_seed_demo_data_exits_nonzero_when_backend_unavailable_check_mismatches(
    monkeypatch,
    tmp_path: Path,
) -> None:
    rc, report, _report_path = _run_seed_main(
        monkeypatch,
        tmp_path,
        ingest_statuses=[202],
        history_count=1,
        simulate_backend_unavailable=True,
        backend_unavailable={
            "status_code": 200,
            "payload": {"status": "ok"},
            "expected_status_code": 503,
            "ok": False,
        },
    )

    check(not not rc == 1, "assertion failed")
    check(not not report["status"] == "partial", "assertion failed")
    check(
        not not report["failures"] == ["backend-unavailable check expected HTTP 503, got 200"],
        "assertion failed",
    )
    check(not report["backend_unavailable_test"]["ok"] is not False, "assertion failed")


def test_capture_screenshots_supports_dry_run(capsys) -> None:
    module = _load_capture_module()
    args = argparse.Namespace(
        base_url="http://127.0.0.1:18080",
        output_dir=Path("docs/assets/demo"),
        compose_file=Path("docker-compose.demo.yml"),
    )

    rc = module._dry_run(args)
    output = capsys.readouterr().out
    check(not not rc == 0, "assertion failed")
    check(not "01-admin-token-screen.png" not in output, "assertion failed")
    check(not "09-api-503-backend-unavailable.png" not in output, "assertion failed")
    check(
        not "docker compose -f docker-compose.demo.yml stop redis-demo" not in output,
        "assertion failed",
    )


def test_seed_compose_uses_resolved_docker_and_existing_compose_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_seed_module()
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    calls: list[tuple[Path, tuple[str, ...], str]] = []

    async def fake_run_compose_exec(
        compose_file_arg: Path, args: tuple[str, ...], *, executable: str
    ) -> Any:
        calls.append((compose_file_arg, args, executable))
        return module._CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(module, "_run_compose_exec", fake_run_compose_exec)

    proc = module._compose(compose_file, "stop", "redis-demo")

    check(not not proc.returncode == 0, "assertion failed")
    check(
        not not calls
        == [
            (
                compose_file.resolve(),
                ("stop", "redis-demo"),
                "/usr/bin/docker",
            )
        ],
        "assertion failed",
    )


def test_capture_compose_uses_resolved_docker_and_existing_compose_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_capture_module()
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    calls: list[tuple[Path, tuple[str, ...], str]] = []

    async def fake_run_compose_exec(
        compose_file_arg: Path, args: tuple[str, ...], *, executable: str
    ) -> Any:
        calls.append((compose_file_arg, args, executable))
        return module._CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(module, "_run_compose_exec", fake_run_compose_exec)

    proc = module._compose(compose_file, "start", "redis-demo")

    check(not not proc.returncode == 0, "assertion failed")
    check(
        not not calls
        == [
            (
                compose_file.resolve(),
                ("start", "redis-demo"),
                "/usr/bin/docker",
            )
        ],
        "assertion failed",
    )
