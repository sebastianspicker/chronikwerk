/** Config form: draft preserve/restore, change count, validate, and stage. */

import { qs, qsa } from './dom';
import { adminFetch } from './http';
import { setButtonPending } from './pending';
import type {
  ConfigControl,
  ConfigDraft,
  ConfigForm,
  ConfigStageResponse,
  ConfigValidationResponse,
  ConfigValue,
  ConfigValues,
} from './types';

const configDraftKey = 'chronikwerk-admin-config-draft';

function configControl(row: HTMLElement): ConfigControl | null {
  // A field row contains one editable non-checkbox control when it participates in staging.
  return qs<ConfigControl>('input:not([type="checkbox"]), select', row);
}

const draftValue = (values: ConfigValues, path: string): ConfigValue | undefined =>
  Object.entries(values).find(([candidate]) => candidate === path)?.[1];

export function formSecurityAcknowledged(form: ConfigForm): boolean {
  // The server requires this explicit acknowledgement before accepting risky changes.
  return form.elements.security_acknowledged.checked;
}

const configDraftEntries = (form: ConfigForm): Array<[string, ConfigValue]> => {
  const entries: Array<[string, ConfigValue]> = [];
  qsa<HTMLElement>('.config-field', form).forEach((row) => {
    const control = configControl(row);
    const path = row.dataset.path;
    if (control && !control.disabled && path) entries.push([path, control.value]);
  });
  return entries;
};

export function preserveConfigDraft(): void {
  // Save only editable, non-secret values before reauthentication reloads the page.
  const form = qs<ConfigForm>('[data-config-form]');
  if (!form) return;
  const entries = configDraftEntries(form);
  try {
    window.sessionStorage.setItem(
      configDraftKey,
      JSON.stringify({
        values: Object.fromEntries(entries),
        securityAcknowledged: formSecurityAcknowledged(form),
      } satisfies ConfigDraft),
    );
  } catch {
    // Reauthentication still succeeds when browser storage is unavailable.
    return;
  }
}

export function restoreConfigDraft(): void {
  // Restore a one-time draft after successful reauthentication, then remove it from storage.
  const form = qs<ConfigForm>('[data-config-form]');
  if (!form) return;
  try {
    const raw = window.sessionStorage.getItem(configDraftKey);
    if (!raw) return;
    window.sessionStorage.removeItem(configDraftKey);
    const draft = JSON.parse(raw) as ConfigDraft;
    qsa<HTMLElement>('.config-field', form).forEach((row) => {
      const control = configControl(row);
      const path = row.dataset.path;
      const value = path ? draftValue(draft.values, path) : undefined;
      if (control && !control.disabled && value !== undefined) control.value = String(value);
    });
    form.elements.security_acknowledged.checked = Boolean(draft.securityAcknowledged);
  } catch {
    window.sessionStorage.removeItem(configDraftKey);
  }
}

const parsedConfigValue = (kind: string | undefined, rawValue: string): ConfigValue => {
  // Form controls serialize strings; recover schema values before the validation request.
  if (kind === 'boolean') return rawValue === 'true';
  if (kind === 'integer') return Number.parseInt(rawValue, 10);
  if (kind === 'number') return Number.parseFloat(rawValue);
  return rawValue;
};

const configEntry = (row: HTMLElement): readonly [string, ConfigValue] | null => {
  // Unmanaged fields are sent only when changed, preventing accidental configuration drift.
  const control = configControl(row);
  const path = row.dataset.path;
  if (!control || control.disabled || !path) return null;
  const value = parsedConfigValue(row.dataset.kind, control.value);
  const original = JSON.parse(row.dataset.original ?? 'null') as ConfigValue;
  if (row.dataset.managed !== 'true' && JSON.stringify(value) === JSON.stringify(original)) {
    return null;
  }
  return [path, value];
};

const configValues = (form: ConfigForm): ConfigValues => {
  // Assemble the sparse overlay that the server validates and stages atomically.
  const entries: Array<readonly [string, ConfigValue]> = [];
  for (const row of qsa<HTMLElement>('.config-field', form)) {
    const entry = configEntry(row);
    if (entry) entries.push(entry);
  }
  return Object.fromEntries(entries);
};

