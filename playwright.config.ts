// Exercise the real admin server across desktop, mobile, and accessibility-relevant engines.
import {defineConfig, devices} from '@playwright/test';
import {mkdtempSync, realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const tempRoot = realpathSync(tmpdir());
const archiveRoot = mkdtempSync(join(tempRoot, 'chronikwerk-browser-archive-'));
const adminRoot = mkdtempSync(join(tempRoot, 'chronikwerk-browser-admin-'));
const python = process.env.PYTHON ?? 'python';

export default defineConfig({
  testDir: './tests/browser',
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
    command: `${python} -m uvicorn chronikwerk.asgi:app --host 127.0.0.1 --port 18765`,
    url: 'http://127.0.0.1:18765/healthz',
    reuseExistingServer: false,
    timeout: 30_000,
    env: {
      PYTHONPATH: 'src',
      ZAMMAD__BASE_URL: 'https://zammad.example.invalid',
      ZAMMAD__API_TOKEN: 'browser-test-token',
      ZAMMAD__WEBHOOK_HMAC_SECRET: 'browser-test-webhook-secret-at-least-32-characters',
      STORAGE__ROOT: archiveRoot,
      HARDENING__TRANSPORT__ALLOW_PRIVATE_NETWORKS: 'true',
      ADMIN__ENABLED: 'true',
      ADMIN__ACCESS_TOKEN: 'browser-test-admin-token-at-least-32-characters',
      ADMIN__STATE_DIR: adminRoot,
      ADMIN__COOKIE_SECURE: 'false'
    }
  }
});
