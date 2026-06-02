from __future__ import annotations

import pathlib
from typing import Any

import pytest

from test.support.checks import check

playwright_sync = pytest.importorskip(
    "playwright.sync_api",
    reason="Admin dashboard runtime tests require Playwright.",
)
Error = playwright_sync.Error
expect = playwright_sync.expect
sync_playwright = playwright_sync.sync_playwright

_DASHBOARD_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src"
    / "zammad_pdf_archiver"
    / "templates"
    / "admin"
    / "dashboard.html"
)

_FETCH_MOCK = """
() => {
  window.__fetchCalls = [];
  window.__pendingFetch = {};
  window.fetch = (url, options = {}) => {
    const method = (options.method || 'GET').toUpperCase();
    const key = method + ' ' + String(url);
    const headers = options.headers || {};
    window.__fetchCalls.push({
      key,
      method,
      url: String(url),
      authorization: headers.Authorization || headers.authorization || null,
    });
    return new Promise((resolve, reject) => {
      if (!window.__pendingFetch[key]) window.__pendingFetch[key] = [];
      window.__pendingFetch[key].push({ resolve, reject });
    });
  };
  window.__resolveFetch = (key, status, data) => {
    const pending = window.__pendingFetch[key] || [];
    if (!pending.length) throw new Error('No pending fetch for ' + key);
    const entry = pending.shift();
    entry.resolve({ status, text: async () => JSON.stringify(data) });
  };
  window.__rejectFetch = (key, message) => {
    const pending = window.__pendingFetch[key] || [];
    if (!pending.length) throw new Error('No pending fetch for ' + key);
    const entry = pending.shift();
    entry.reject(new Error(message));
  };
}
"""


@pytest.fixture()
def browser():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as exc:
            pytest.skip(
                "Playwright Chromium browser is unavailable; "
                f"run `python -m playwright install chromium`: {exc}"
            )
        try:
            yield browser
        finally:
            browser.close()


def _dashboard_html() -> str:
    return _DASHBOARD_PATH.read_text(encoding="utf-8")


def _new_dashboard_page(browser, *, width: int = 1280, height: int = 720):
    page = browser.new_page(viewport={"width": width, "height": height})
    page.set_content(_dashboard_html(), wait_until="domcontentloaded")
    page.evaluate(_FETCH_MOCK)
    return page


def _wait_for_pending(page, key: str, *, count: int = 1) -> None:
    page.wait_for_function(
        "(args) => ((window.__pendingFetch[args.key] || []).length >= args.count)",
        arg={"key": key, "count": count},
    )


def _resolve_fetch(page, key: str, status: int, data: dict[str, Any]) -> None:
    page.evaluate(
        "({ key, status, data }) => window.__resolveFetch(key, status, data)",
        {"key": key, "status": status, "data": data},
    )


def _reject_fetch(page, key: str, message: str) -> None:
    page.evaluate(
        "({ key, message }) => window.__rejectFetch(key, message)",
        {"key": key, "message": message},
    )


def _resolve_followup_refresh(page, *, history_count: int) -> None:
    _wait_for_pending(page, "GET /admin/api/queue/stats")
    _wait_for_pending(page, "GET /admin/api/history?limit=100")
    _resolve_fetch(
        page,
        "GET /admin/api/queue/stats",
        200,
        {"execution_backend": "redis_queue", "queue_enabled": True},
    )
    _resolve_fetch(
        page,
        "GET /admin/api/history?limit=100",
        200,
        {"status": "ok", "available": True, "count": history_count, "items": []},
    )


def test_admin_dashboard_starts_unknown_and_requires_token(browser) -> None:
    page = _new_dashboard_page(browser, width=390, height=844)
    try:
        expect(page.locator("#dashboardStatusText")).to_have_text("Status unknown")
        expect(page.locator("#actions")).to_have_text("No action run.")
        indicator_classes = page.locator("#dashboardStatusIndicator").evaluate(
            "(el) => Array.from(el.classList)"
        )
        check(not not "status-ok" not in indicator_classes, "assertion failed")

        page.click("#btn-refresh")

        expect(page.locator(".toast.toast-error")).to_contain_text("Bearer token is required")
        check(not not page.evaluate("() => window.__fetchCalls.length") == 0, "assertion failed")
        check(
            not not page.evaluate(
                "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
            ),
            "assertion failed",
        )
    finally:
        page.close()