const fieldChanged = (row: HTMLElement): boolean => {
  const control = configControl(row);
  if (!control || control.disabled) return false;
  const value = parsedConfigValue(row.dataset.kind, control.value);
  return JSON.stringify(value) !== JSON.stringify(JSON.parse(row.dataset.original ?? 'null'));
};

const updateConfigChangeCount = (form: ConfigForm): number => {
  const rows = qsa<HTMLElement>('.config-field', form);
  let count = 0;
  rows.forEach((row) => {
    const changed = fieldChanged(row);
    row.dataset.changed = String(changed);
    if (changed) count += 1;
  });
  const output = qs<HTMLElement>('[data-change-count]', form);
  if (output) {
    const label = count === 0 ? output.dataset.zero : count === 1 ? output.dataset.one : output.dataset.many;
    output.textContent = (label ?? '').replace('{count}', String(count));
  }
  const acknowledgement = qs<HTMLElement>('[data-security-ack]', form);
  if (acknowledgement) acknowledgement.hidden = !rows.some((row) =>
    row.dataset.security === 'true' && (fieldChanged(row) || row.dataset.managed === 'true'));
  return count;
};

const clearValidationFeedback = (form: ConfigForm, errorSummary: HTMLElement | null): void => {
  qsa<HTMLElement>('.config-field', form).forEach((row) => {
    qs<HTMLElement>('[data-field-error]', row)?.setAttribute('hidden', '');
    configControl(row)?.removeAttribute('aria-invalid');
  });
  if (errorSummary) errorSummary.hidden = true;
};

const showConfigStageResult = (
  form: ConfigForm,
  response: Response,
  data: ConfigStageResponse,
): void => {
  // Keep UI feedback tied to the revision returned by the optimistic-concurrency endpoint.
  const result = qs<HTMLElement>('[data-config-result]');
  if (!result) return;
  result.textContent = response.ok
    ? `${result.dataset.success ?? ''} ${data.revision ?? ''}`.trim()
    : data.message ?? '';
  result.className = `inline-result ${response.ok ? 'banner--success' : 'banner--error'}`;
  if (response.ok && data.revision) {
    form.dataset.revision = data.revision;
    const banner = qs<HTMLElement>('[data-config-staged]');
    const revision = qs<HTMLElement>('[data-staged-revision]');
    if (banner) banner.hidden = false;
    if (revision) revision.textContent = data.revision;
  }
};

const showValidationError = (form: ConfigForm, path: string, message: string): void => {
  const row = qsa<HTMLElement>('.config-field', form).find((node) => node.dataset.path === path);
  if (!row) return;
  const error = qs<HTMLElement>('[data-field-error]', row);
  if (error) {
    error.textContent = message;
    error.hidden = false;
  }
  configControl(row)?.setAttribute('aria-invalid', 'true');
};

const showValidationErrors = (
  form: ConfigForm,
  errorSummary: HTMLElement | null,
  data: ConfigValidationResponse,
): void => {
  for (const {path, message} of data.errors ?? []) showValidationError(form, path, message);
  if (!errorSummary) return;
  errorSummary.textContent = data.message ?? data.errors?.map(({message}) => message).join(' ') ?? '';
  errorSummary.hidden = false;
  errorSummary.focus();
};

const configReviewRow = (path: string, before: unknown, after: unknown): HTMLTableRowElement => {
  const row = document.createElement('tr');
  row.dataset.path = path;
  const labels = qs<HTMLElement>('[data-config-review]')?.dataset;
  const headings = [labels?.labelPath, labels?.labelBefore, labels?.labelAfter];
  [path, JSON.stringify(before), JSON.stringify(after)].forEach((value, index) => {
    const cell = document.createElement('td');
    cell.textContent = value;
    cell.dataset.label = headings[index] ?? '';
    row.append(cell);
  });
  return row;
};

const updateConfigReviewState = (review: HTMLElement, diffLength: number): void => {
  const empty = diffLength === 0;
  const status = qs<HTMLElement>('[data-config-review-status]', review);
  const region = qs<HTMLElement>('[data-config-diff-region]', review);
  const stageButton = qs<HTMLButtonElement>('[data-config-stage]', review);
  if (status) {
    status.textContent = '';
    if (empty) status.textContent = review.dataset.noChanges ?? '';
  }
  if (region) region.hidden = empty;
  if (stageButton) stageButton.disabled = empty;
};

