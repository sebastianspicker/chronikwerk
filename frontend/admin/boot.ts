/** Page-level wiring shared across admin views. */

import { qs, qsa } from './dom';

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
