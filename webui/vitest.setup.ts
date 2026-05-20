import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll, beforeEach, vi } from 'vitest';

import { HttpResponse, http, server } from './src/test/msw';

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });
});

beforeEach(() => {
  server.use(
    http.get('/status', () =>
      HttpResponse.json({ media_server: { type: 'plex', connected: true } }),
    ),
  );
});

afterEach(() => {
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});

Object.defineProperty(window, 'scrollTo', {
  value: vi.fn(),
  writable: true,
});
