import { tanstackRouter } from '@tanstack/router-plugin/vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { defineConfig } from 'vite-plus';

export default defineConfig({
  fmt: {
    singleQuote: true,
    sortImports: {
      groups: [
        'type-import',
        ['value-builtin', 'value-external'],
        'type-internal',
        'value-internal',
        ['type-parent', 'type-sibling', 'type-index'],
        ['value-parent', 'value-sibling', 'value-index'],
        'unknown',
      ],
    },
    sortPackageJson: true,
    trailingComma: 'all',
  },
  plugins: [
    tanstackRouter({
      target: 'react',
    }),
    react(),
  ],
  base: '/static/dist/',
  root: import.meta.dirname,
  resolve: {
    alias: [
      {
        find: /^@\//,
        replacement: `${path.resolve(import.meta.dirname, 'src')}/`,
      },
    ],
  },
  build: {
    outDir: path.resolve(import.meta.dirname, 'static/dist'),
    emptyOutDir: true,
    manifest: true,
    rolldownOptions: {
      input: [path.resolve(import.meta.dirname, 'src/app/main.tsx')],
    },
  },
  test: {
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx', 'src/**/*.spec.ts', 'src/**/*.spec.tsx'],
    exclude: ['tests/**'],
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    css: true,
    restoreMocks: true,
  },
});
