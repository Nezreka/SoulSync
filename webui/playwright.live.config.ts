import { defineConfig } from '@playwright/test';

/**
 * Batch-1 audit against a RUNNING SoulSync.
 *
 * The repo's playwright.config.ts pins a Linux chromium path; this one takes
 * whatever browser playwright installed, so it runs on either side of WSL.
 * Point it at the server with PLAYWRIGHT_BASE_URL if it is not on 8008.
 */
export default defineConfig({
  testDir: './tests/live',
  timeout: 120_000,
  workers: 1,
  // The dev server restarts on its own (file watcher, worker recycling), and a
  // restart lands as ECONNREFUSED mid-test. Retry rather than call that a
  // finding.
  retries: 2,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:8008',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
