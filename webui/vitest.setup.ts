// Pin the suite to a NON-UTC zone.
//
// The app reads naive SQLite timestamps ("2026-07-27 02:10:37") and must treat
// them as UTC; `new Date(naive)` reads them as local, which skews every
// relative label by the viewer's offset. Under TZ=UTC — what CI runs — local
// and UTC coincide, so that bug is invisible and no test can catch it. Running
// tests at an offset reproduces the condition real users are in.
process.env.TZ = 'America/Los_Angeles';

import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll, beforeEach, vi } from 'vitest';

import { HttpResponse, http, server } from './src/test/msw';

// Node 26 exposes a global `localStorage` slot whose value may be undefined.
// Use jsdom's implementation where available and a spec-shaped in-memory
// fallback otherwise. Keep one stable instance across tests in the worker.
function createMemoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(String(key)) ?? null,
    key: (index) => Array.from(values.keys())[index] ?? null,
    removeItem: (key) => {
      values.delete(String(key));
    },
    setItem: (key, value) => {
      values.set(String(key), String(value));
    },
  };
}

const testLocalStorage = window.localStorage ?? createMemoryStorage();

function restoreLocalStorage() {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: testLocalStorage,
  });
}

restoreLocalStorage();

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });
});

beforeEach(() => {
  restoreLocalStorage();
  server.use(
    http.get('/status', () =>
      HttpResponse.json({ media_server: { type: 'plex', connected: true } }),
    ),
    // Shell chrome, not page data: the nav's Issues badge fetches this on every
    // page render, so without a default handler EVERY route test logs an
    // unhandled-request error and vitest warns that unhandled errors "might
    // cause false positive tests". A test that cares about the badge overrides
    // this with its own handler.
    http.get('/api/issues/counts', () => HttpResponse.json({ success: true, counts: {} })),
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
