import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { ArtistsToast } from './-discover.use-your-artists';

import { useYourArtists } from './-discover.use-your-artists';

let toasts: ArtistsToast[] = [];
let settingsPosts: unknown[] = [];
let watchPosts: { url: string; body: unknown }[] = [];
let allQueries: string[] = [];

function stub({
  enabled = ['spotify', 'lastfm'],
  connectedList = ['spotify', 'lastfm'],
  refreshAnswers = [{ stale: true }, { stale: false, artists: [{}, {}], total: 2 }],
  infoOk = true,
}: {
  enabled?: string[];
  connectedList?: string[];
  refreshAnswers?: Record<string, unknown>[];
  infoOk?: boolean;
} = {}) {
  settingsPosts = [];
  watchPosts = [];
  allQueries = [];
  let refreshCall = 0;
  server.use(
    http.get('/api/discover/your-artists/sources', () =>
      HttpResponse.json({ success: true, enabled, connected: connectedList }),
    ),
    http.post('/api/settings', async ({ request }) => {
      settingsPosts.push(await request.json());
      return HttpResponse.json({ success: true });
    }),
    http.post('/api/discover/your-artists/refresh', () => HttpResponse.json({ success: true })),
    http.get('/api/discover/your-artists', () => {
      const body = refreshAnswers[Math.min(refreshCall, refreshAnswers.length - 1)];
      refreshCall += 1;
      return HttpResponse.json({ success: true, ...body });
    }),
    http.get('/api/discover/your-artists/all', ({ request }) => {
      allQueries.push(new URL(request.url).search);
      return HttpResponse.json({
        success: true,
        total: 61,
        artists: [{ id: 1, artist_name: 'Aphex Twin' }],
      });
    }),
    http.get('/api/discover/your-artists/info/:id', () => {
      if (!infoOk) return HttpResponse.error();
      return HttpResponse.json({ success: true, genres: ['idm'], popularity: 80 });
    }),
    http.post('/api/watchlist/add', async ({ request }) => {
      watchPosts.push({ url: 'add', body: await request.json() });
      return HttpResponse.json({ success: true });
    }),
    http.post('/api/watchlist/remove', async ({ request }) => {
      watchPosts.push({ url: 'remove', body: await request.json() });
      return HttpResponse.json({ success: true });
    }),
  );
}

function mount() {
  const client = createTestQueryClient();
  return {
    client,
    ...renderHook(() => useYourArtists((t) => toasts.push(t)), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    }),
  };
}

beforeEach(() => {
  toasts = [];
  stub();
});

afterEach(() => {
  vi.useRealTimers();
  server.resetHandlers();
});

const pool = {
  id: 7,
  artist_name: 'Aphex Twin',
  active_source_id: 'sp1',
  active_source: 'spotify',
  on_watchlist: 0,
};

describe('useYourArtists — sources', () => {
  it('opens with the server state, falls back to defaults on failure', async () => {
    const { result } = mount();
    act(() => result.current.sources.openModal());
    await waitFor(() => expect(result.current.sources.state.spotify).toBe(true));
    expect(result.current.sources.state).toEqual({
      spotify: true,
      tidal: false,
      lastfm: true,
      deezer: false,
    });
    expect(result.current.sources.connected).toEqual(['spotify', 'lastfm']);
  });

  it('falls back to ALL defaults with nothing connected when the fetch dies', async () => {
    server.use(http.get('/api/discover/your-artists/sources', () => HttpResponse.error()));
    const { result } = mount();
    act(() => result.current.sources.openModal());
    // ARTISTS_DEFAULT_SOURCES: all four on (5613).
    await waitFor(() => expect(result.current.sources.state.deezer).toBe(true));
    expect(result.current.sources.state).toEqual({
      spotify: true,
      tidal: true,
      lastfm: true,
      deezer: true,
    });
    expect(result.current.sources.connected).toEqual([]);
  });

  it('toggles connected sources; a disconnected one refuses WITH the hint', async () => {
    const { result } = mount();
    act(() => result.current.sources.openModal());
    await waitFor(() => expect(result.current.sources.state.spotify).toBe(true));
    act(() => result.current.sources.toggle('spotify'));
    expect(result.current.sources.state.spotify).toBe(false);
    act(() => result.current.sources.toggle('tidal'));
    expect(result.current.sources.state.tidal).toBe(false);
    expect(toasts.at(-1)).toMatchObject({ level: 'warning' });
    expect(toasts.at(-1)!.message).toContain('Tidal not connected');
  });

  it('refuses an empty save; a real one posts the comma-joined key and closes', async () => {
    const { result } = mount();
    act(() => result.current.sources.openModal());
    await waitFor(() => expect(result.current.sources.state.spotify).toBe(true));
    act(() => result.current.sources.toggle('spotify'));
    act(() => result.current.sources.toggle('lastfm'));
    await act(() => result.current.sources.save());
    expect(toasts.at(-1)).toEqual({ message: 'Select at least one source', level: 'error' });
    expect(settingsPosts).toHaveLength(0);

    act(() => result.current.sources.toggle('spotify'));
    await act(() => result.current.sources.save());
    expect(settingsPosts).toEqual([{ discover: { your_artists_sources: 'spotify' } }]);
    expect(result.current.sources.open).toBe(false);
    expect(result.current.sources.savedEnabled).toEqual(['spotify']);
    expect(toasts.at(-1)).toEqual({
      message: 'Sources saved — refresh to apply',
      level: 'success',
    });
  });
});

