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

export function formSecurityAcknowledged(form: ConfigForm): boolean {
  // The server requires this explicit acknowledgement before accepting risky changes.
  return form.elements.security_acknowledged.checked;
}

export function preserveConfigDraft(): void {
  // Save only editable, non-secret values before reauthentication reloads the page.
  const form = qs<ConfigForm>('[data-config-form]');
  if (!form) return;
  const values: ConfigValues = {};
  qsa<HTMLElement>('.config-field', form).forEach((row) => {
    const control = configControl(row);
    const path = row.dataset.path;
    if (control && !control.disabled && path) values[path] = control.value;
  });
  try {
    window.sessionStorage.setItem(
      configDraftKey,
      JSON.stringify({
        values,
        securityAcknowledged: formSecurityAcknowledged(form),
      } satisfies ConfigDraft),
    );
  } catch {
    // Reauthentication still succeeds when browser storage is unavailable.
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
      if (control && !control.disabled && path && Object.hasOwn(draft.values, path)) {
        control.value = String(draft.values[path]);
      }
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
  const values: ConfigValues = {};
  for (const row of qsa<HTMLElement>('.config-field', form)) {
    const entry = configEntry(row);
    if (entry) values[entry[0]] = entry[1];
  }
  return values;
};

function showConfigStageResult(
  form: ConfigForm,
  response: Response,
  data: ConfigStageResponse,
): void {
  // Keep UI feedback tied to the revision returned by the optimistic-concurrency endpoint.
  const result = qs<HTMLElement>('[data-config-result]');
  if (!result) return;
  result.textContent = response.ok
    ? `${result.dataset.success ?? ''} ${data.revision ?? ''}`.trim()
    : data.message ?? '';
  result.className = `inline-result ${response.ok ? 'banner--success' : 'banner--error'}`;
  if (response.ok && data.revision) form.dataset.revision = data.revision;
}

export function initConfigForm(): void {
  const configForm = qs<ConfigForm>('[data-config-form]');
  let validatedOverlay: unknown = null;
  restoreConfigDraft();

  const updateConfigChangeCount = (): void => {
    if (!configForm) return;
    const count = qsa<HTMLElement>('.config-field', configForm).filter((row) => {
      const control = configControl(row);
      if (!control || control.disabled) return false;
      const value = parsedConfigValue(row.dataset.kind, control.value);
      const original = JSON.parse(row.dataset.original ?? 'null') as ConfigValue;
      return JSON.stringify(value) !== JSON.stringify(original);
    }).length;
    const output = qs<HTMLElement>('[data-change-count]', configForm);
    if (!output) return;
    if (count === 0) output.textContent = output.dataset.zero ?? '';
    else if (count === 1) output.textContent = output.dataset.one ?? '';
    else output.textContent = (output.dataset.many ?? '').replace('{count}', String(count));
  };

  const invalidateConfigReview = (): void => {
    validatedOverlay = null;
    const review = qs<HTMLElement>('[data-config-review]');
    if (review) review.hidden = true;
    updateConfigChangeCount();
  };

  configForm?.addEventListener('input', invalidateConfigReview);
  configForm?.addEventListener('change', invalidateConfigReview);
  updateConfigChangeCount();

  configForm?.addEventListener('submit', async (event) => {
    // Validation returns a reviewable diff; it intentionally does not persist configuration.
    event.preventDefault();
    const submit = event.submitter instanceof HTMLButtonElement ? event.submitter : null;
    const errorSummary = qs<HTMLElement>('[data-config-errors]', configForm);
    qsa<HTMLElement>('.config-field', configForm).forEach((row) => {
      const error = qs<HTMLElement>('[data-field-error]', row);
      const control = configControl(row);
      if (error) error.hidden = true;
      control?.removeAttribute('aria-invalid');
    });
    if (errorSummary) errorSummary.hidden = true;
    if (submit) {
      submit.disabled = true;
      submit.setAttribute('aria-busy', 'true');
    }
    let response: Response;
    let data: ConfigValidationResponse;
    try {
      response = await adminFetch('/admin/api/v1/config/validate', {
        method: 'POST',
        body: JSON.stringify({
          values: configValues(configForm),
          security_acknowledged: formSecurityAcknowledged(configForm),
        }),
      });
      data = await response.json() as ConfigValidationResponse;
    } catch (error: unknown) {
      const sessionExpired = error instanceof Error && error.message === 'session_expired';
      if (!sessionExpired && errorSummary) {
        errorSummary.textContent = errorSummary.dataset.networkError ?? '';
        errorSummary.hidden = false;
        errorSummary.focus();
      }
      return;
    } finally {
      if (submit) {
        submit.removeAttribute('aria-busy');
        submit.disabled = false;
      }
    }
    if (!response.ok) {
      (data.errors ?? []).forEach((item) => {
        const row = qsa<HTMLElement>('.config-field', configForm).find(
          (node) => node.dataset.path === item.path,
        );
        const error = row ? qs<HTMLElement>('[data-field-error]', row) : null;
        const control = row ? configControl(row) : null;
        if (error) {
          error.textContent = item.message;
          error.hidden = false;
        }
        control?.setAttribute('aria-invalid', 'true');
      });
      if (errorSummary) {
        errorSummary.textContent =
          data.message ?? data.errors?.map(({message}) => message).join(' ') ?? '';
        errorSummary.hidden = false;
        errorSummary.focus();
      }
      return;
    }
    validatedOverlay = data.overlay ?? null;
    const tbody = qs<HTMLTableSectionElement>('[data-config-diff]');
    const review = qs<HTMLElement>('[data-config-review]');
    if (!tbody || !review) return;
    const diff = data.diff ?? [];
    tbody.replaceChildren(...diff.map((item) => {
      const row = document.createElement('tr');
      [item.path, JSON.stringify(item.before), JSON.stringify(item.after)].forEach((value) => {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.append(cell);
      });
      return row;
    }));
    const status = qs<HTMLElement>('[data-config-review-status]', review);
    const region = qs<HTMLElement>('[data-config-diff-region]', review);
    const stageButton = qs<HTMLButtonElement>('[data-config-stage]', review);
    if (status) status.textContent = diff.length === 0 ? review.dataset.noChanges ?? '' : '';
    if (region) region.hidden = diff.length === 0;
    if (stageButton) stageButton.disabled = diff.length === 0;
    review.hidden = false;
    review.scrollIntoView({block: 'start'});
  });

  async function stageValidatedConfig(event: Event): Promise<void> {
    // Stage only the server-validated overlay; a new edit must be validated again.
    if (!validatedOverlay || !configForm) return;
    const button = event.currentTarget as HTMLButtonElement;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
      const response = await adminFetch('/admin/api/v1/config/staged', {
        method: 'PUT',
        headers: {'If-Match': configForm.dataset.revision ?? ''},
        body: JSON.stringify({
          overlay: validatedOverlay,
          security_acknowledged: formSecurityAcknowledged(configForm),
        }),
      });
      const data = await response.json() as ConfigStageResponse;
      showConfigStageResult(configForm, response, data);
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
  }

  qs<HTMLButtonElement>('[data-config-stage]')?.addEventListener('click', stageValidatedConfig);
}
