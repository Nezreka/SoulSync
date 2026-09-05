import { defineConfig } from '@playwright/test';

/**
 * Layout probes that need no server.
 *
 * The main config points at a local SoulSync and a Linux chromium; these specs
 * lay real markup out under the real stylesheets and MEASURE it, so they run
 * anywhere a browser does. jsdom cannot do this: every bug they cover is a
 * rectangle sitting on another rectangle.
 *
 * globalSetup regenerates the React markup first, so a probe can never pass
 * against a component that has since changed.
 */
export default defineConfig({
  testDir: './tests/layout',
  globalSetup: './tests/layout/generate-fixtures.ts',
  timeout: 30_000,
  use: { trace: 'off' },
});
