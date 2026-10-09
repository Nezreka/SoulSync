import { tanstackRouter } from '@tanstack/router-plugin/vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { defineConfig } from 'vite';

export default defineConfig(({ command }) => ({
  plugins: [
    tanstackRouter({
      target: 'react',
    }),
    react(),
  ],
  base: command === 'serve' ? '/static/dist/' : './',
  server: {
    // the page is served by flask on :8008 and pulls react from here on :5173,
    // so every module load is cross-origin. vite only allows localhost origins
    // by default, which blanks every react page on a phone under `dev.py --lan`.
    // allow private lan addresses too, nothing public.
    cors: {
      origin:
        /^https?:\/\/(?:(?:[^:]+\.)?localhost|127\.0\.0\.1|\[::1\]|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)(?::\d+)?$/,
    },
  },
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
}));
