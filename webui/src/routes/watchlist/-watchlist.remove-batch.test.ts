import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { REMOVE_BATCH_TIMEOUT_MS, removeWatchlistArtistsBatch } from './-watchlist.api';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

/** remove-batch never answers in time; the watchlist reports what is left. */
function stubSlowServer(leftOnList: string[]) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = urlOf(input);
      calls.push(url);
      if (url.includes('/api/watchlist/remove-batch')) {
        const signal = (input instanceof Request ? input.signal : undefined) ?? init?.signal;
        return new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener('abort', () => reject(signal.reason));
        });
      }
      if (url.includes('/api/watchlist/artists')) {
        return Promise.resolve(
          json({
            success: true,
            artists: leftOnList.map((id, index) => ({
              id: index + 1,
              artist_name: `Artist ${id}`,
              spotify_artist_id: id,
            })),
          }),
        );
      }
      return Promise.resolve(json({ success: true }));
    }),
  );
  return calls;
}

describe('removeWatchlistArtistsBatch (#2200)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('waits longer than the 10s default before giving up', async () => {
    stubSlowServer([]);
    let settled = false;
    const pending = removeWatchlistArtistsBatch(['sp-1']).finally(() => {
      settled = true;
    });

    await vi.advanceTimersByTimeAsync(30_000);
    expect(settled).toBe(false);

    await vi.advanceTimersByTimeAsync(REMOVE_BATCH_TIMEOUT_MS);
    await pending;
    expect(settled).toBe(true);
  });

  it('a timeout is not an error: it reports what the watchlist holds now', async () => {
    const calls = stubSlowServer(['sp-3', 'sp-keep']);

    const pending = removeWatchlistArtistsBatch(['sp-1', 'sp-2', 'sp-3']);
    await vi.advanceTimersByTimeAsync(REMOVE_BATCH_TIMEOUT_MS + 1);

    await expect(pending).resolves.toEqual({ removed: 2, pending: 1, timedOut: true });
    expect(calls.some((url) => url.includes('/api/watchlist/artists'))).toBe(true);
  });

  it('an answer in time passes the server count through', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(json({ success: true, removed: 2 }))),
    );

    await expect(removeWatchlistArtistsBatch(['sp-1', 'sp-2'])).resolves.toEqual({
      removed: 2,
      pending: 0,
      timedOut: false,
    });
  });

  it('a real failure still throws', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(json({ success: false, error: 'nope' }, 500))),
    );

    await expect(removeWatchlistArtistsBatch(['sp-1'])).rejects.toThrow('nope');
  });
});