def test_admin_dashboard_refresh_renders_data_and_visible_503(browser) -> None:
    page = _new_dashboard_page(browser)
    try:
        page.fill("#token", "admin-token")
        page.click("#btn-refresh")

        _wait_for_pending(page, "GET /admin/api/queue/stats")
        _wait_for_pending(page, "GET /admin/api/history?limit=100")
        calls = page.evaluate("() => window.__fetchCalls")
        check(
            not not {call["authorization"] for call in calls} == {"Bearer admin-token"},
            "assertion failed",
        )

        _resolve_fetch(
            page,
            "GET /admin/api/history?limit=100",
            503,
            {"detail": "history_unavailable"},
        )
        _resolve_fetch(
            page,
            "GET /admin/api/queue/stats",
            200,
            {"execution_backend": "redis_queue", "queue_enabled": True},
        )

        expect(page.locator("#queue")).to_contain_text("redis_queue")
        expect(page.locator("#history")).to_contain_text("history_unavailable")
        expect(page.locator("#dashboardStatusText")).to_have_text("Dashboard refresh incomplete")
        indicator_classes = page.locator("#dashboardStatusIndicator").evaluate(
            "(el) => Array.from(el.classList)"
        )
        check(not "status-error" not in indicator_classes, "assertion failed")
        expect(page.locator(".toast.toast-error")).to_contain_text("History failed")
    finally:
        page.close()


def test_admin_dashboard_retry_network_error_is_visible(browser) -> None:
    page = _new_dashboard_page(browser)
    try:
        page.fill("#token", "admin-token")
        page.fill("#retryTicket", "42")
        page.click("#btn-retry")

        _wait_for_pending(page, "POST /admin/api/retry/42")
        _reject_fetch(page, "POST /admin/api/retry/42", "offline")

        expect(page.locator("#actions")).to_contain_text('"status": 0')
        expect(page.locator("#actions")).to_contain_text("offline")
        expect(page.locator("#dashboardStatusText")).to_contain_text(
            "Retry failed (network/timeout): offline"
        )
        expect(page.locator(".toast.toast-error")).to_contain_text("Retry failed")
    finally:
        page.close()


def test_admin_dashboard_dlq_actions_wait_for_followup_refreshes(browser) -> None:
    page = _new_dashboard_page(browser)
    try:
        page.fill("#token", "admin-token")

        page.click("#btn-drain")
        _wait_for_pending(page, "POST /admin/api/dlq/drain?limit=100")
        _resolve_fetch(
            page,
            "POST /admin/api/dlq/drain?limit=100",
            200,
            {"status": "ok", "drained": 2},
        )
        expect(page.locator("#btn-drain")).to_be_disabled()
        _resolve_followup_refresh(page, history_count=0)
        expect(page.locator("#btn-drain")).to_be_enabled()
        expect(page.locator("#actions")).to_contain_text('"drained": 2')
        expect(page.locator("#dashboardStatusText")).to_have_text(
            "DLQ drained and dashboard refreshed"
        )

        page.click("#btn-replay")
        _wait_for_pending(page, "POST /admin/api/dlq/replay?limit=10")
        _resolve_fetch(
            page,
            "POST /admin/api/dlq/replay?limit=10",
            200,
            {"status": "ok", "replayed": 1, "skipped": 0, "errors": 0},
        )
        expect(page.locator("#btn-replay")).to_be_disabled()
        _resolve_followup_refresh(page, history_count=1)
        expect(page.locator("#btn-replay")).to_be_enabled()
        expect(page.locator("#actions")).to_contain_text('"replayed": 1')
        expect(page.locator("#dashboardStatusText")).to_have_text(
            "DLQ replayed and dashboard refreshed"
        )
    finally:
        page.close()
