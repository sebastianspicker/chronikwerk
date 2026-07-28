/** Reauthentication dialog: establish a new session and preserve config drafts. */

import { qs } from './dom';
import { preserveConfigDraft } from './config';
import type { ConfigForm } from './types';

const showReauthError = (error: HTMLElement | null, message: string): void => {
  if (!error) return;
  error.textContent = message;
  error.hidden = false;
};

const submitReauthForm = async (form: ConfigForm, event: SubmitEvent): Promise<void> => {
  // Persist the safe draft only after a successful replacement session is established.
  event.preventDefault();
  const error = qs<HTMLElement>('[data-reauth-error]', form);
  const submit = qs<HTMLButtonElement>('button[type="submit"]', form);
  if (error) error.hidden = true;
  if (submit) submit.disabled = true;
  form.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch('/admin/api/v1/session', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({access_token: new FormData(form).get('access_token')}),
    });
    if (!response.ok) {
      showReauthError(error, error?.dataset.invalid ?? '');
      return;
    }
    preserveConfigDraft();
    window.location.reload();
  } catch {
    showReauthError(error, error?.dataset.networkError ?? '');
  } finally {
    form.removeAttribute('aria-busy');
    if (submit) submit.disabled = false;
  }
};

export function initReauthForm(): void {
  const form = qs<ConfigForm>('[data-reauth-form]');
  form?.addEventListener('submit', (event) => {
    void submitReauthForm(form, event);
  });
}
