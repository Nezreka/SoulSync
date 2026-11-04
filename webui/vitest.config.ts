import { mergeConfig, defineConfig } from 'vitest/config';

import viteConfig from './vite.config';

export default mergeConfig(
  viteConfig({ command: 'serve', mode: 'test' }),
  defineConfig({
    test: {
      include: ['src/**/*.test.ts', 'src/**/*.test.tsx', 'src/**/*.spec.ts', 'src/**/*.spec.tsx'],
      exclude: [
        'tests/**',
        // Upstream's artist page runs here only for artists the catalogue does
        // not hold; `/artist-detail/library/<id>` opens Library v2 instead.
        // These two mount the page through the router on exactly that URL, so
        // they test a mode this branch never shows. The page's components keep
        // their own tests, and -route.catalogue.test.tsx covers the route.
        'src/routes/artist-detail/-route.test.tsx',
        'src/routes/artist-detail/-ui/artist-detail-page.test.tsx',
      ],
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./vitest.setup.ts'],
      css: true,
      restoreMocks: true,
      // full-route tests (watchlist, stats, podcasts, import) mount the whole
      // page plus its queries and run past the 5s default on a loaded box or
      // the 4-core ci runner. they pass fine with room, they're just slow.
      testTimeout: 15000,
      // A runaway test (see route-guard.test.ts for the redirect loop that
      // once ground two workers for hours and wedged CI) must die as a visible
      // OOM naming its file, not sit at node's huge default ceiling spinning
      // in GC forever while the pool waits on it. Healthy workers run ~130MB;
      // the ceiling only exists to terminate the pathological case.
      execArgv: ['--max-old-space-size=1024'],
    },
  }),
);
