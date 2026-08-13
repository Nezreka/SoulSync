import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { ArtMapNode } from './-discover.artist-map';

import { artMap } from './-discover.artist-map';
import { useArtistMap } from './-discover.use-artist-map';

let toasts: unknown[] = [];
let genresHits = 0;
let listHits = 0;

const rawNode = (id: number, genre: string, type = 'similar') => ({
  id,
  name: `N${id}`,
  genres: [genre],
  type,
  popularity: 50,
  image_url: '',
});

function stub({
  nodes = [rawNode(1, 'idm', 'watchlist'), rawNode(2, 'idm'), rawNode(3, 'ambient')],
  edges = [{ source: 1, target: 2 }],
  watchlistEmpty = false,
  exploreStatus = 200,
  exploreNodes = [
    { ...rawNode(9, 'idm'), ring: 0 },
    { ...rawNode(10, 'idm'), ring: 1 },
  ],
}: Record<string, unknown> = {}) {
  genresHits = 0;
  listHits = 0;
  server.use(
    http.get('/api/discover/artist-map', () => {
      if (watchlistEmpty) return HttpResponse.json({ success: true, nodes: [] });
      return HttpResponse.json({
        success: true,
        nodes,
        edges,
        watchlist_count: 1,
        similar_count: 2,
      });
    }),
    http.get('/api/discover/artist-map/genre-list', () => {
      listHits += 1;
      return HttpResponse.json({
        genres: [
          { name: 'idm', count: 2 },
          { name: 'ambient', count: 1 },
        ],
      });
    }),
    http.get('/api/discover/artist-map/genres', () => {
      genresHits += 1;
      return HttpResponse.json({
        success: true,
        genres: [
          { name: 'idm', count: 2, artist_ids: [1, 2] },
          { name: 'ambient', count: 1, artist_ids: [3] },
        ],
        nodes: { 1: rawNode(1, 'idm'), 2: rawNode(2, 'idm'), 3: rawNode(3, 'ambient') },
      });
    }),
    http.get('/api/discover/artist-map/explore', () => {
      if (exploreStatus !== 200) return HttpResponse.json({ success: false }, { status: 404 });
      return HttpResponse.json({
        success: true,
        center: 'Aphex Twin',
        nodes: exploreNodes,
        edges: [],
      });
    }),
  );
}

function mount() {
  return renderHook(() => useArtistMap((t) => toasts.push(t)));
}

beforeEach(() => {
  toasts = [];
  stub();
  artMap.width = 1000;
  artMap.height = 600;
  artMap._panelW = 320;
  artMap.canvas = null;
  artMap.ctx = null;
  artMap.placed = [];
  artMap.edges = [];
  artMap._islands = [];
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  server.resetHandlers();
});

describe('useArtistMap — watchlist', () => {
  it('walks loading → built world: islands, edges, stats, reveal, focus', async () => {
    // Gate the payload so the loading phase is observable.
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.get('/api/discover/artist-map', async () => {
        await gate;
        return HttpResponse.json({
          success: true,
          nodes: [rawNode(1, 'idm', 'watchlist'), rawNode(2, 'idm'), rawNode(3, 'ambient')],
          edges: [{ source: 1, target: 2 }],
          watchlist_count: 1,
          similar_count: 2,
        });
      }),
    );
    const { result } = mount();
    expect(result.current.kind).toBeNull();
    let open!: Promise<void>;
    act(() => {
      open = result.current.openWatchlist();
    });
    expect(result.current.kind).toBe('watchlist');
    expect(result.current.title).toBe('Artist Map');
    expect(result.current.loading).toBe('Building artist map...');
    release();
    await act(async () => {
      await open;
    });
    expect(result.current.loading).toBeNull();
    expect(result.current.stats).toBe('1 watchlist · 2 similar');
    // Two genre islands, laid out; every bubble revealed.
    expect((artMap._islands ?? []).map((i) => i.name)).toEqual(
      expect.arrayContaining(['Idm', 'Ambient']),
    );
    expect(artMap.placed.length).toBeGreaterThan(0);
    expect(artMap._oneIsland).toBe(true);
    expect(artMap.edges.length).toBeGreaterThan(0);
    expect(result.current.focusVersion).toBeGreaterThan(0);
  });

  it('an EMPTY watchlist keeps the map open with the guidance copy', async () => {
    stub({ watchlistEmpty: true });
    const { result } = mount();
    await act(async () => result.current.openWatchlist());
    expect(result.current.kind).toBe('watchlist');
    expect(result.current.loading).toBe(
      'No watchlist artists. Add artists to your watchlist first.',
    );
    expect(artMap.placed).toEqual([]);
  });

  it('a payload landing AFTER close is dropped', async () => {
    const { result } = mount();
    let open!: Promise<void>;
    act(() => {
      open = result.current.openWatchlist();
    });
    act(() => result.current.close());
    await act(async () => {
      await open;
    });
    expect(result.current.kind).toBeNull();
    expect(result.current.loading).toBeNull();
    expect(artMap.placed).toEqual([]);
  });
});