describe('useYourArtists — refresh', () => {
  it('polls until settled, updates the cache, toasts the count', async () => {
    vi.useFakeTimers();
    const { result, client } = mount();
    await act(() => result.current.refresh.start());
    expect(result.current.refresh.refreshing).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(5100)); // first poll: stale
    expect(result.current.refresh.refreshing).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(5100)); // second: settled
    expect(result.current.refresh.refreshing).toBe(false);
    expect(toasts.at(-1)).toEqual({
      message: 'Found 2 artists from your services',
      level: 'success',
    });
    expect(client.getQueryData(['discover', 'your-artists'])).toMatchObject({ total: 2 });
  });

  it('DIVERGENCE 1: giving up re-enables the button', async () => {
    vi.useFakeTimers();
    stub({ refreshAnswers: [{ stale: true }] });
    const { result } = mount();
    await act(() => result.current.refresh.start());
    await act(() => vi.advanceTimersByTimeAsync(61 * 5000 + 100));
    expect(result.current.refresh.refreshing).toBe(false);
    expect(toasts).toEqual([]); // no success toast — it just stopped
  });
});

describe('useYourArtists — the View All modal', () => {
  it('opens fresh at page 1 and loads', async () => {
    const { result } = mount();
    act(() => result.current.browse.openModal());
    expect(result.current.browse.total).toBeNull();
    await waitFor(() => expect(result.current.browse.phase).toBe('ready'));
    expect(result.current.browse.total).toBe(61);
    expect(result.current.browse.artists).toHaveLength(1);
    expect(allQueries[0]).toContain('page=1');
    expect(allQueries[0]).not.toContain('source=');
  });

  it('debounces ONLY typing; pills and sort reload immediately', async () => {
    vi.useFakeTimers();
    const { result } = mount();
    act(() => result.current.browse.openModal());
    await act(() => vi.advanceTimersByTimeAsync(0));
    const before = allQueries.length;
    act(() => result.current.browse.filter({ source: 'tidal' }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(allQueries.length).toBe(before + 1);
    expect(allQueries.at(-1)).toContain('source=tidal');
    act(() => result.current.browse.filter({ search: 'aph' }));
    // Flushing microtasks proves typing did NOT fire a request — only the
    // 300ms timer may.
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(allQueries.length).toBe(before + 1); // not yet
    await act(() => vi.advanceTimersByTimeAsync(350));
    expect(allQueries.length).toBe(before + 2);
    expect(allQueries.at(-1)).toContain('search=aph');
    // Every filter resets to page 1 (the module's documented divergence).
    expect(result.current.browse.state.page).toBe(1);
  });

  it('REOPENS fresh at page 1 with no filters', async () => {
    const { result } = mount();
    act(() => result.current.browse.openModal());
    await waitFor(() => expect(result.current.browse.phase).toBe('ready'));
    act(() => result.current.browse.filter({ source: 'tidal' }));
    await waitFor(() => expect(allQueries.at(-1)).toContain('source=tidal'));
    act(() => result.current.browse.closeModal());
    act(() => result.current.browse.openModal());
    expect(result.current.browse.state).toEqual({ page: 1, source: '', sort: 'name', search: '' });
    expect(result.current.browse.total).toBeNull();
    await waitFor(() => expect(allQueries.at(-1)).not.toContain('source='));
  });

  it('pages without resetting filters', async () => {
    const { result } = mount();
    act(() => result.current.browse.openModal());
    await waitFor(() => expect(result.current.browse.phase).toBe('ready'));
    act(() => result.current.browse.page(2));
    await waitFor(() => expect(allQueries.at(-1)).toContain('page=2'));
  });
});

describe('useYourArtists — info modal + watch toggle', () => {
  it('opens with the pool, resolves enrichment, closes clean', async () => {
    const { result } = mount();
    act(() => result.current.info.open(pool));
    expect(result.current.info.phase).toBe('loading');
    expect(result.current.info.pool).toMatchObject({ artist_name: 'Aphex Twin' });
    await waitFor(() => expect(result.current.info.phase).toBe('ready'));
    expect(result.current.info.data).toMatchObject({ genres: ['idm'] });
    act(() => result.current.info.close());
    expect(result.current.info.pool).toBeNull();
  });

  it('gives up on a HUNG info endpoint after the 8s abort window', async () => {
    vi.useFakeTimers();
    server.use(http.get('/api/discover/your-artists/info/:id', () => new Promise<never>(() => {})));
    const { result } = mount();
    act(() => result.current.info.open(pool));
    await act(() => vi.advanceTimersByTimeAsync(8100));
    expect(result.current.info.phase).toBe('error');
  });

  it('marks a dead info endpoint as error', async () => {
    stub({ infoOk: false });
    const { result } = mount();
    act(() => result.current.info.open(pool));
    await waitFor(() => expect(result.current.info.phase).toBe('error'));
  });

  it('adds with name+source, then REMOVES on the next toggle via the override', async () => {
    const { result } = mount();
    await act(() => result.current.info.toggleWatch(pool));
    expect(watchPosts).toEqual([
      { url: 'add', body: { artist_id: 'sp1', artist_name: 'Aphex Twin', source: 'spotify' } },
    ]);
    expect(toasts.at(-1)).toEqual({ message: 'Added Aphex Twin to watchlist', level: 'success' });
    // _syncYaCardWatchlist: every card showing pool 7 now reads watched.
    expect(result.current.watchOverrides['7']).toBe(1);
    // The SECOND toggle consults the override, not the stale pool flag.
    await act(() => result.current.info.toggleWatch(pool));
    expect(watchPosts.at(-1)).toEqual({ url: 'remove', body: { artist_id: 'sp1' } });
    expect(toasts.at(-1)).toEqual({ message: 'Removed Aphex Twin from watchlist', level: 'info' });
    expect(result.current.watchOverrides['7']).toBe(0);
  });
});
