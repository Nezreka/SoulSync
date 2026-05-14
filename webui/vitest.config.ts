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
    },
  }),
);
