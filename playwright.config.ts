import { defineConfig } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export default defineConfig({
  testDir: './tests/browser',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: 'list',
  outputDir: join(tmpdir(), 'chronikwerk-playwright-results'),
  use: {
    baseURL: 'http://127.0.0.1:4177', browserName: 'chromium', trace: 'off',
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE },
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile-390', use: { viewport: { width: 390, height: 844 } } },
    { name: 'mobile-320', use: { viewport: { width: 320, height: 720 } } },
  ],
  webServer: {
    command: 'node tests/browser/server.mjs',
    url: 'http://127.0.0.1:4177/admin/configuration',
    reuseExistingServer: false,
  },
});
