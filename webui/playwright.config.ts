import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  use: {
    launchOptions: {
      executablePath: '/usr/bin/chromium',
    },
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:8008',
    trace: 'on-first-retry',
  },
});
