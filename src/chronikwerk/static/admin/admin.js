// frontend/admin/dom.ts
var qs = (selector, parent = document) => parent.querySelector(selector);
var qsa = (selector, parent = document) => [...parent.querySelectorAll(selector)];

// frontend/admin/boot.ts
function initAutoSubmit() {
  qsa("[data-auto-submit]").forEach((control) => {
    control.addEventListener("change", () => {
      if (control instanceof HTMLInputElement || control instanceof HTMLSelectElement) {
        control.form?.requestSubmit();
      }
    });
  });
}
function initDialogClose() {
  qs("[data-dialog-close]")?.addEventListener("click", () => {
    qs("#reauth-dialog")?.close();
  });
}

// frontend/admin/http.ts
var csrfToken = () => qs('meta[name="csrf-token"]')?.content || "";
async function adminFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (!["GET", "HEAD"].includes((options.method || "GET").toUpperCase())) {
    headers.set("X-CSRF-Token", csrfToken());
  }
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    const dialog = qs("#reauth-dialog");
    if (dialog && !dialog.open) dialog.showModal();
    throw new Error("session_expired");
  }
  return response;
}

// frontend/admin/config.ts
var configDraftKey = "chronikwerk-admin-config-draft";
function configControl(row) {
  return qs('input:not([type="checkbox"]), select', row);
}
function formSecurityAcknowledged(form) {
  return form.elements.security_acknowledged.checked;
}
function preserveConfigDraft() {
  const form = qs("[data-config-form]");
  if (!form) return;
  const values = {};
  qsa(".config-field", form).forEach((row) => {
    const control = configControl(row);
    const path = row.dataset.path;
    if (control && !control.disabled && path) values[path] = control.value;
  });
  try {
    window.sessionStorage.setItem(
      configDraftKey,
      JSON.stringify({
        values,
        securityAcknowledged: formSecurityAcknowledged(form)
      })
    );
  } catch {
  }
}
function restoreConfigDraft() {
  const form = qs("[data-config-form]");
  if (!form) return;
  try {
    const raw = window.sessionStorage.getItem(configDraftKey);
    if (!raw) return;
    window.sessionStorage.removeItem(configDraftKey);
    const draft = JSON.parse(raw);
    qsa(".config-field", form).forEach((row) => {
      const control = configControl(row);
      const path = row.dataset.path;
      if (control && !control.disabled && path && Object.hasOwn(draft.values, path)) {
        control.value = String(draft.values[path]);
      }
    });
    form.elements.security_acknowledged.checked = Boolean(draft.securityAcknowledged);
  } catch {
    window.sessionStorage.removeItem(configDraftKey);
  }
}
var parsedConfigValue = (kind, rawValue) => {
  if (kind === "boolean") return rawValue === "true";
  if (kind === "integer") return Number.parseInt(rawValue, 10);
  if (kind === "number") return Number.parseFloat(rawValue);
  return rawValue;
};
var configEntry = (row) => {
  const control = configControl(row);
  const path = row.dataset.path;
  if (!control || control.disabled || !path) return null;
  const value = parsedConfigValue(row.dataset.kind, control.value);
  const original = JSON.parse(row.dataset.original ?? "null");
  if (row.dataset.managed !== "true" && JSON.stringify(value) === JSON.stringify(original)) {
    return null;
  }
  return [path, value];
};
var configValues = (form) => {
  const values = {};
  for (const row of qsa(".config-field", form)) {
    const entry = configEntry(row);
    if (entry) values[entry[0]] = entry[1];
  }
  return values;
};
function showConfigStageResult(form, response, data) {
  const result = qs("[data-config-result]");
  if (!result) return;
  result.textContent = response.ok ? `${result.dataset.success ?? ""} ${data.revision ?? ""}`.trim() : data.message ?? "";
  result.className = `inline-result ${response.ok ? "banner--success" : "banner--error"}`;
  if (response.ok && data.revision) form.dataset.revision = data.revision;
}
function initConfigForm() {
  const configForm = qs("[data-config-form]");
  let validatedOverlay = null;
  restoreConfigDraft();
  const updateConfigChangeCount = () => {
    if (!configForm) return;
    const count = qsa(".config-field", configForm).filter((row) => {
      const control = configControl(row);
      if (!control || control.disabled) return false;
      const value = parsedConfigValue(row.dataset.kind, control.value);
      const original = JSON.parse(row.dataset.original ?? "null");
      return JSON.stringify(value) !== JSON.stringify(original);
    }).length;
    const output = qs("[data-change-count]", configForm);
    if (!output) return;
    if (count === 0) output.textContent = output.dataset.zero ?? "";
    else if (count === 1) output.textContent = output.dataset.one ?? "";
    else output.textContent = (output.dataset.many ?? "").replace("{count}", String(count));
  };
  const invalidateConfigReview = () => {
    validatedOverlay = null;
    const review = qs("[data-config-review]");
    if (review) review.hidden = true;
    updateConfigChangeCount();
  };
  configForm?.addEventListener("input", invalidateConfigReview);
  configForm?.addEventListener("change", invalidateConfigReview);
  updateConfigChangeCount();
  configForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter instanceof HTMLButtonElement ? event.submitter : null;
    const errorSummary = qs("[data-config-errors]", configForm);
    qsa(".config-field", configForm).forEach((row) => {
      const error = qs("[data-field-error]", row);
      const control = configControl(row);
      if (error) error.hidden = true;
      control?.removeAttribute("aria-invalid");
    });
    if (errorSummary) errorSummary.hidden = true;
    if (submit) {
      submit.disabled = true;
      submit.setAttribute("aria-busy", "true");
    }
    let response;
    let data;
    try {
      response = await adminFetch("/admin/api/v1/config/validate", {
        method: "POST",
        body: JSON.stringify({
          values: configValues(configForm),
          security_acknowledged: formSecurityAcknowledged(configForm)
        })
      });
      data = await response.json();
    } catch (error) {
      const sessionExpired = error instanceof Error && error.message === "session_expired";
      if (!sessionExpired && errorSummary) {
        errorSummary.textContent = errorSummary.dataset.networkError ?? "";
        errorSummary.hidden = false;
        errorSummary.focus();
      }
      return;
    } finally {
      if (submit) {
        submit.removeAttribute("aria-busy");
        submit.disabled = false;
      }
    }
    if (!response.ok) {
      (data.errors ?? []).forEach((item) => {
        const row = qsa(".config-field", configForm).find(
          (node) => node.dataset.path === item.path
        );
        const error = row ? qs("[data-field-error]", row) : null;
        const control = row ? configControl(row) : null;
        if (error) {
          error.textContent = item.message;
          error.hidden = false;
        }
        control?.setAttribute("aria-invalid", "true");
      });
      if (errorSummary) {
        errorSummary.textContent = data.message ?? data.errors?.map(({ message }) => message).join(" ") ?? "";
        errorSummary.hidden = false;
        errorSummary.focus();
      }
      return;
    }
    validatedOverlay = data.overlay ?? null;
    const tbody = qs("[data-config-diff]");
    const review = qs("[data-config-review]");
    if (!tbody || !review) return;
    const diff = data.diff ?? [];
    tbody.replaceChildren(...diff.map((item) => {
      const row = document.createElement("tr");
      [item.path, JSON.stringify(item.before), JSON.stringify(item.after)].forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      });
      return row;
    }));
    const status = qs("[data-config-review-status]", review);
    const region = qs("[data-config-diff-region]", review);
    const stageButton = qs("[data-config-stage]", review);
    if (status) status.textContent = diff.length === 0 ? review.dataset.noChanges ?? "" : "";
    if (region) region.hidden = diff.length === 0;
    if (stageButton) stageButton.disabled = diff.length === 0;
    review.hidden = false;
    review.scrollIntoView({ block: "start" });
  });
  async function stageValidatedConfig(event) {
    if (!validatedOverlay || !configForm) return;
    const button = event.currentTarget;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      const response = await adminFetch("/admin/api/v1/config/staged", {
        method: "PUT",
        headers: { "If-Match": configForm.dataset.revision ?? "" },
        body: JSON.stringify({
          overlay: validatedOverlay,
          security_acknowledged: formSecurityAcknowledged(configForm)
        })
      });
      const data = await response.json();
      showConfigStageResult(configForm, response, data);
    } catch (error) {
      const sessionExpired = error instanceof Error && error.message === "session_expired";
      const result = qs("[data-config-result]");
      if (!sessionExpired && result) {
        result.textContent = result.dataset.networkError ?? "";
        result.className = "inline-result banner--error";
      }
    } finally {
      button.removeAttribute("aria-busy");
      button.disabled = false;
    }
  }
  qs("[data-config-stage]")?.addEventListener("click", stageValidatedConfig);
}

