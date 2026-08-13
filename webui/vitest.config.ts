import { mergeConfig, defineConfig } from 'vitest/config';

import viteConfig from './vite.config';

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      include: ['src/**/*.test.ts', 'src/**/*.test.tsx', 'src/**/*.spec.ts', 'src/**/*.spec.tsx'],
      exclude: ['tests/**'],
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./vitest.setup.ts'],
      css: true,
      restoreMocks: true,
      // A runaway test (see route-guard.test.ts for the redirect loop that
      // once ground two workers for hours and wedged CI) must die as a visible
      // OOM naming its file, not sit at node's huge default ceiling spinning
      // in GC forever while the pool waits on it. Healthy workers run ~130MB;
      // the ceiling only exists to terminate the pathological case.
      execArgv: ['--max-old-space-size=1024'],
    },
  }),
);
