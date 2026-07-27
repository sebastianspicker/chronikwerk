/** Overview admission refresh and storage writability probe. */

import { qs } from './dom';
import { adminFetch } from './http';
import type { OverviewElements, StatusResponse, StorageCheckResponse } from './types';

const overviewElements = (): OverviewElements | null => {
  // Avoid scheduling DOM updates on pages that do not render the overview controls.
  const status = qs<HTMLElement>('[data-refresh-status]');
  const running = qs<HTMLElement>('[data-admission-running]');
  const pending = qs<HTMLElement>('[data-admission-pending]');
  const refreshed = qs<HTMLTimeElement>('[data-last-refresh]');
  if (!status || !running || !pending || !refreshed) return null;
  return {status, running, pending, refreshed};
};

const setCapacityBar = (selector: string, current: number, max: number | undefined): void => {
  // Optional: only updates when the bar markup is present and limits are known.
  if (max === undefined || max <= 0) return;
  const root = qs<HTMLElement>(selector);
  if (!root) return;
  const fill = qs<HTMLElement>('i', root) ?? root;
  const pct = Math.min(100, Math.max(0, (current / max) * 100));
  fill.style.width = `${pct}%`;
};

const showOverviewStatus = (elements: OverviewElements, data: StatusResponse): void => {
  // Render the server response together so the displayed timestamp matches its counters.
  const running = Number(data.admission.running);
  const pending = Number(data.admission.pending);
  elements.running.textContent = String(data.admission.running);
  elements.pending.textContent = String(data.admission.pending);
  setCapacityBar('[data-capacity-running-bar]', running, data.admission.max_running);
  setCapacityBar('[data-capacity-pending-bar]', pending, data.admission.max_pending);
  const now = new Date();
  elements.refreshed.dateTime = now.toISOString();
  elements.refreshed.textContent = `${new Intl.DateTimeFormat(document.documentElement.lang, {
    dateStyle: 'short',
    timeStyle: 'medium',
    timeZone: 'UTC',
  }).format(now)} UTC`;
  elements.status.textContent = '';
};

const refreshOverview = async (): Promise<void> => {
  // Background refresh pauses in hidden tabs to avoid needless session and network traffic.
  if (document.visibilityState !== 'visible') return;
  const elements = overviewElements();
  if (!elements) return;
  try {
    const response = await adminFetch('/admin/api/v1/status');
    if (!response.ok) throw new Error('status_refresh_failed');
    showOverviewStatus(elements, await response.json() as StatusResponse);
  } catch (error: unknown) {
    const sessionExpired = error instanceof Error && error.message === 'session_expired';
    if (!sessionExpired) elements.status.textContent = elements.status.dataset.error ?? '';
  }
};

export function initOverview(): void {
  if (!qs('[data-overview]')) return;
  window.setInterval(refreshOverview, 30_000);
}

export function initStorageCheck(): void {
  qs<HTMLButtonElement>('[data-storage-check]')?.addEventListener('click', async (event) => {
    // Disable the control during the probe to prevent concurrent state checks from racing the UI.
    const button = event.currentTarget as HTMLButtonElement;
    const result = qs<HTMLElement>('[data-storage-result]');
    const state = qs<HTMLElement>('[data-storage-state]');
    const checkedAt = qs<HTMLElement>('[data-storage-time]');
    if (!result) return;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
      const response = await adminFetch('/admin/api/v1/status/storage-check', {method: 'POST'});
      if (!response.ok) throw new Error('storage_check_failed');
      const data = await response.json() as StorageCheckResponse;
      const writable = Boolean(data.storage?.writable);
      result.textContent = writable ? result.dataset.success ?? '' : result.dataset.error ?? '';
      if (state) {
        state.textContent = writable ? state.dataset.success ?? '' : state.dataset.error ?? '';
        state.className = `state-value ${writable ? 'state-value--success' : 'state-value--error'}`;
      }
      if (checkedAt) {
        checkedAt.hidden = false;
        checkedAt.textContent = new Intl.DateTimeFormat(document.documentElement.lang, {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          timeZone: 'UTC',
          timeZoneName: 'short',
        }).format(new Date());
      }
    } catch (error: unknown) {
      const sessionExpired = error instanceof Error && error.message === 'session_expired';
      if (!sessionExpired) {
        result.textContent = result.dataset.error ?? '';
        if (state) {
          state.textContent = state.dataset.error ?? '';
          state.className = 'state-value state-value--error';
        }
      }
    } finally {
      button.removeAttribute('aria-busy');
      button.disabled = false;
    }
  });
}