// frontend/admin/overview.ts
var overviewElements = () => {
  const status = qs("[data-refresh-status]");
  const running = qs("[data-admission-running]");
  const pending = qs("[data-admission-pending]");
  const refreshed = qs("[data-last-refresh]");
  if (!status || !running || !pending || !refreshed) return null;
  return { status, running, pending, refreshed };
};
var setCapacityBar = (selector, current, max) => {
  if (max === void 0 || max <= 0) return;
  const root = qs(selector);
  if (!root) return;
  const fill = qs("i", root) ?? root;
  const pct = Math.min(100, Math.max(0, current / max * 100));
  fill.style.width = `${pct}%`;
};
var showOverviewStatus = (elements, data) => {
  const running = Number(data.admission.running);
  const pending = Number(data.admission.pending);
  elements.running.textContent = String(data.admission.running);
  elements.pending.textContent = String(data.admission.pending);
  setCapacityBar("[data-capacity-running-bar]", running, data.admission.max_running);
  setCapacityBar("[data-capacity-pending-bar]", pending, data.admission.max_pending);
  const now = /* @__PURE__ */ new Date();
  elements.refreshed.dateTime = now.toISOString();
  elements.refreshed.textContent = `${new Intl.DateTimeFormat(document.documentElement.lang, {
    dateStyle: "short",
    timeStyle: "medium",
    timeZone: "UTC"
  }).format(now)} UTC`;
  elements.status.textContent = "";
};
var refreshOverview = async () => {
  if (document.visibilityState !== "visible") return;
  const elements = overviewElements();
  if (!elements) return;
  try {
    const response = await adminFetch("/admin/api/v1/status");
    if (!response.ok) throw new Error("status_refresh_failed");
    showOverviewStatus(elements, await response.json());
  } catch (error) {
    const sessionExpired = error instanceof Error && error.message === "session_expired";
    if (!sessionExpired) elements.status.textContent = elements.status.dataset.error ?? "";
  }
};
function initOverview() {
  if (!qs("[data-overview]")) return;
  window.setInterval(refreshOverview, 3e4);
}
function initStorageCheck() {
  qs("[data-storage-check]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const result = qs("[data-storage-result]");
    const state = qs("[data-storage-state]");
    const checkedAt = qs("[data-storage-time]");
    if (!result) return;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      const response = await adminFetch("/admin/api/v1/status/storage-check", { method: "POST" });
      if (!response.ok) throw new Error("storage_check_failed");
      const data = await response.json();
      const writable = Boolean(data.storage?.writable);
      result.textContent = writable ? result.dataset.success ?? "" : result.dataset.error ?? "";
      if (state) {
        state.textContent = writable ? state.dataset.success ?? "" : state.dataset.error ?? "";
        state.className = `state-value ${writable ? "state-value--success" : "state-value--error"}`;
      }
      if (checkedAt) {
        checkedAt.hidden = false;
        checkedAt.textContent = new Intl.DateTimeFormat(document.documentElement.lang, {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          timeZone: "UTC",
          timeZoneName: "short"
        }).format(/* @__PURE__ */ new Date());
      }
    } catch (error) {
      const sessionExpired = error instanceof Error && error.message === "session_expired";
      if (!sessionExpired) {
        result.textContent = result.dataset.error ?? "";
        if (state) {
          state.textContent = state.dataset.error ?? "";
          state.className = "state-value state-value--error";
        }
      }
    } finally {
      button.removeAttribute("aria-busy");
      button.disabled = false;
    }
  });
}

// frontend/admin/reauth.ts
function initReauthForm() {
  qs("[data-reauth-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const error = qs("[data-reauth-error]", form);
    const submit = qs('button[type="submit"]', form);
    if (error) error.hidden = true;
    if (submit) submit.disabled = true;
    form.setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/admin/api/v1/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_token: new FormData(form).get("access_token") })
      });
      if (!response.ok) {
        if (error) {
          error.textContent = error.dataset.invalid ?? "";
          error.hidden = false;
        }
        return;
      }
      preserveConfigDraft();
      window.location.reload();
    } catch {
      if (error) {
        error.textContent = error.dataset.networkError ?? "";
        error.hidden = false;
      }
    } finally {
      form.removeAttribute("aria-busy");
      if (submit) submit.disabled = false;
    }
  });
}

// frontend/admin.ts
initAutoSubmit();
initDialogClose();
initReauthForm();
initStorageCheck();
initOverview();
initConfigForm();
