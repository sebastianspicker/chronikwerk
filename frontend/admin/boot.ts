/** Page-level wiring shared across admin views. */

import { qs, qsa } from './dom';
import { setButtonPending } from './pending';

export function initAutoSubmit(): void {
  qsa<HTMLElement>('[data-auto-submit]').forEach((control) => {
    // Native change events preserve keyboard and assistive-technology submission paths.
    control.addEventListener('change', () => {
      if (control instanceof HTMLInputElement || control instanceof HTMLSelectElement) {
        control.form?.requestSubmit();
      }
    });
  });
}

export function initDialogClose(): void {
  qs<HTMLElement>('[data-dialog-close]')?.addEventListener('click', () => {
    qs<HTMLDialogElement>('#reauth-dialog')?.close();
  });
}

export function initPendingForms(): void {
  qsa<HTMLFormElement>('[data-pending-form]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (form.dataset.submitting === 'true') { event.preventDefault(); return; }
      if (event.defaultPrevented) return;
      form.dataset.submitting = 'true';
      form.setAttribute('aria-busy', 'true');
      setButtonPending(qs<HTMLButtonElement>('button[type="submit"]', form), true);
    });
  });
  window.addEventListener('pageshow', () => {
    qsa<HTMLFormElement>('[data-pending-form]').forEach((form) => {
      delete form.dataset.submitting;
      form.removeAttribute('aria-busy');
      setButtonPending(qs<HTMLButtonElement>('button[type="submit"]', form), false);
    });
  });
}
