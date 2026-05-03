import { tanstackRouter } from '@tanstack/router-plugin/vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { defineConfig } from 'vite';

export default defineConfig({
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
});
