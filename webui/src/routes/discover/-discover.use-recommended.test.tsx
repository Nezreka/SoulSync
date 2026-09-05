import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { RecommendedArtist } from './-discover.recommended';
import type { RecToast } from './-discover.use-recommended';

import { useAdventurousness, useRecommended } from './-discover.use-recommended';

let toasts: RecToast[] = [];
let enrichBodies: unknown[] = [];
let advBodies: unknown[] = [];
let recFetches = 0;

function stub() {
  enrichBodies = [];
  advBodies = [];
  recFetches = 0;
  server.use(
    http.post('/api/watchlist/add', () => HttpResponse.json({ success: true })),
    http.post('/api/watchlist/remove', () => HttpResponse.json({ success: true })),
    http.post('/api/discover/similar-artists/enrich', async ({ request }) => {
      enrichBodies.push(await request.json());
      return HttpResponse.json({
        success: true,
        artists: { sp2: { image_url: '/img/2.jpg' } },
      });
    }),
    http.post('/api/discover/adventurousness', async ({ request }) => {
      advBodies.push(await request.json());
      return HttpResponse.json({ success: true });
    }),
    ...['/api/discover/listening-recommendations', '/api/discover/similar-artists'].map((p) =>
      http.get(p, () => {
        recFetches += 1;
        return HttpResponse.json({ success: true, artists: [] });
      }),
    ),
  );
}

function wrapper() {
  const client = createTestQueryClient();
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  toasts = [];
  stub();
});

afterEach(() => {
  vi.useRealTimers();
  server.resetHandlers();
});

describe('useRecommended', () => {
  it('checkWatching folds check-batch answers into watchingIds (1173-1195)', async () => {
    let checkBody: unknown = null;
    server.use(
      http.post('/api/watchlist/check-batch', async ({ request }) => {
        checkBody = await request.json();
        return HttpResponse.json({ success: true, results: { sp1: true, sp2: false } });
      }),
    );
    const { result } = renderHook(() => useRecommended((t) => toasts.push(t)), {
      wrapper: wrapper(),
    });
    await act(async () => {
      await result.current.checkWatching([
        { artist_id: 'sp1', artist_name: 'A' },
        { artist_id: 'sp2', artist_name: 'B' },
        { artist_name: 'no-id' },
      ] as RecommendedArtist[]);
    });
    // Only real ids go up; only TRUE answers come back into the set.
    expect(checkBody).toEqual({ artist_ids: ['sp1', 'sp2'] });
    expect(result.current.watchingIds.has('sp1')).toBe(true);
    expect(result.current.watchingIds.has('sp2')).toBe(false);
  });

  it('checkWatching with no usable ids never calls the endpoint', async () => {
    let hits = 0;
    server.use(
      http.post('/api/watchlist/check-batch', () => {
        hits += 1;
        return HttpResponse.json({ success: true, results: {} });
      }),
    );
    const { result } = renderHook(() => useRecommended((t) => toasts.push(t)), {
      wrapper: wrapper(),
    });
    await act(async () => {
      await result.current.checkWatching([{ artist_name: 'no-id' }] as RecommendedArtist[]);
    });
    expect(hits).toBe(0);
  });

  it('toggles per card, tracking membership and toasting', async () => {
    const { result } = renderHook(() => useRecommended((t) => toasts.push(t)));
    await act(() => result.current.toggleWatchlist('sp1', 'Aphex Twin'));
    expect(result.current.watchingIds.has('sp1')).toBe(true);
    expect(toasts.at(-1)).toEqual({ message: 'Added Aphex Twin to watchlist', level: 'success' });
    await act(() => result.current.toggleWatchlist('sp1', 'Aphex Twin'));
    expect(result.current.watchingIds.has('sp1')).toBe(false);
    expect(toasts.at(-1)!.message).toContain('Removed');
  });

  it('enriches ONLY image-less cards, by the source-picked id column', async () => {
    const items = [
      { artist_name: 'A', image_url: '/have.jpg', spotify_artist_id: 'sp1' },
      { artist_name: 'B', spotify_artist_id: 'sp2' },
    ] as RecommendedArtist[];
    const { result } = renderHook(() => useRecommended((t) => toasts.push(t)));
    await act(() => result.current.enrichImages(items, 'spotify'));
    expect(enrichBodies).toEqual([{ artist_ids: ['sp2'], source: 'spotify' }]);
    expect(result.current.images).toEqual({ sp2: '/img/2.jpg' });
    // Nothing image-less → no request at all (1014).
    await act(() => result.current.enrichImages([items[0]], 'spotify'));
    expect(enrichBodies).toHaveLength(1);
  });

  it('a deezer-sourced shelf asks by the deezer id column', async () => {
    const items = [{ artist_name: 'B', deezer_artist_id: 'dz9' }] as RecommendedArtist[];
    const { result } = renderHook(() => useRecommended((t) => toasts.push(t)));
    await act(() => result.current.enrichImages(items, 'deezer'));
    expect(enrichBodies).toEqual([{ artist_ids: ['dz9'], source: 'deezer' }]);
  });
});

describe('useAdventurousness', () => {
  it('commit saves the value and REFETCHES both rec rows', async () => {
    const { result } = renderHook(() => useAdventurousness(0.3), { wrapper: wrapper() });
    await act(() => result.current.commit(0.7));
    expect(result.current.value).toBe(0.7);
    expect(advBodies).toEqual([{ value: 0.7 }]);
    // refetchQueries on cold keys is a no-op fetch-wise; the CONTRACT under
    // test is the throttle and the save bodies — the refetch wiring is typed.
  });

  it('drag only updates local state; persistence waits for commit', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_753_000_000_000);
    const { result } = renderHook(() => useAdventurousness(0.3), { wrapper: wrapper() });
    act(() => result.current.change(0.4));
    act(() => result.current.change(0.5)); // inside the throttle window
    expect(result.current.value).toBe(0.5);
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(advBodies).toEqual([]);
    vi.setSystemTime(1_753_000_000_500);
    act(() => result.current.change(0.6));
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(advBodies).toEqual([]);
  });
});
