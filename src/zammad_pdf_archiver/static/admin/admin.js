const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

async function adminFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (!['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase())) {
    headers.set('X-CSRF-Token', csrfToken());
  }
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(url, {...options, headers});
  if (response.status === 401) {
    const dialog = document.querySelector('#reauth-dialog');
    if (dialog && !dialog.open) dialog.showModal();
    throw new Error('session_expired');
  }
  return response;
}

document.querySelectorAll('[data-auto-submit]').forEach((control) => {
  control.addEventListener('change', () => control.form?.requestSubmit());
});

document.querySelector('[data-dialog-close]')?.addEventListener('click', () => {
  document.querySelector('#reauth-dialog')?.close();
});

const configDraftKey = 'zpa-admin-config-draft';

function preserveConfigDraft() {
  const form = document.querySelector('[data-config-form]');
  if (!form) return;
  const values = {};
  form.querySelectorAll('.config-field').forEach((row) => {
    const control = row.querySelector('input:not([type="checkbox"]), select');
    if (control && !control.disabled) values[row.dataset.path] = control.value;
  });
  try {
    window.sessionStorage.setItem(configDraftKey, JSON.stringify({
      values,
      securityAcknowledged: form.elements.security_acknowledged.checked
    }));
  } catch (_error) {
    // Reauthentication still succeeds when browser storage is unavailable.
  }
}

function restoreConfigDraft() {
  const form = document.querySelector('[data-config-form]');
  if (!form) return;
  try {
    const raw = window.sessionStorage.getItem(configDraftKey);
    if (!raw) return;
    window.sessionStorage.removeItem(configDraftKey);
    const draft = JSON.parse(raw);
    form.querySelectorAll('.config-field').forEach((row) => {
      const control = row.querySelector('input:not([type="checkbox"]), select');
      if (control && !control.disabled && Object.hasOwn(draft.values, row.dataset.path)) {
        control.value = draft.values[row.dataset.path];
      }
    });
    form.elements.security_acknowledged.checked = Boolean(draft.securityAcknowledged);
  } catch (_error) {
    window.sessionStorage.removeItem(configDraftKey);
  }
}

document.querySelector('[data-reauth-form]')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const error = form.querySelector('[data-reauth-error]');
  const response = await fetch('/admin/api/v1/session', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({access_token: new FormData(form).get('access_token')})
  });
  if (!response.ok) { error.hidden = false; return; }
  preserveConfigDraft();
  window.location.reload();
});

document.querySelector('[data-storage-check]')?.addEventListener('click', async (event) => {
  const button = event.currentTarget;
  const result = document.querySelector('[data-storage-result]');
  button.disabled = true;
  try {
    const response = await adminFetch('/admin/api/v1/status/storage-check', {method: 'POST'});
    const data = await response.json();
    result.textContent = data.storage?.writable ? result.dataset.success : result.dataset.error;
    result.className = `inline-result ${data.storage?.writable ? 'banner--success' : 'banner--error'}`;
  } finally { button.disabled = false; }
});

if (document.querySelector('[data-overview]')) {
  window.setInterval(async () => {
    if (document.visibilityState !== 'visible') return;
    const status = document.querySelector('[data-refresh-status]');
    try {
      const response = await adminFetch('/admin/api/v1/status');
      if (!response.ok) throw new Error('status_refresh_failed');
      const data = await response.json();
      document.querySelector('[data-admission-running]').textContent = data.admission.running;
      document.querySelector('[data-admission-pending]').textContent = data.admission.pending;
      const refreshed = document.querySelector('[data-last-refresh]');
      const now = new Date();
      refreshed.dateTime = now.toISOString();
      refreshed.textContent = new Intl.DateTimeFormat(document.documentElement.lang, {
        dateStyle: 'short', timeStyle: 'medium', timeZone: 'UTC'
      }).format(now) + ' UTC';
      status.textContent = '';
    } catch (error) {
      if (error.message !== 'session_expired') status.textContent = status.dataset.error;
    }
  }, 30_000);
}

const configForm = document.querySelector('[data-config-form]');
let validatedOverlay = null;
restoreConfigDraft();

function configValues(form) {
  const values = {};
  form.querySelectorAll('.config-field').forEach((row) => {
    const control = row.querySelector('input:not([type="checkbox"]), select');
    if (!control || control.disabled) return;
    const kind = row.dataset.kind;
    let value = control.value;
    if (kind === 'boolean') value = value === 'true';
    if (kind === 'integer') value = Number.parseInt(value, 10);
    if (kind === 'number') value = Number.parseFloat(value);
    const original = JSON.parse(row.dataset.original);
    if (row.dataset.managed === 'true' || JSON.stringify(value) !== JSON.stringify(original)) {
      values[row.dataset.path] = value;
    }
  });
  return values;
}

configForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  configForm.querySelectorAll('[data-field-error]').forEach((node) => { node.hidden = true; });
  const response = await adminFetch('/admin/api/v1/config/validate', {
    method: 'POST',
    body: JSON.stringify({
      values: configValues(configForm),
      security_acknowledged: configForm.elements.security_acknowledged.checked
    })
  });
  const data = await response.json();
  if (!response.ok) {
    (data.errors || []).forEach((item) => {
      const row = [...configForm.querySelectorAll('.config-field')].find((node) => node.dataset.path === item.path);
      const error = row?.querySelector('[data-field-error]');
      if (error) { error.textContent = item.message; error.hidden = false; }
    });
    return;
  }
  validatedOverlay = data.overlay;
  const tbody = document.querySelector('[data-config-diff]');
  tbody.replaceChildren(...data.diff.map((item) => {
    const row = document.createElement('tr');
    [item.path, JSON.stringify(item.before), JSON.stringify(item.after)].forEach((value) => {
      const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
    });
    return row;
  }));
  document.querySelector('[data-config-review]').hidden = false;
  document.querySelector('[data-config-review]').scrollIntoView({block: 'start'});
});

document.querySelector('[data-config-stage]')?.addEventListener('click', async (event) => {
  if (!validatedOverlay) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const response = await adminFetch('/admin/api/v1/config/staged', {
      method: 'PUT',
      headers: {'If-Match': configForm.dataset.revision},
      body: JSON.stringify({
        overlay: validatedOverlay,
        security_acknowledged: configForm.elements.security_acknowledged.checked
      })
    });
    const data = await response.json();
    const result = document.querySelector('[data-config-result]');
    result.textContent = response.ok ? `${result.dataset.success} ${data.revision}` : data.message;
    result.className = `inline-result ${response.ok ? 'banner--success' : 'banner--error'}`;
    if (response.ok) configForm.dataset.revision = data.revision;
  } finally { button.disabled = false; }
});