const showConfigReview = (form: ConfigForm, data: ConfigValidationResponse): void => {
  const tbody = qs<HTMLTableSectionElement>('[data-config-diff]');
  const review = qs<HTMLElement>('[data-config-review]');
  if (!tbody || !review) return;
  const diff = data.diff ?? [];
  tbody.replaceChildren(...diff.map((item) => configReviewRow(item.path, item.before, item.after)));
  updateConfigReviewState(review, diff.length);
  review.hidden = false;
  review.tabIndex = -1;
  review.focus({preventScroll: true});
  review.scrollIntoView({block: 'nearest'});
};

const requestConfigValidation = async (
  form: ConfigForm,
  errorSummary: HTMLElement | null,
  isCurrent: () => boolean,
): Promise<{response: Response; data: ConfigValidationResponse} | null> => {
  try {
    const response = await adminFetch('/admin/api/v1/config/validate', {
      method: 'POST',
      body: JSON.stringify({
        values: configValues(form),
        security_acknowledged: formSecurityAcknowledged(form),
      }),
    });
    return {response, data: await response.json() as ConfigValidationResponse};
  } catch (error: unknown) {
    const sessionExpired = error instanceof Error && error.message === 'session_expired';
    if (!sessionExpired && errorSummary && isCurrent()) {
      errorSummary.textContent = errorSummary.dataset.networkError ?? '';
      errorSummary.hidden = false;
      errorSummary.focus();
    }
    return null;
  }
};

const handleConfigValidationResult = (
  form: ConfigForm,
  errorSummary: HTMLElement | null,
  result: {response: Response; data: ConfigValidationResponse} | null,
): ConfigValidationResponse | null => {
  if (!result) return null;
  if (!result.response.ok) {
    showValidationErrors(form, errorSummary, result.data);
    return null;
  }
  showConfigReview(form, result.data);
  return result.data;
};

const stageValidatedConfig = async (
  form: ConfigForm,
  overlay: unknown,
): Promise<boolean> => {
  try {
    const response = await adminFetch('/admin/api/v1/config/staged', {
      method: 'PUT',
      headers: {'If-Match': form.dataset.revision ?? ''},
      body: JSON.stringify({overlay, security_acknowledged: formSecurityAcknowledged(form)}),
    });
    showConfigStageResult(form, response, await response.json() as ConfigStageResponse);
    return response.ok;
  } catch (error: unknown) {
    const sessionExpired = error instanceof Error && error.message === 'session_expired';
    const result = qs<HTMLElement>('[data-config-result]');
    if (!sessionExpired && result) {
      result.textContent = result.dataset.networkError ?? '';
      result.className = 'inline-result banner--error';
    }
    return false;
  }
};

interface ReviewState {
  generation: number;
  overlay: unknown;
  signature: string | null;
  busy: boolean;
}

const formSignature = (form: ConfigForm): string => JSON.stringify({
  values: configDraftEntries(form), acknowledged: formSecurityAcknowledged(form),
});

const configFeedback = (form: ConfigForm, state: string): void => {
  const feedback = qs<HTMLElement>('[data-config-feedback]', form);
  if (feedback) feedback.textContent = feedback.dataset[state] ?? '';
};

const resetConfigEdits = (form: ConfigForm): void => {
  qsa<HTMLElement>('.config-field', form).forEach((row) => {
    const control = configControl(row);
    if (control && !control.disabled) control.value = String(JSON.parse(row.dataset.original ?? 'null'));
  });
  form.elements.security_acknowledged.checked = false;
  clearValidationFeedback(form, qs<HTMLElement>('[data-config-errors]', form));
};

const acceptStagedValues = (form: ConfigForm): void => {
  qsa<HTMLElement>('.config-field', form).forEach((row) => {
    const entry = configEntry(row);
    if (!entry) return;
    row.dataset.original = JSON.stringify(entry[1]);
    row.dataset.managed = 'true';
    const provenance = qs<HTMLElement>('.provenance', row);
    if (provenance) {
      provenance.textContent = form.dataset.stagedLabel ?? provenance.textContent;
      provenance.classList.add('provenance--staged');
    }
  });
};

