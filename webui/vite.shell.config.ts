import path from 'node:path';
import { defineConfig } from 'vite';

/**
 * The SHELL bundle: classic scripts from webui/static ported to typescript.
 *
 * Built separately from the react app because it must be an IIFE loaded as a
 * synchronous classic <script> - the remaining vanilla scripts and inline
 * onclick handlers consume its window globals, and a deferred `type="module"`
 * bundle would run after them. Fixed filename (no hash): index.html loads it
 * through url_for with v=static_v, the same cache-busting every static file
 * uses.
 *
 * Runs AFTER the react build in `npm run build` - the react build empties
 * static/dist, so order matters.
 */
export default defineConfig({
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
    emptyOutDir: false,
    lib: {
      entry: path.resolve(import.meta.dirname, 'src/shell/index.ts'),
      name: 'SoulSyncShell',
      formats: ['iife'],
      fileName: () => 'shell.js',
    },
  },
});
