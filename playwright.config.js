import {defineConfig, devices} from '@playwright/test';
import {mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const archiveRoot = mkdtempSync(join(tmpdir(), 'zpa-browser-archive-'));
const adminRoot = mkdtempSync(join(tmpdir(), 'zpa-browser-admin-'));
const python = process.env.PYTHON || 'python';

export default defineConfig({
  testDir: './test/browser',
  timeout: 30_000,
  expect: {timeout: 5_000},
  fullyParallel: false,
  retries: 0,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:18765',
    trace: 'retain-on-failure'
  },
  projects: [
    {name: 'chromium', use: {...devices['Desktop Chrome']}},
    {name: 'firefox', use: {...devices['Desktop Firefox']}},
    {name: 'webkit', use: {...devices['Desktop Safari']}},
    {name: 'narrow', use: {...devices['iPhone 13'], viewport: {width: 390, height: 844}}}
  ],
  webServer: {
    command: `${python} -m uvicorn zammad_pdf_archiver.asgi:app --host 127.0.0.1 --port 18765`,
    url: 'http://127.0.0.1:18765/healthz',
    reuseExistingServer: false,
    timeout: 30_000,
    env: {
      PYTHONPATH: 'src',
      ZAMMAD__BASE_URL: 'https://zammad.example.invalid',
      ZAMMAD__API_TOKEN: 'browser-test-token',
      ZAMMAD__WEBHOOK_HMAC_SECRET: 'browser-test-webhook-secret',
      STORAGE__ROOT: archiveRoot,
      HARDENING__TRANSPORT__ALLOW_PRIVATE_NETWORKS: 'true',
      ADMIN__ENABLED: 'true',
      ADMIN__ACCESS_TOKEN: 'browser-test-admin-token-at-least-32-characters',
      ADMIN__STATE_DIR: adminRoot,
      ADMIN__COOKIE_SECURE: 'false'
    }
  }
});
