/** Keep asynchronous and native submission states visible without changing form values. */

export function setButtonPending(button: HTMLButtonElement | null, pending: boolean): void {
  if (!button) return;
  if (pending) {
    button.dataset.idleLabel ??= button.textContent ?? '';
    if (button.dataset.pending) button.textContent = button.dataset.pending;
    button.setAttribute('aria-busy', 'true');
  } else {
    if (button.dataset.idleLabel !== undefined) button.textContent = button.dataset.idleLabel;
    button.removeAttribute('aria-busy');
  }
  button.disabled = pending;
}
