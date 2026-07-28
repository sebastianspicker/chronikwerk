/** CSRF headers and session-expiry handling for admin API calls. */

import { qs } from './dom';

export const csrfToken = (): string =>
  qs<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? '';

const requiresCsrfToken = (method: string | undefined): boolean =>
  !['GET', 'HEAD'].includes((method ?? 'GET').toUpperCase());

export async function adminFetch(
  url: RequestInfo | URL,
  options: RequestInit = {},
): Promise<Response> {
  // Centralize CSRF and session expiry so individual controls cannot forget either boundary.
  const headers = new Headers(options.headers ?? {});
  if (requiresCsrfToken(options.method)) {
    headers.set('X-CSRF-Token', csrfToken());
  }
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(url, {...options, headers});
  if (response.status === 401) {
    const dialog = qs<HTMLDialogElement>('#reauth-dialog');
    if (dialog && !dialog.open) dialog.showModal();
    throw new Error('session_expired');
  }
  return response;
}
