import { afterEach, describe, expect, it, vi } from 'vitest';

import { checkWatchlistRequest, toggleWatchlistRequest } from './-artist-detail.watchlist-button';

/**
 * The hero watchlist button's requests. The toggle re-checks the SERVER state
 * first (6963): the current status decides add vs remove, not the button's
 * belief.
 */

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.updateWatchlistCount;
});

function stubEndpoints(isWatching: boolean, toggleResult: unknown) {
  const calls: { url: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      calls.push({ url, body });
      if (url === '/api/watchlist/check') {
        return new Response(JSON.stringify({ success: true, is_watching: isWatching }));
      }
      return new Response(JSON.stringify(toggleResult));
    }),
  );
  return calls;
}

describe('checkWatchlistRequest', () => {
  it('returns the flag, or null when the endpoint fails', async () => {
    stubEndpoints(true, {});
    expect(await checkWatchlistRequest('sp9')).toBe(true);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
        throw new Error('offline');
      }),
    );
    expect(await checkWatchlistRequest('sp9')).toBeNull();
  });
});

describe('toggleWatchlistRequest', () => {
  it('not watching → adds with the artist name, reports watching', async () => {
    const calls = stubEndpoints(false, { success: true, message: 'Added Aphex Twin' });
    const bump = vi.fn();
    window.updateWatchlistCount = bump as never;
    const result = await toggleWatchlistRequest('sp9', 'Aphex Twin');
    expect(result).toEqual({ watching: true, message: 'Added Aphex Twin' });
    expect(calls[1].url).toBe('/api/watchlist/add');
    expect(calls[1].body).toEqual({ artist_id: 'sp9', artist_name: 'Aphex Twin' });
    expect(bump).toHaveBeenCalled();
  });

  it('watching → removes with the id only', async () => {
    const calls = stubEndpoints(true, { success: true, message: 'Removed' });
    const result = await toggleWatchlistRequest('sp9', 'Aphex Twin');
    expect(result).toEqual({ watching: false, message: 'Removed' });
    expect(calls[1].url).toBe('/api/watchlist/remove');
    expect(calls[1].body).toEqual({ artist_id: 'sp9' });
  });

  it('a failed check aborts before any toggle request', async () => {
    const spy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ success: false, error: 'db locked' })),
    );
    vi.stubGlobal('fetch', spy);
    await expect(toggleWatchlistRequest('sp9', 'Aphex Twin')).rejects.toThrow('db locked');
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
