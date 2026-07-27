/** Reauthentication dialog: establish a new session and preserve config drafts. */

import { qs } from './dom';
import { preserveConfigDraft } from './config';
import type { ConfigForm } from './types';

export function initReauthForm(): void {
  qs<ConfigForm>('[data-reauth-form]')?.addEventListener('submit', async (event) => {
    // Persist the safe draft only after a successful replacement session is established.
    event.preventDefault();
    const form = event.currentTarget as ConfigForm;
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
        if (error) {
          error.textContent = error.dataset.invalid ?? '';
          error.hidden = false;
        }
        return;
      }
      preserveConfigDraft();
      window.location.reload();
    } catch {
      if (error) {
        error.textContent = error.dataset.networkError ?? '';
        error.hidden = false;
      }
    } finally {
      form.removeAttribute('aria-busy');
      if (submit) submit.disabled = false;
    }
  });
}
