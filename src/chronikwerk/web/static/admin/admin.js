// frontend/admin/dom.ts
var qs = (selector, parent = document) => parent.querySelector(selector);
var qsa = (selector, parent = document) => [...parent.querySelectorAll(selector)];

// frontend/admin/pending.ts
function setButtonPending(button, pending) {
  if (!button) return;
  if (pending) {
    button.dataset.idleLabel ??= button.textContent ?? "";
    if (button.dataset.pending) button.textContent = button.dataset.pending;
    button.setAttribute("aria-busy", "true");
  } else {
    if (button.dataset.idleLabel !== void 0) button.textContent = button.dataset.idleLabel;
    button.removeAttribute("aria-busy");
  }
  button.disabled = pending;
}

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
function initPendingForms() {
  qsa("[data-pending-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      if (event.defaultPrevented) return;
      form.dataset.submitting = "true";
      form.setAttribute("aria-busy", "true");
      setButtonPending(qs('button[type="submit"]', form), true);
    });
  });
  window.addEventListener("pageshow", () => {
    qsa("[data-pending-form]").forEach((form) => {
      delete form.dataset.submitting;
      form.removeAttribute("aria-busy");
      setButtonPending(qs('button[type="submit"]', form), false);
    });
  });
}

// frontend/admin/http.ts
var csrfToken = () => qs('meta[name="csrf-token"]')?.content ?? "";
var requiresCsrfToken = (method) => !["GET", "HEAD"].includes((method ?? "GET").toUpperCase());
async function adminFetch(url, options = {}) {
  const headers = new Headers(options.headers ?? {});
  if (requiresCsrfToken(options.method)) {
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
var draftValue = (values, path) => Object.entries(values).find(([candidate]) => candidate === path)?.[1];
function formSecurityAcknowledged(form) {
  return form.elements.security_acknowledged.checked;
}
var configDraftEntries = (form) => {
  const entries = [];
  qsa(".config-field", form).forEach((row) => {
    const control = configControl(row);
    const path = row.dataset.path;
    if (control && !control.disabled && path) entries.push([path, control.value]);
  });
  return entries;
};
function preserveConfigDraft() {
  const form = qs("[data-config-form]");
  if (!form) return;
  const entries = configDraftEntries(form);
  try {
    window.sessionStorage.setItem(
      configDraftKey,
      JSON.stringify({
        values: Object.fromEntries(entries),
        securityAcknowledged: formSecurityAcknowledged(form)
      })
    );
  } catch {
    return;
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
      const value = path ? draftValue(draft.values, path) : void 0;
      if (control && !control.disabled && value !== void 0) control.value = String(value);
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
  const entries = [];
  for (const row of qsa(".config-field", form)) {
    const entry = configEntry(row);
    if (entry) entries.push(entry);
  }
  return Object.fromEntries(entries);
};
var fieldChanged = (row) => {
  const control = configControl(row);
  if (!control || control.disabled) return false;
  const value = parsedConfigValue(row.dataset.kind, control.value);
  return JSON.stringify(value) !== JSON.stringify(JSON.parse(row.dataset.original ?? "null"));
};
var updateConfigChangeCount = (form) => {
  const rows = qsa(".config-field", form);
  let count = 0;
  rows.forEach((row) => {
    const changed = fieldChanged(row);
    row.dataset.changed = String(changed);
    if (changed) count += 1;
  });
  const output = qs("[data-change-count]", form);
  if (output) {
    const label = count === 0 ? output.dataset.zero : count === 1 ? output.dataset.one : output.dataset.many;
    output.textContent = (label ?? "").replace("{count}", String(count));
  }
  const acknowledgement = qs("[data-security-ack]", form);
  if (acknowledgement) acknowledgement.hidden = !rows.some((row) => row.dataset.security === "true" && (fieldChanged(row) || row.dataset.managed === "true"));
  return count;
};
var clearValidationFeedback = (form, errorSummary) => {
  qsa(".config-field", form).forEach((row) => {
    qs("[data-field-error]", row)?.setAttribute("hidden", "");
    configControl(row)?.removeAttribute("aria-invalid");
  });
  if (errorSummary) errorSummary.hidden = true;
};
var showConfigStageResult = (form, response, data) => {
  const result = qs("[data-config-result]");
  if (!result) return;
  result.textContent = response.ok ? `${result.dataset.success ?? ""} ${data.revision ?? ""}`.trim() : data.message ?? "";
  result.className = `inline-result ${response.ok ? "banner--success" : "banner--error"}`;
  if (response.ok && data.revision) {
    form.dataset.revision = data.revision;
    const banner = qs("[data-config-staged]");
    const revision = qs("[data-staged-revision]");
    if (banner) banner.hidden = false;
    if (revision) revision.textContent = data.revision;
  }
};
var showValidationError = (form, path, message) => {
  const row = qsa(".config-field", form).find((node) => node.dataset.path === path);
  if (!row) return;
  const error = qs("[data-field-error]", row);
  if (error) {
    error.textContent = message;
    error.hidden = false;
  }
  configControl(row)?.setAttribute("aria-invalid", "true");
};
var showValidationErrors = (form, errorSummary, data) => {
  for (const { path, message } of data.errors ?? []) showValidationError(form, path, message);
  if (!errorSummary) return;
  errorSummary.textContent = data.message ?? data.errors?.map(({ message }) => message).join(" ") ?? "";
  errorSummary.hidden = false;
  errorSummary.focus();
};
var configReviewRow = (path, before, after) => {
  const row = document.createElement("tr");
  row.dataset.path = path;
  const labels = qs("[data-config-review]")?.dataset;
  const headings = [labels?.labelPath, labels?.labelBefore, labels?.labelAfter];
  [path, JSON.stringify(before), JSON.stringify(after)].forEach((value, index) => {
    const cell = document.createElement("td");
    cell.textContent = value;
    cell.dataset.label = headings[index] ?? "";
    row.append(cell);
  });
  return row;
};
var updateConfigReviewState = (review, diffLength) => {
  const empty = diffLength === 0;
  const status = qs("[data-config-review-status]", review);
  const region = qs("[data-config-diff-region]", review);
  const stageButton = qs("[data-config-stage]", review);
  if (status) {
    status.textContent = "";
    if (empty) status.textContent = review.dataset.noChanges ?? "";
  }
  if (region) region.hidden = empty;
  if (stageButton) stageButton.disabled = empty;
};
var showConfigReview = (form, data) => {
  const tbody = qs("[data-config-diff]");
  const review = qs("[data-config-review]");
  if (!tbody || !review) return;
  const diff = data.diff ?? [];
  tbody.replaceChildren(...diff.map((item) => configReviewRow(item.path, item.before, item.after)));
  updateConfigReviewState(review, diff.length);
  review.hidden = false;
  review.tabIndex = -1;
  review.focus({ preventScroll: true });
  review.scrollIntoView({ block: "nearest" });
};
var requestConfigValidation = async (form, errorSummary, isCurrent) => {
  try {
    const response = await adminFetch("/admin/api/v1/config/validate", {
      method: "POST",
      body: JSON.stringify({
        values: configValues(form),
        security_acknowledged: formSecurityAcknowledged(form)
      })
    });
    return { response, data: await response.json() };
  } catch (error) {
    const sessionExpired = error instanceof Error && error.message === "session_expired";
    if (!sessionExpired && errorSummary && isCurrent()) {
      errorSummary.textContent = errorSummary.dataset.networkError ?? "";
      errorSummary.hidden = false;
      errorSummary.focus();
    }
    return null;
  }
};
var handleConfigValidationResult = (form, errorSummary, result) => {
  if (!result) return null;
  if (!result.response.ok) {
    showValidationErrors(form, errorSummary, result.data);
    return null;
  }
  showConfigReview(form, result.data);
  return result.data;
};
var stageValidatedConfig = async (form, overlay) => {
  try {
    const response = await adminFetch("/admin/api/v1/config/staged", {
      method: "PUT",
      headers: { "If-Match": form.dataset.revision ?? "" },
      body: JSON.stringify({ overlay, security_acknowledged: formSecurityAcknowledged(form) })
    });
    showConfigStageResult(form, response, await response.json());
    return response.ok;
  } catch (error) {
    const sessionExpired = error instanceof Error && error.message === "session_expired";
    const result = qs("[data-config-result]");
    if (!sessionExpired && result) {
      result.textContent = result.dataset.networkError ?? "";
      result.className = "inline-result banner--error";
    }
    return false;
  }
};
var formSignature = (form) => JSON.stringify({
  values: configDraftEntries(form),
  acknowledged: formSecurityAcknowledged(form)
});
var configFeedback = (form, state) => {
  const feedback = qs("[data-config-feedback]", form);
  if (feedback) feedback.textContent = feedback.dataset[state] ?? "";
};
var resetConfigEdits = (form) => {
  qsa(".config-field", form).forEach((row) => {
    const control = configControl(row);
    if (control && !control.disabled) control.value = String(JSON.parse(row.dataset.original ?? "null"));
  });
  form.elements.security_acknowledged.checked = false;
  clearValidationFeedback(form, qs("[data-config-errors]", form));
};
var acceptStagedValues = (form) => {
  qsa(".config-field", form).forEach((row) => {
    const entry = configEntry(row);
    if (!entry) return;
    row.dataset.original = JSON.stringify(entry[1]);
    row.dataset.managed = "true";
    const provenance = qs(".provenance", row);
    if (provenance) {
      provenance.textContent = form.dataset.stagedLabel ?? provenance.textContent;
      provenance.classList.add("provenance--staged");
    }
  });
};
var lockConfigControls = (form) => {
  const controls = qsa("input, select", form);
  const enabled = controls.filter((control) => !control.disabled);
  enabled.forEach((control) => {
    control.disabled = true;
  });
  return () => enabled.forEach((control) => {
    control.disabled = false;
  });
};
function initConfigForm() {
  restoreConfigDraft();
  const form = qs("[data-config-form]");
  if (!form) return;
  const state = { generation: 0, overlay: null, signature: null, busy: false };
  const review = qs("[data-config-review]");
  const stage = qs("[data-config-stage]");
  const submit = qs('button[type="submit"]', form);
  const reset = qs("[data-config-reset]", form);
  const refresh = () => {
    const count = updateConfigChangeCount(form);
    if (submit) submit.disabled = state.busy || count === 0;
    if (reset) reset.disabled = state.busy || count === 0;
    if (stage) stage.disabled = state.busy || state.overlay === null;
  };
  const invalidate = () => {
    const hadReview = state.signature !== null || state.busy;
    state.generation += 1;
    state.overlay = null;
    state.signature = null;
    if (review) review.hidden = true;
    configFeedback(form, hadReview ? "invalidated" : "");
    refresh();
  };
  form.addEventListener("input", invalidate);
  form.addEventListener("change", invalidate);
  reset?.addEventListener("click", () => {
    if (state.busy) return;
    resetConfigEdits(form);
    invalidate();
    configFeedback(form, "");
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!state.busy && changedFields(form)) void validateCurrentConfig(form, state, submit, refresh);
  });
  stage?.addEventListener("click", () => {
    if (state.busy || state.overlay === null) return;
    if (state.signature !== formSignature(form)) {
      invalidate();
      return;
    }
    void stageCurrentConfig(form, state, stage, submit, refresh);
  });
  refresh();
}
var changedFields = (form) => qsa(".config-field", form).some(fieldChanged);
var validateCurrentConfig = async (form, state, submit, refresh) => {
  const generation = state.generation;
  const signature = formSignature(form);
  const isCurrent = () => generation === state.generation && signature === formSignature(form);
  const errorSummary = qs("[data-config-errors]", form);
  clearValidationFeedback(form, errorSummary);
  state.overlay = null;
  state.signature = null;
  const review = qs("[data-config-review]");
  if (review) review.hidden = true;
  state.busy = true;
  refresh();
  setButtonPending(submit, true);
  configFeedback(form, "validating");
  const result = await requestConfigValidation(form, errorSummary, isCurrent);
  state.busy = false;
  setButtonPending(submit, false);
  if (isCurrent()) {
    const data = handleConfigValidationResult(form, errorSummary, result);
    if (data?.diff?.length) {
      state.overlay = data.overlay ?? null;
      state.signature = signature;
    }
    configFeedback(form, "");
  } else configFeedback(form, "invalidated");
  refresh();
};
var stageCurrentConfig = async (form, state, stage, submit, refresh) => {
  state.busy = true;
  refresh();
  setButtonPending(stage, true);
  const unlock = lockConfigControls(form);
  configFeedback(form, "staging");
  const succeeded = await stageValidatedConfig(form, state.overlay);
  unlock();
  state.busy = false;
  setButtonPending(stage, false);
  if (succeeded) {
    acceptStagedValues(form);
    state.overlay = null;
    state.signature = null;
    const result = qs("[data-config-result]");
    result?.setAttribute("tabindex", "-1");
    result?.focus();
  }
  if (submit) submit.disabled = false;
  configFeedback(form, "");
  refresh();
};

// frontend/admin/overview.ts
var overviewElements = () => {
  const status = qs("[data-refresh-status]");
  const running = qs("[data-admission-running]");
  const pending = qs("[data-admission-pending]");
  const refreshed = qs("[data-last-refresh]");
  if (!status) return null;
  if (!running) return null;
  if (!pending) return null;
  if (!refreshed) return null;
  return { status, running, pending, refreshed };
};
var setCapacityBar = (selector, current, max) => {
  if (max === void 0 || max <= 0) return;
  const root = qs(selector);
  if (!root) return;
  if (root instanceof HTMLProgressElement) {
    root.max = max;
    root.value = current;
    return;
  }
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
  const maxRunning = qs("[data-admission-max-running]");
  const maxPending = qs("[data-admission-max-pending]");
  if (maxRunning && data.admission.max_running !== void 0) maxRunning.textContent = String(data.admission.max_running);
  if (maxPending && data.admission.max_pending !== void 0) maxPending.textContent = String(data.admission.max_pending);
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
  let refreshing = false;
  const refresh = async () => {
    if (refreshing) return;
    refreshing = true;
    try {
      await refreshOverview();
    } finally {
      refreshing = false;
    }
  };
  window.setInterval(() => {
    void refresh();
  }, 3e4);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void refresh();
  });
}
var storageMessage = (element, writable) => {
  if (writable) return element.dataset.success ?? "";
  return element.dataset.error ?? "";
};
var showStorageResult = (state, result, writable) => {
  result.textContent = storageMessage(result, writable);
  if (!state) return;
  state.textContent = storageMessage(state, writable);
  state.className = writable ? "state-value state-value--success" : "state-value state-value--error";
};
var showStorageCheckTime = (checkedAt) => {
  if (!checkedAt) return;
  checkedAt.hidden = false;
  checkedAt.textContent = new Intl.DateTimeFormat(document.documentElement.lang, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short"
  }).format(/* @__PURE__ */ new Date());
};
var runStorageCheck = async (button) => {
  const result = qs("[data-storage-result]");
  const state = qs("[data-storage-state]");
  const checkedAt = qs("[data-storage-time]");
  if (!result) return;
  if (button.disabled) return;
  setButtonPending(button, true);
  try {
    const response = await adminFetch("/admin/api/v1/status/storage-check", { method: "POST" });
    if (!response.ok) throw new Error("storage_check_failed");
    const data = await response.json();
    showStorageResult(state, result, Boolean(data.storage?.writable));
    showStorageCheckTime(checkedAt);
  } catch (error) {
    const sessionExpired = error instanceof Error && error.message === "session_expired";
    if (!sessionExpired) showStorageResult(state, result, false);
  } finally {
    setButtonPending(button, false);
  }
};
function initStorageCheck() {
  const button = qs("[data-storage-check]");
  button?.addEventListener("click", () => {
    void runStorageCheck(button);
  });
}