describe('useArtistMap — genre map', () => {
  it('loads the sidebar list, builds from genre groups, focuses the selection', async () => {
    const { result } = mount();
    await act(async () => result.current.openGenre('ambient'));
    expect(result.current.kind).toBe('genre');
    expect(result.current.title).toBe('Genre Map');
    expect(result.current.sidebarGenres).toEqual([
      { name: 'idm', count: 2 },
      { name: 'ambient', count: 1 },
    ]);
    expect(result.current.selectedGenre).toBe('ambient');
    expect(result.current.stats).toBe('2 genres · 3 artists');
    const focused = (artMap._islands ?? [])[artMap._focusIdx ?? 0];
    expect(focused.name.toLowerCase()).toBe('ambient');
  });

  it('switchGenre is a pure rebuild off the CACHED payload — no refetch', async () => {
    const { result } = mount();
    await act(async () => result.current.openGenre('idm'));
    expect(genresHits).toBe(1);
    await act(async () => result.current.switchGenre('ambient'));
    expect(genresHits).toBe(1);
    // Not even the sidebar list is refetched — a switch touches NO network.
    expect(listHits).toBe(1);
    expect(result.current.selectedGenre).toBe('ambient');
  });
});

describe('useArtistMap — explorer', () => {
  it('builds the explore world with its stats', async () => {
    const { result } = mount();
    await act(async () => result.current.openExplorer('Aphex Twin'));
    expect(result.current.kind).toBe('explorer');
    expect(result.current.title).toBe('Artist Explorer');
    // `Aphex Twin · N similar · M extended` (9699-9702).
    expect(result.current.stats).toBe('Aphex Twin · 1 similar · 0 extended');
    expect(result.current.loading).toBeNull();
  });

  it('a 404 shows the not-a-real-artist copy, then SELF-CLOSES at 2.5s', async () => {
    vi.useFakeTimers();
    stub({ exploreStatus: 404 });
    const { result } = mount();
    await act(async () => result.current.openExplorer('zzz'));
    expect(result.current.loading).toContain("doesn't appear to be a real artist");
    await act(() => vi.advanceTimersByTimeAsync(2600));
    expect(result.current.kind).toBeNull();
  });

  it('refuses an empty name outright', async () => {
    const { result } = mount();
    await act(async () => result.current.openExplorer(''));
    expect(result.current.kind).toBeNull();
  });
});

describe('useArtistMap — nav, pool adapter, host', () => {
  it('islandNav bumps focusVersion only when it actually moved', async () => {
    const { result } = mount();
    await act(async () => result.current.openWatchlist());
    const v = result.current.focusVersion;
    act(() => result.current.islandNav(1));
    expect(result.current.focusVersion).toBe(v + 1);
    // Collapse to one island → nav is inert (below two).
    artMap._islands = [artMap._islands![0]];
    act(() => result.current.islandNav(1));
    expect(result.current.focusVersion).toBe(v + 1);
  });

  it('poolFor adapts a map node exactly as openYourArtistInfoModal_direct', async () => {
    const { result } = mount();
    await act(async () => result.current.openWatchlist());
    const node = artMap.placed.find((n) => n.name === 'N1') as ArtMapNode & {
      spotify_id?: string;
    };
    node.spotify_id = 'sp1';
    const pool = result.current.poolFor(node);
    expect(pool.artist_name).toBe('N1');
    expect(pool.active_source).toBe('spotify');
    expect(pool.active_source_id).toBe('sp1');
    expect(pool.on_watchlist).toBe(1);
    // Related come from the remapped edges, both directions.
    expect((pool._related ?? []).map((r) => (r as ArtMapNode).name)).toContain('N2');
  });

  it('the host answers visibility from hook state and closes through it', async () => {
    const { result } = mount();
    const host = result.current.makeHost({
      showTooltip: () => {},
      showPanelArtist: () => {},
      showContextMenu: () => {},
      hideContextMenu: () => {},
      focusSearch: () => true,
      toggleSimilar: () => {},
      resized: () => {},
    });
    expect(host.isVisible()).toBe(false);
    await act(async () => result.current.openWatchlist());
    expect(host.isVisible()).toBe(true);
    act(() => host.close());
    expect(result.current.kind).toBeNull();
  });
});
