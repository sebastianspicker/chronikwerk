import {defineConfig, devices} from '@playwright/test';

const python = process.env.PYTHON ?? 'python3';

export default defineConfig({
  testDir: './tests/browser-demo',
  timeout: 20_000,
  expect: {timeout: 5_000},
  retries: 0,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:18766',
    trace: 'retain-on-failure'
  },
  projects: [
    {name: 'desktop', use: {...devices['Desktop Chrome']}},
    {name: 'narrow', use: {...devices['iPhone 13'], viewport: {width: 390, height: 844}}}
  ],
  webServer: {
    command: `${python} -m http.server 18766 --bind 127.0.0.1 --directory build/static-demo`,
    url: 'http://127.0.0.1:18766/',
    reuseExistingServer: false,
    timeout: 15_000
  }
});
