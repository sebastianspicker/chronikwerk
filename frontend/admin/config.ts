/** Config form: draft preserve/restore, change count, validate, and stage. */

import { qs, qsa } from './dom';
import { adminFetch } from './http';
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

export function preserveConfigDraft(): void {
  // Save only editable, non-secret values before reauthentication reloads the page.
  const form = qs<ConfigForm>('[data-config-form]');
  if (!form) return;
  const entries: Array<[string, ConfigValue]> = [];
  qsa<HTMLElement>('.config-field', form).forEach((row) => {
    const control = configControl(row);
    const path = row.dataset.path;
    if (control && !control.disabled && path) entries.push([path, control.value]);
  });
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

const changedConfigFieldCount = (form: ConfigForm): number =>
  qsa<HTMLElement>('.config-field', form).filter((row) => {
    const control = configControl(row);
    if (!control || control.disabled) return false;
    const value = parsedConfigValue(row.dataset.kind, control.value);
    const original = JSON.parse(row.dataset.original ?? 'null') as ConfigValue;
    return JSON.stringify(value) !== JSON.stringify(original);
  }).length;

const updateConfigChangeCount = (form: ConfigForm): void => {
  const output = qs<HTMLElement>('[data-change-count]', form);
  if (!output) return;
  const count = changedConfigFieldCount(form);
  if (count === 0) output.textContent = output.dataset.zero ?? '';
  else if (count === 1) output.textContent = output.dataset.one ?? '';
  else output.textContent = (output.dataset.many ?? '').replace('{count}', String(count));
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
  if (response.ok && data.revision) form.dataset.revision = data.revision;
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
  [path, JSON.stringify(before), JSON.stringify(after)].forEach((value) => {
    const cell = document.createElement('td');
    cell.textContent = value;
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
  review.scrollIntoView({block: 'start'});
};

const requestConfigValidation = async (
  form: ConfigForm,
  errorSummary: HTMLElement | null,
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
    if (!sessionExpired && errorSummary) {
      errorSummary.textContent = errorSummary.dataset.networkError ?? '';
      errorSummary.hidden = false;
      errorSummary.focus();
    }
    return null;
  }
};

const stageValidatedConfig = async (
  form: ConfigForm,
  overlay: unknown,
  button: HTMLButtonElement,
): Promise<void> => {
  // Stage only the server-validated overlay; a new edit must be validated again.
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    const response = await adminFetch('/admin/api/v1/config/staged', {
      method: 'PUT',
      headers: {'If-Match': form.dataset.revision ?? ''},
      body: JSON.stringify({overlay, security_acknowledged: formSecurityAcknowledged(form)}),
    });
    showConfigStageResult(form, response, await response.json() as ConfigStageResponse);
  } catch (error: unknown) {
    const sessionExpired = error instanceof Error && error.message === 'session_expired';
    const result = qs<HTMLElement>('[data-config-result]');
    if (!sessionExpired && result) {
      result.textContent = result.dataset.networkError ?? '';
      result.className = 'inline-result banner--error';
    }
  } finally {
    button.removeAttribute('aria-busy');
    button.disabled = false;
  }
};

export function initConfigForm(): void {
  restoreConfigDraft();
  const form = qs<ConfigForm>('[data-config-form]');
  if (!form) return;
  let validatedOverlay: unknown = null;
  const invalidateConfigReview = (): void => {
    validatedOverlay = null;
    const review = qs<HTMLElement>('[data-config-review]');
    if (review) review.hidden = true;
    updateConfigChangeCount(form);
  };
  const validateConfigForm = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault();
    const submit = event.submitter instanceof HTMLButtonElement ? event.submitter : null;
    const errorSummary = qs<HTMLElement>('[data-config-errors]', form);
    clearValidationFeedback(form, errorSummary);
    if (submit) {
      submit.disabled = true;
      submit.setAttribute('aria-busy', 'true');
    }
    const result = await requestConfigValidation(form, errorSummary);
    if (submit) {
      submit.removeAttribute('aria-busy');
      submit.disabled = false;
    }
    if (!result) return;
    if (!result.response.ok) {
      showValidationErrors(form, errorSummary, result.data);
      return;
    }
    validatedOverlay = result.data.overlay ?? null;
    showConfigReview(form, result.data);
  };

  form.addEventListener('input', invalidateConfigReview);
  form.addEventListener('change', invalidateConfigReview);
  form.addEventListener('submit', (event) => {
    void validateConfigForm(event);
  });
  qs<HTMLButtonElement>('[data-config-stage]')?.addEventListener('click', (event) => {
    const button = event.currentTarget;
    if (button instanceof HTMLButtonElement && validatedOverlay) {
      void stageValidatedConfig(form, validatedOverlay, button);
    }
  });
  updateConfigChangeCount(form);
}
