import AxeBuilder from '@axe-core/playwright';
import {expect, test} from '@playwright/test';

const ACCESS_TOKEN = 'browser-test-admin-token-at-least-32-characters';

async function signIn(page) {
  await page.goto('/admin/login?lang=en-GB');
  await page.locator('#access-token').fill(ACCESS_TOKEN);
  await page.getByRole('button', {name: 'Sign in'}).click();
  await expect(page.getByRole('heading', {name: 'Overview'})).toBeVisible();
}

async function expectNoSeriousAxeFindings(page) {
  const results = await new AxeBuilder({page}).analyze();
  const blocking = results.violations.filter(({impact}) => ['serious', 'critical'].includes(impact));
  expect(blocking).toEqual([]);
}

test('login and shell are keyboard accessible in English and German', async ({page, browserName}) => {
  await page.goto('/admin/login?lang=en-GB');
  await page.keyboard.press(browserName === 'webkit' ? 'Alt+Tab' : 'Tab');
  await expect(page.getByRole('link', {name: 'Skip to main content'})).toBeFocused();
  await expectNoSeriousAxeFindings(page);

  await page.locator('#access-token').fill(ACCESS_TOKEN);
  await page.getByRole('button', {name: 'Sign in'}).click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en-GB');
  await expect(page.getByText('Job history and sessions are process-local and volatile.')).toBeVisible();
  await expectNoSeriousAxeFindings(page);

  await page.getByLabel('Language').selectOption('de-DE');
  await expect(page.locator('html')).toHaveAttribute('lang', 'de-DE');
  await expect(page.getByRole('heading', {name: 'Übersicht'})).toBeVisible();
});

test('jobs, retry disclosure, and configuration remain operable without external assets', async ({page}) => {
  const externalRequests = [];
  page.on('request', (request) => {
    if (new URL(request.url()).origin !== 'http://127.0.0.1:18765') externalRequests.push(request.url());
  });
  await signIn(page);
  await page.getByRole('link', {name: 'Jobs'}).click();
  await expect(page.getByText('No matching job events in volatile history.')).toBeVisible();
  await page.goto('/admin/jobs/12');
  await page.getByText('Reprocess ticket').click();
  await expect(page.getByLabel('I have reviewed the overwrite risk.')).toBeVisible();
  await page.getByRole('link', {name: 'Configuration'}).click();
  await expect(page.getByText('pdf.max_articles')).toBeVisible();
  await expectNoSeriousAxeFindings(page);
  expect(externalRequests).toEqual([]);
});

test('narrow layout and static assets stay within release budgets', async ({page, request}) => {
  await signIn(page);
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.getByRole('navigation', {name: 'Primary'})).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);

  const html = await (await request.get('/admin/login?lang=en-GB')).body();
  const css = await (await request.get('/admin/static/admin.css')).body();
  const js = await (await request.get('/admin/static/admin.js')).body();
  expect(html.byteLength).toBeLessThanOrEqual(100 * 1024);
  expect(css.byteLength + js.byteLength).toBeLessThanOrEqual(150 * 1024);
});

test('inline reauthentication preserves an unsaved non-secret configuration draft', async ({page}) => {
  await signIn(page);
  await page.goto('/admin/configuration');
  const field = page.locator('.config-field[data-path="pdf.max_articles"] input');
  await field.fill('249');
  await page.evaluate(async () => {
    const csrf = document.querySelector('meta[name="csrf-token"]').content;
    const response = await fetch('/admin/api/v1/session', {
      method: 'DELETE',
      headers: {'X-CSRF-Token': csrf}
    });
    if (!response.ok) throw new Error(`session deletion failed: ${response.status}`);
  });
  await page.locator('[data-config-form]').evaluate((form) => form.requestSubmit());
  const dialog = page.locator('#reauth-dialog');
  await expect(dialog).toBeVisible();
  await dialog.locator('#reauth-token').fill(ACCESS_TOKEN);
  await dialog.getByRole('button', {name: 'Sign in'}).click();
  await expect(page).toHaveURL(/\/admin\/configuration$/);
  await expect(field).toHaveValue('249');
});
