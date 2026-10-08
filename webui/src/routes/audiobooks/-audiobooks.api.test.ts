import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import {
  followRole,
  followWatchlist,
  grabRelease,
  runWishlistPass,
  searchWishlistBook,
  unfollowAuthor,
} from './-audiobooks.api';

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

describe('grabbing a release', () => {
  it('shows the server reason when the grab is refused', async () => {
    // the modal used to say only "Request failed", whatever went wrong
    server.use(
      http.post('/api/audiobooks/grab', () =>
        HttpResponse.json(
          { success: false, error: 'The torrent client did not accept the release.' },
          { status: 502 },
        ),
      ),
    );

    const result = await grabRelease('B1', {} as Parameters<typeof grabRelease>[1]);

    expect(result.ok).toBe(false);
    expect(result.error).toBe('The torrent client did not accept the release.');
  });
});

describe('audiobook watchlist roles', () => {
  it('follows a series with its ASIN and asks for the backlog', async () => {
    let sent: Record<string, unknown> = {};
    server.use(
      http.post('/api/audiobooks/watchlist', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, followed: true, wishlisted: 23 });
      }),
    );

    const result = await followWatchlist('Mein Lotta-Leben', '', {
      role: 'series',
      seriesAsin: 'B00J8FC074',
      backfill: true,
    });

    expect(result).toEqual({ ok: true, wishlisted: 23 });
    expect(sent).toMatchObject({
      name: 'Mein Lotta-Leben',
      role: 'series',
      series_asin: 'B00J8FC074',
      backfill: true,
    });
  });

  it('follows an author by default, without a backfill', async () => {
    let sent: Record<string, unknown> = {};
    server.use(
      http.post('/api/audiobooks/watchlist', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true });
      }),
    );

    await followWatchlist('Andy Weir');

    expect(sent).toMatchObject({ role: 'author', backfill: false });
  });

  it('names the role when unfollowing, so a series never removes an author', async () => {
    const seen: string[] = [];
    server.use(
      http.delete('/api/audiobooks/watchlist/:name', ({ request }) => {
        seen.push(new URL(request.url).searchParams.get('role') ?? '');
        return HttpResponse.json({ success: true });
      }),
    );

    await unfollowAuthor('Lotta', 'series');
    await unfollowAuthor('Lotta');

    expect(seen).toEqual(['series', 'author']);
  });

  it('treats an unknown or missing role as an author', () => {
    expect(followRole({ role: 'series' })).toBe('series');
    expect(followRole({ role: 'narrator' })).toBe('narrator');
    expect(followRole({ role: 'publisher' })).toBe('author');
    expect(followRole({})).toBe('author');
  });
});
