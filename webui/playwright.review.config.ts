import { defineConfig } from '@playwright/test';

/** Behavioral review journeys use the same built app and API harness as visuals. */
export default defineConfig({
  testDir: './tests/review',
  timeout: 30_000,
  fullyParallel: true,
  workers: 2,
  use: {
    browserName: 'chromium',
    serviceWorkers: 'block',
    contextOptions: { reducedMotion: 'reduce' },
    locale: 'en-US',
    timezoneId: 'UTC',
    colorScheme: 'dark',
    trace: 'retain-on-failure',
  },
});
