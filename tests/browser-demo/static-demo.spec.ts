import AxeBuilder from '@axe-core/playwright';
import {expect, test} from '@playwright/test';

test('click-through stays local and marks command simulations', async ({page}) => {
  const externalRequests: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).origin !== 'http://127.0.0.1:18766') externalRequests.push(request.url());
  });

  await page.goto('/');
  await expect(page.getByRole('heading', {name: 'Overview'})).toBeVisible();
  await page.getByRole('button', {name: /Check storage Simulated/}).click();
  await expect(page.getByText('Simulated. No storage request was sent.')).toBeVisible();

  await page.getByRole('button', {name: 'Jobs', exact: true}).click();
  await page.getByRole('button', {name: 'DEMO-1042'}).click();
  await page.getByText('Reprocess ticket').click();
  await page.getByLabel('I have reviewed the overwrite risk.').check();
  await page.getByRole('button', {name: /Request reprocessing Simulated/}).click();
  await expect(page.getByText('Simulated. Reprocessing was not requested.')).toBeVisible();

  await page.getByRole('button', {name: 'Configuration', exact: true}).click();
  await page.getByLabel('pdf.max_articles').fill('300');
  await page.getByRole('button', {name: 'Review changes'}).click();
  await page.getByLabel('I understand that activation requires an external restart.').check();
  await page.getByRole('button', {name: /Stage revision Simulated/}).click();
  await expect(page.getByText('Simulated. No revision was staged and no restart was requested.')).toBeVisible();

  const blocking = (await new AxeBuilder({page}).analyze()).violations.filter(
    ({impact}) => impact !== null && ['serious', 'critical'].includes(impact)
  );
  expect(blocking).toEqual([]);
  expect(externalRequests).toEqual([]);
});

test('narrow viewport has no document overflow', async ({page}) => {
  await page.goto('/');
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
  await page.setViewportSize({width: 320, height: 640});
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
});