// frontend/admin/reauth.ts
var showReauthError = (error, message) => {
  if (!error) return;
  error.textContent = message;
  error.hidden = false;
};
var submitReauthForm = async (form, event) => {
  event.preventDefault();
  if (form.getAttribute("aria-busy") === "true") return;
  const error = qs("[data-reauth-error]", form);
  const submit = qs('button[type="submit"]', form);
  if (error) error.hidden = true;
  setButtonPending(submit, true);
  form.setAttribute("aria-busy", "true");
  try {
    const response = await fetch("/admin/api/v1/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ access_token: new FormData(form).get("access_token") })
    });
    if (!response.ok) {
      showReauthError(error, error?.dataset.invalid ?? "");
      return;
    }
    preserveConfigDraft();
    window.location.reload();
  } catch {
    showReauthError(error, error?.dataset.networkError ?? "");
  } finally {
    form.removeAttribute("aria-busy");
    setButtonPending(submit, false);
  }
};
function initReauthForm() {
  const form = qs("[data-reauth-form]");
  form?.addEventListener("submit", (event) => {
    void submitReauthForm(form, event);
  });
}

// frontend/admin.ts
initAutoSubmit();
initDialogClose();
initPendingForms();
initReauthForm();
initStorageCheck();
initOverview();
initConfigForm();
