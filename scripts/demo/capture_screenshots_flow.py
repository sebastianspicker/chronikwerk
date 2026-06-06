from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from scripts.demo.capture_screenshots_support import (
    SHOT_FILENAMES,
    compose,
    import_playwright,
    wait_http_ok,
)


def write_shot(page: Any, path: Path) -> None:
    page.screenshot(path=str(path), full_page=True)


def wait_for_admin_payload(page: Any, selector: str, *, timeout_ms: int = 10_000) -> None:
    page.wait_for_function(
        "(sel) => { const el = document.querySelector(sel);"
        " if (!el) return false;"
        " const txt = (el.textContent || '').trim();"
        " return txt.includes('\"status\"') || txt.includes('\"error\"'); }",
        arg=selector,
        timeout=timeout_ms,
    )


def capture_admin_flow(page: Any, args: argparse.Namespace, out_dir: Path) -> None:
    page.goto(f"{args.base_url}/admin", wait_until="networkidle")
    page.wait_for_selector("#token", timeout=10_000)
    write_shot(page, out_dir / SHOT_FILENAMES[0])

    page.fill("#token", args.token)
    page.click("#btn-refresh")
    wait_for_admin_payload(page, "#queue")
    write_shot(page, out_dir / SHOT_FILENAMES[1])

    page.click("#btn-history")
    wait_for_admin_payload(page, "#history")
    write_shot(page, out_dir / SHOT_FILENAMES[2])

    page.fill("#historyTicket", str(args.filter_ticket_id))
    page.click("#btn-history")
    page.wait_for_function(
        "(tid) => (document.querySelector('#history')?.textContent || '')"
        ".includes(String(tid))",
        arg=args.filter_ticket_id,
        timeout=10_000,
    )
    write_shot(page, out_dir / SHOT_FILENAMES[3])

    page.fill("#retryTicket", str(args.retry_ticket_id))
    page.click("#btn-retry")
    wait_for_admin_payload(page, "#actions")
    write_shot(page, out_dir / SHOT_FILENAMES[4])


def capture_dlq_flow(page: Any, out_dir: Path) -> None:
    page.fill("#historyTicket", "")
    page.click("#btn-refresh")
    wait_for_admin_payload(page, "#queue")
    write_shot(page, out_dir / SHOT_FILENAMES[5])

    page.fill("#drainLimit", "100")
    page.click("#btn-drain")
    wait_for_admin_payload(page, "#actions")
    write_shot(page, out_dir / SHOT_FILENAMES[6])


def capture_unauthorized_history(desktop: Any, base_url: str, out_dir: Path) -> None:
    unauthorized_page = desktop.new_page()
    try:
        unauthorized_page.goto(
            f"{base_url}/admin/api/history?limit=10",
            wait_until="networkidle",
        )
        write_shot(unauthorized_page, out_dir / SHOT_FILENAMES[7])
    finally:
        unauthorized_page.close()


def capture_backend_unavailable(page: Any, args: argparse.Namespace, out_dir: Path) -> None:
    stop = compose(args.compose_file, "stop", "redis-demo")
    if stop.returncode != 0:
        raise RuntimeError(f"unable to stop redis-demo: {stop.stderr.strip()}")

    page.click("#btn-refresh")
    page.wait_for_function(
        "() => (document.querySelector('#queue')?.textContent || '').includes('503')",
        timeout=10_000,
    )
    write_shot(page, out_dir / SHOT_FILENAMES[8])


def restart_redis_demo(args: argparse.Namespace) -> None:
    start = compose(args.compose_file, "start", "redis-demo")
    if start.returncode != 0:
        raise RuntimeError(f"unable to start redis-demo: {start.stderr.strip()}")
    wait_http_ok("archiver", f"{args.base_url}/healthz", timeout_s=args.timeout_seconds)


def capture_mobile_view(browser: Any, args: argparse.Namespace, out_dir: Path) -> None:
    mobile = browser.new_context(viewport={"width": 390, "height": 844})
    try:
        mobile_page = mobile.new_page()
        mobile_page.goto(f"{args.base_url}/admin", wait_until="networkidle")
        mobile_page.fill("#token", args.token)
        mobile_page.click("#btn-refresh")
        wait_for_admin_payload(mobile_page, "#queue")
        write_shot(mobile_page, out_dir / SHOT_FILENAMES[9])
    finally:
        mobile.close()


def capture(args: argparse.Namespace) -> int:
    sync_playwright, Error, PlaywrightTimeoutError = import_playwright()

    out_dir = args.output_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    wait_http_ok("archiver", f"{args.base_url}/healthz", timeout_s=args.timeout_seconds)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        desktop = browser.new_context(viewport={"width": 1366, "height": 900})
        page = desktop.new_page()

        redis_was_stopped = False
        try:
            capture_admin_flow(page, args, out_dir)
            capture_dlq_flow(page, out_dir)
            capture_unauthorized_history(desktop, args.base_url, out_dir)
            capture_backend_unavailable(page, args, out_dir)
            redis_was_stopped = True

            restart_redis_demo(args)
            redis_was_stopped = False
            capture_mobile_view(browser, args, out_dir)

        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"timeout while capturing screenshots: {exc}") from exc
        except Error as exc:
            raise RuntimeError(f"playwright error: {exc}") from exc
        finally:
            if redis_was_stopped:
                compose(args.compose_file, "start", "redis-demo")
            desktop.close()
            browser.close()

    print(f"Captured {len(SHOT_FILENAMES)} screenshots in {out_dir}")
    return 0
