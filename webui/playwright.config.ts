import { defineConfig } from '@playwright/test';

const chromiumPath = process.env.PLAYWRIGHT_CHROMIUM_PATH;

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  use: {
    // CI images may provide a system Chromium, while local development uses
    // Playwright's pinned browser. Never hard-code a path that may not exist.
    launchOptions: chromiumPath ? { executablePath: chromiumPath } : undefined,
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:8008',
    trace: 'on-first-retry',
  },
});
