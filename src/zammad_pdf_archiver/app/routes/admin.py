from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse

from zammad_pdf_archiver.app.constants import REQUEST_ID_KEY
from zammad_pdf_archiver.app.jobs.history import read_history
from zammad_pdf_archiver.app.jobs.redis_queue import drain_dlq, get_queue_stats
from zammad_pdf_archiver.app.routes.ingest import _dispatch_ticket
from zammad_pdf_archiver.config.settings import Settings

router = APIRouter()
log = structlog.get_logger(__name__)


def _settings_or_503(request: Request) -> Settings:
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="settings_not_configured")
    return settings


def _verify_admin_auth(request: Request, settings: Settings) -> None:
    if not settings.admin.enabled:
        raise HTTPException(status_code=404, detail="admin_disabled")

    token = settings.admin.bearer_token
    expected = token.get_secret_value().encode("utf-8") if token is not None else b""
    if not expected:
        raise HTTPException(status_code=503, detail="admin_token_not_configured")

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth) < 8:
        raise HTTPException(status_code=401, detail="unauthorized")

    provided = auth[7:].strip().encode("utf-8")
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard() -> HTMLResponse:
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Zammad PDF Archiver | Admin</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-base: #0f1115;
      --bg-gradient: radial-gradient(circle at top right, rgba(29, 78, 216, 0.15), transparent 40%),
                     radial-gradient(circle at bottom left, rgba(147, 51, 234, 0.15), transparent 40%);
      --panel-bg: rgba(23, 25, 31, 0.6);
      --panel-border: rgba(255, 255, 255, 0.08);
      --fg-primary: #f8fafc;
      --fg-secondary: #94a3b8;
      --accent-color: #3b82f6;
      --accent-hover: #60a5fa;
      --danger-color: #ef4444;
      --success-color: #10b981;
      --border-radius: 16px;
      --font-ui: 'Inter', system-ui, -apple-system, sans-serif;
      --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--font-ui);
      background-color: var(--bg-base);
      background-image: var(--bg-gradient);
      background-attachment: fixed;
      color: var(--fg-primary);
      min-height: 100vh;
      padding: 40px 20px;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }

    .wrap {
      max-width: 1200px;
      margin: 0 auto;
      display: grid;
      gap: 24px;
      grid-template-columns: 1fr;
    }

    @media (min-width: 1024px) {
      .wrap { grid-template-columns: 1fr 1fr; }
      .panel.full-width { grid-column: 1 / -1; }
    }

    .panel {
      background: var(--panel-bg);
      border: 1px solid var(--panel-border);
      border-radius: var(--border-radius);
      padding: 24px;
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      box-shadow: 0 4px 24px -1px rgba(0, 0, 0, 0.2);
      transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
      display: flex;
      flex-direction: column;
    }
    
    .panel:hover {
      border-color: rgba(255, 255, 255, 0.15);
      box-shadow: 0 8px 32px -4px rgba(0, 0, 0, 0.3);
    }

    .header-panel {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
    }

    h1 {
      font-size: 28px;
      font-weight: 600;
      letter-spacing: -0.02em;
      background: linear-gradient(to right, #fff, #94a3b8);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 4px;
    }

    h2 {
      font-size: 16px;
      font-weight: 500;
      color: var(--fg-primary);
      margin-bottom: 16px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    h2::before {
      content: '';
      display: block;
      width: 4px;
      height: 16px;
      background: var(--accent-color);
      border-radius: 2px;
    }

    p.status {
      color: var(--fg-secondary);
      font-size: 14px;
      margin-bottom: 0;
    }

    .row {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 16px;
    }
    .row:last-of-type {
      margin-bottom: 0;
    }

    input {
      background: rgba(0, 0, 0, 0.2);
      border: 1px solid var(--panel-border);
      color: var(--fg-primary);
      border-radius: 8px;
      padding: 10px 14px;
      font-family: var(--font-ui);
      font-size: 14px;
      outline: none;
      transition: all 0.2s ease;
      min-width: 240px;
      box-shadow: inset 0 2px 4px rgba(0,0,0,0.1);
    }
    input::placeholder { color: #64748b; }
    input:focus {
      border-color: var(--accent-color);
      box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2), inset 0 2px 4px rgba(0,0,0,0.1);
    }

    button {
      background: var(--accent-color);
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 10px 18px;
      font-family: var(--font-ui);
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: all 0.2s ease;
      box-shadow: 0 2px 8px rgba(59, 130, 246, 0.3);
    }
    button:hover {
      background: var(--accent-hover);
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(59, 130, 246, 0.4);
    }
    button:active {
      transform: translateY(1px);
    }
    button.secondary {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--panel-border);
      box-shadow: none;
    }
    button.secondary:hover {
      background: rgba(255, 255, 255, 0.1);
      border-color: rgba(255, 255, 255, 0.2);
    }
    button:disabled, button.loading {
      opacity: 0.7;
      cursor: not-allowed;
      transform: none;
    }

    pre {
      margin: 16px 0 0 0;
      padding: 16px;
      border: 1px solid var(--panel-border);
      background: #0f1115;
      border-radius: 12px;
      max-height: 400px;
      overflow: auto;
      font-family: var(--font-mono);
      font-size: 13px;
      color: #e2e8f0;
      flex-grow: 1;
      box-shadow: inset 0 4px 12px rgba(0,0,0,0.3);
      scrollbar-width: thin;
      scrollbar-color: rgba(255,255,255,0.2) transparent;
    }
    pre::-webkit-scrollbar { width: 8px; height: 8px; }
    pre::-webkit-scrollbar-track { background: transparent; }
    pre::-webkit-scrollbar-thumb {
      background-color: rgba(255, 255, 255, 0.2);
      border-radius: 4px;
    }

    /* Simple subtle spinner */
    @keyframes spin { to { transform: rotate(360deg); } }
    .spinner {
      display: inline-block;
      width: 14px;
      height: 14px;
      border: 2px solid rgba(255,255,255,0.3);
      border-radius: 50%;
      border-top-color: #fff;
      animation: spin 0.8s linear infinite;
      display: none;
    }
    button.loading .spinner { display: inline-block; }
    
    .status-indicator {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--success-color);
      box-shadow: 0 0 8px var(--success-color);
      margin-right: 8px;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel full-width header-panel">
      <div>
        <h1>Zammad Archiver Admin</h1>
        <p class="status"><span class="status-indicator"></span>System Dashboard & Maintenance</p>
      </div>
      <div class="row" style="margin: 0;">
        <input id="token" type="password" placeholder="Admin Bearer Token" autocomplete="current-password" />
        <button id="btn-refresh" onclick="loadAll()">
          <span class="spinner"></span>Refresh
        </button>
      </div>
    </div>

    <div class="panel">
      <h2>Queue Stats</h2>
      <pre id="queue">No data loaded.</pre>
    </div>

    <div class="panel">
      <h2>History</h2>
      <div class="row">
        <input id="historyLimit" type="number" value="100" min="1" max="5000" style="width: 100px; min-width: 0;" title="Limit" />
        <input id="historyTicket" type="number" placeholder="Ticket ID (optional)" style="flex: 1;" />
        <button id="btn-history" class="secondary" onclick="loadHistory()">
          <span class="spinner"></span>Fetch
        </button>
      </div>
      <pre id="history">No data loaded.</pre>
    </div>

    <div class="panel full-width">
      <h2>Actions</h2>
      <div class="row">
        <input id="retryTicket" type="number" placeholder="Ticket ID to Retry" />
        <button id="btn-retry" class="secondary" onclick="retryTicket()">
          <span class="spinner"></span>Retry Ticket
        </button>
      </div>
      <div class="row">
        <input id="drainLimit" type="number" value="100" min="1" max="1000" style="width: 100px; min-width: 0;" title="Drain Limit" />
        <button id="btn-drain" class="secondary" onclick="drainDlq()">
          <span class="spinner"></span>Drain DLQ
        </button>
      </div>
      <pre id="actions">Ready.</pre>
    </div>
  </div>

  <script>
    function authHeaders() {
      const token = document.getElementById('token').value.trim();
      return token ? { 'Authorization': `Bearer ${token}` } : {};
    }

    function toggleLoading(btnId, isLoading) {
      const btn = document.getElementById(btnId);
      if(!btn) return;
      if (isLoading) {
        btn.classList.add('loading');
        btn.disabled = true;
      } else {
        btn.classList.remove('loading');
        btn.disabled = false;
      }
    }

    async function requestJson(url, options = {}) {
      const headers = Object.assign({}, authHeaders(), options.headers || {});
      const resp = await fetch(url, Object.assign({}, options, { headers }));
      const text = await resp.text();
      let data;
      try { data = JSON.parse(text); } catch { data = { raw: text }; }
      return { status: resp.status, data };
    }

    function renderJson(elId, data) {
      document.getElementById(elId).textContent = JSON.stringify(data, null, 2);
    }

    async function loadQueue() {
      toggleLoading('btn-refresh', true);
      try {
        const out = await requestJson('/admin/api/queue/stats');
        renderJson('queue', out);
      } finally {
        toggleLoading('btn-refresh', false);
      }
    }

    async function loadHistory() {
      toggleLoading('btn-history', true);
      try {
        const limit = encodeURIComponent(document.getElementById('historyLimit').value || '100');
        const tid = document.getElementById('historyTicket').value.trim();
        const suffix = tid ? `&ticket_id=${encodeURIComponent(tid)}` : '';
        const out = await requestJson(`/admin/api/history?limit=${limit}${suffix}`);
        renderJson('history', out);
      } finally {
        toggleLoading('btn-history', false);
      }
    }

    async function retryTicket() {
      const id = document.getElementById('retryTicket').value.trim();
      if (!id) return;
      toggleLoading('btn-retry', true);
      try {
        const out = await requestJson(
          `/admin/api/retry/${encodeURIComponent(id)}`,
          { method: 'POST' },
        );
        renderJson('actions', out);
      } finally {
        toggleLoading('btn-retry', false);
      }
    }

    async function drainDlq() {
      toggleLoading('btn-drain', true);
      try {
        const limit = encodeURIComponent(document.getElementById('drainLimit').value || '100');
        const out = await requestJson(`/admin/api/dlq/drain?limit=${limit}`, { method: 'POST' });
        renderJson('actions', out);
        
        loadQueue();
        loadHistory();
      } finally {
        toggleLoading('btn-drain', false);
      }
    }

    async function loadAll() {
      await Promise.all([loadQueue(), loadHistory()]);
    }
    
    // Auto-load if token is saved in browser / optional
    // loadAll();
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.get("/admin/api/queue/stats")
async def admin_queue_stats(request: Request) -> dict[str, object]:
    settings = _settings_or_503(request)
    _verify_admin_auth(request, settings)
    try:
        stats = await get_queue_stats(settings)
    except Exception as exc:
        log.warning("admin.queue_stats_unavailable")
        raise HTTPException(status_code=503, detail="queue_unavailable") from exc
    return {str(k): v for k, v in stats.items()}


@router.get("/admin/api/history")
async def admin_history(
    request: Request,
    limit: int | None = None,
    ticket_id: int | None = None,
) -> dict[str, object]:
    settings = _settings_or_503(request)
    _verify_admin_auth(request, settings)

    resolved_limit = limit if limit is not None else settings.admin.history_limit
    bounded_limit = max(1, min(int(resolved_limit), 5000))
    try:
        items = await read_history(settings, limit=bounded_limit, ticket_id=ticket_id)
    except Exception as exc:
        log.warning("admin.history_unavailable")
        raise HTTPException(status_code=503, detail="history_unavailable") from exc
    return {"status": "ok", "count": len(items), "items": items}


@router.post("/admin/api/retry/{ticket_id}")
async def admin_retry_ticket(request: Request, ticket_id: int) -> dict[str, object]:
    settings = _settings_or_503(request)
    _verify_admin_auth(request, settings)

    payload: dict[str, object] = {
        "ticket_id": ticket_id,
        REQUEST_ID_KEY: getattr(request.state, "request_id", None),
    }
    try:
        await _dispatch_ticket(
            delivery_id=None,
            payload_for_job=payload,
            settings=settings,
        )
    except Exception as exc:
        log.warning("admin.retry_dispatch_unavailable", ticket_id=ticket_id)
        raise HTTPException(status_code=503, detail="queue_unavailable") from exc
    return {"status": "accepted", "ticket_id": ticket_id}


@router.post("/admin/api/dlq/drain")
async def admin_drain_dlq(request: Request, limit: int = 100) -> dict[str, object]:
    settings = _settings_or_503(request)
    _verify_admin_auth(request, settings)

    bounded_limit = max(1, min(int(limit), 1000))
    try:
        drained = await drain_dlq(settings, limit=bounded_limit)
    except Exception as exc:
        log.warning("admin.dlq_unavailable")
        raise HTTPException(status_code=503, detail="dlq_unavailable") from exc
    return {"status": "ok", "drained": drained}