const lockConfigControls = (form: ConfigForm): (() => void) => {
  const controls = qsa<HTMLInputElement | HTMLSelectElement>('input, select', form);
  const enabled = controls.filter((control) => !control.disabled);
  enabled.forEach((control) => { control.disabled = true; });
  return () => enabled.forEach((control) => { control.disabled = false; });
};

export function initConfigForm(): void {
  restoreConfigDraft();
  const form = qs<ConfigForm>('[data-config-form]');
  if (!form) return;
  const state: ReviewState = {generation: 0, overlay: null, signature: null, busy: false};
  const review = qs<HTMLElement>('[data-config-review]');
  const stage = qs<HTMLButtonElement>('[data-config-stage]');
  const submit = qs<HTMLButtonElement>('button[type="submit"]', form);
  const reset = qs<HTMLButtonElement>('[data-config-reset]', form);
  const refresh = (): void => {
    const count = updateConfigChangeCount(form);
    if (submit) submit.disabled = state.busy || count === 0;
    if (reset) reset.disabled = state.busy || count === 0;
    if (stage) stage.disabled = state.busy || state.overlay === null;
  };
  const invalidate = (): void => {
    const hadReview = state.signature !== null || state.busy;
    state.generation += 1;
    state.overlay = null;
    state.signature = null;
    if (review) review.hidden = true;
    configFeedback(form, hadReview ? 'invalidated' : '');
    refresh();
  };
  form.addEventListener('input', invalidate);
  form.addEventListener('change', invalidate);
  reset?.addEventListener('click', () => {
    if (state.busy) return;
    resetConfigEdits(form);
    invalidate();
    configFeedback(form, '');
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!state.busy && changedFields(form)) void validateCurrentConfig(form, state, submit, refresh);
  });
  stage?.addEventListener('click', () => {
    if (state.busy || state.overlay === null) return;
    if (state.signature !== formSignature(form)) { invalidate(); return; }
    void stageCurrentConfig(form, state, stage, submit, refresh);
  });
  refresh();
}

const changedFields = (form: ConfigForm): boolean =>
  qsa<HTMLElement>('.config-field', form).some(fieldChanged);

const validateCurrentConfig = async (
  form: ConfigForm, state: ReviewState, submit: HTMLButtonElement | null, refresh: () => void,
): Promise<void> => {
  const generation = state.generation;
  const signature = formSignature(form);
  const isCurrent = (): boolean => generation === state.generation && signature === formSignature(form);
  const errorSummary = qs<HTMLElement>('[data-config-errors]', form);
  clearValidationFeedback(form, errorSummary);
  state.overlay = null;
  state.signature = null;
  const review = qs<HTMLElement>('[data-config-review]');
  if (review) review.hidden = true;
  state.busy = true;
  refresh();
  setButtonPending(submit, true);
  configFeedback(form, 'validating');
  const result = await requestConfigValidation(form, errorSummary, isCurrent);
  state.busy = false;
  setButtonPending(submit, false);
  if (isCurrent()) {
    const data = handleConfigValidationResult(form, errorSummary, result);
    if (data?.diff?.length) {
      state.overlay = data.overlay ?? null;
      state.signature = signature;
    }
    configFeedback(form, '');
  } else configFeedback(form, 'invalidated');
  refresh();
};

const stageCurrentConfig = async (
  form: ConfigForm, state: ReviewState, stage: HTMLButtonElement,
  submit: HTMLButtonElement | null, refresh: () => void,
): Promise<void> => {
  state.busy = true;
  refresh();
  setButtonPending(stage, true);
  const unlock = lockConfigControls(form);
  configFeedback(form, 'staging');
  const succeeded = await stageValidatedConfig(form, state.overlay);
  unlock();
  state.busy = false;
  setButtonPending(stage, false);
  if (succeeded) {
    acceptStagedValues(form);
    state.overlay = null;
    state.signature = null;
    const result = qs<HTMLElement>('[data-config-result]');
    result?.setAttribute('tabindex', '-1');
    result?.focus();
  }
  if (submit) submit.disabled = false;
  configFeedback(form, '');
  refresh();
};
