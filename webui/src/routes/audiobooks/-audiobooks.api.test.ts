import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { runWishlistPass, searchWishlistBook } from './-audiobooks.api';

// A real per-book search takes 20-50s (Soulseek polls up to 45s per query).
// ky's 10s default used to abort it while the server carried on and finished.
const SLOW_SEARCH_MS = 15_000;

function slowly(body: Record<string, unknown>) {
  return async () => {
    await new Promise((resolve) => setTimeout(resolve, SLOW_SEARCH_MS));
    return HttpResponse.json(body);
  };
}

describe('audiobook wishlist searches', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('waits for a per-book search that outlasts the default timeout', async () => {
    vi.useFakeTimers();
    server.use(
      http.post(
        '/api/audiobooks/wishlist/B1/search',
        slowly({ success: true, outcome: { status: 'grabbed' } }),
      ),
    );

    const pending = searchWishlistBook('B1');
    await vi.advanceTimersByTimeAsync(SLOW_SEARCH_MS);

    await expect(pending).resolves.toEqual({
      success: true,
      outcome: { status: 'grabbed' },
      error: undefined,
    });
  });

  it('waits for a whole wishlist pass that outlasts the default timeout', async () => {
    vi.useFakeTimers();
    server.use(
      http.post(
        '/api/audiobooks/wishlist/search',
        slowly({ success: true, summary: { grabbed: 2 } }),
      ),
    );

    const pending = runWishlistPass();
    await vi.advanceTimersByTimeAsync(SLOW_SEARCH_MS);

    await expect(pending).resolves.toEqual({ grabbed: 2 });
  });
});
