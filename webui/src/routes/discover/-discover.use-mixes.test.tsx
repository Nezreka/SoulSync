import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { fetchDiscoveryWeekly, fetchReleaseRadar, fetchSeasonalPlaylist } from './-discover.api';
import { useDiscoverMixes } from './-discover.use-mixes';

/**
 * The Your Mixes registry hook.
 *
 * The vanilla shelf is arrival-ordered — whatever loader finished first sits
 * first, so the shelf shuffles with network timing. The documented divergence
 * here is the point under test: the SAME cards, in feeder-declaration order,
 * deterministically.
 */

const track = (name: string) => ({ track_name: name, artist_name: 'A' });

interface StubOptions {
  radar?: unknown[] | 'error';
  weekly?: unknown[];
  seasonalPlaylist?: unknown[];
  playlistAvailable?: boolean;
  personalized?: unknown[];
  decades?: unknown[];
}

let hits: string[] = [];

function stub({
  radar = [],
  weekly = [],
  seasonalPlaylist = [],
  playlistAvailable = true,
  personalized = [],
  decades = [],
}: StubOptions = {}) {
  hits = [];
  const json = (body: Record<string, unknown>) => HttpResponse.json({ success: true, ...body });
  server.use(
    http.get('/api/discover/release-radar', () => {
      hits.push('radar');
      if (radar === 'error') return HttpResponse.json({}, { status: 500 });
      return json({ tracks: radar });
    }),
    http.get('/api/discover/discovery-weekly', () => json({ tracks: weekly })),
    http.get('/api/discover/seasonal/current', () => {
      hits.push('seasonal-current');
      return json({
        season: 'winter',
        name: 'Winter',
        icon: '❄️',
        playlist_available: playlistAvailable,
      });
    }),
    http.get('/api/discover/seasonal/winter/playlist', () => {
      hits.push('seasonal-playlist');
      return json({ tracks: seasonalPlaylist });
    }),
    ...[
      '/api/discover/personalized/popular-picks',
      '/api/discover/personalized/hidden-gems',
      '/api/discover/personalized/discovery-shuffle',
      '/api/discover/personalized/listening-mix',
    ].map((path) => http.get(path, () => json({ tracks: personalized }))),
    http.get('/api/discover/decades/available', () => {
      hits.push('decades');
      return json({ decades });
    }),
  );
}

function mount(belowFoldReady = true) {
  const client = createTestQueryClient();
  return renderHook(() => useDiscoverMixes(belowFoldReady), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}

afterEach(() => {
  server.resetHandlers();
});

describe('useDiscoverMixes', () => {
  it('holds the SHARED below-fold queries until tier 1 settles', async () => {
    // seasonal + decades share cache keys with useDiscoverPage's gated tier-2
    // entries; an ungated observer here would fire them at mount and defeat
    // the tiering. Slow externals (radar) fire regardless — that is their
    // whole point.
    stub({ radar: [track('r')], decades: [{ year: 1980, track_count: 1 }] });
    const { result } = mount(false);
    await waitFor(() => expect(result.current.mixes.length).toBeGreaterThan(0));
    expect(hits).toContain('radar');
    expect(hits).not.toContain('seasonal-current');
    expect(hits).not.toContain('decades');
    expect(result.current.decadeMixes).toEqual([]);
  });

  it('renders every fed mix in DECLARATION order, not arrival order', async () => {
    stub({
      radar: [track('r')],
      weekly: [track('w')],
      seasonalPlaylist: [track('s')],
      personalized: [track('p')],
    });
    const { result } = mount();
    await waitFor(() => expect(result.current.mixes).toHaveLength(7));
    expect(result.current.mixes.map((m) => m.key)).toEqual([
      'release_radar',
      'discovery_weekly',
      'seasonal_playlist',
      'popular_picks',
      'hidden_gems',
      'listening_mix',
      'discovery_shuffle',
    ]);
    // The vanilla's exact card copy (2079-2083, 2108-2112, 4568-4572 …).
    expect(result.current.mixes[0].title).toBe('Fresh Tape');
    expect(result.current.mixes[0].subtitle).toBe('New releases from artists you follow');
    expect(result.current.mixes[1].title).toBe('The Archives');
    expect(result.current.mixes[3].subtitle).toBe('Popular tracks from artists you love');
    expect(result.current.mixes[5].title).toBe('Your Listening Mix');
    // Every card carries its own key as syncKey.
    expect(result.current.mixes.every((m) => m.syncKey === m.key)).toBe(true);
  });

  it('omits a feeder that returned nothing — no empty cards', async () => {
    stub({ personalized: [track('p')] });
    const { result } = mount();
    await waitFor(() => expect(result.current.mixes.length).toBeGreaterThan(0));
    expect(result.current.mixes.map((m) => m.key)).toEqual([
      'popular_picks',
      'hidden_gems',
      'listening_mix',
      'discovery_shuffle',
    ]);
  });

  it('titles the seasonal mix from the season, subtitle lowercased', async () => {
    stub({ seasonalPlaylist: [track('s')] });
    const { result } = mount();
    await waitFor(() =>
      expect(result.current.mixes.find((m) => m.key === 'seasonal_playlist')).toBeDefined(),
    );
    const seasonal = result.current.mixes.find((m) => m.key === 'seasonal_playlist')!;
    expect(seasonal.title).toBe('❄️ Winter Mix');
    expect(seasonal.subtitle).toBe('Curated playlist for winter');
  });

  it('never fetches the playlist of a season that does not advertise one', async () => {
    // The 4285 guard: playlist_available false → the loader is not even built.
    stub({ playlistAvailable: false, personalized: [track('p')] });
    const { result } = mount();
    await waitFor(() => expect(result.current.mixes.length).toBe(4));
    expect(hits).not.toContain('seasonal-playlist');
  });

  it('a dead slow-external feeder costs its own card and nothing else', async () => {
    stub({ radar: 'error', weekly: [track('w')], personalized: [track('p')] });
    const { result } = mount();
    await waitFor(() => expect(result.current.mixes).toHaveLength(5));
    expect(result.current.mixes.map((m) => m.key)).not.toContain('release_radar');
    expect(result.current.mixes[0].key).toBe('discovery_weekly');
  });

  it('builds the Time Machine shelf from the available decades', async () => {
    stub({
      decades: [
        { year: 1980, track_count: 12 },
        { year: 1990, track_count: 3 },
      ],
    });
    const { result } = mount();
    await waitFor(() => expect(result.current.decadeMixes).toHaveLength(2));
    expect(result.current.decadeMixes[0].key).toBe('decade_1980');
    expect(result.current.decadeMixes[0].title).toBe('1980s');
    expect(result.current.decadeMixes[0].subtitle).toBe('1980s Classics');
    expect(result.current.decadeMixes[0].trackCount).toBe(12);
  });

  it('the registry resolves BOTH shelves by key, for the shared modal', async () => {
    stub({ personalized: [track('p')], decades: [{ year: 1980, track_count: 1 }] });
    const { result } = mount();
    await waitFor(() => expect(Object.keys(result.current.registry).length).toBe(5));
    expect(result.current.registry['hidden_gems']).toBeDefined();
    expect(result.current.registry['decade_1980']).toBeDefined();
  });
});

describe('the feeder fetchers', () => {
  it('wrap their endpoints in section outcomes, error included', async () => {
    stub({ radar: [track('r')], weekly: [] });
    const radar = await fetchReleaseRadar();
    expect(radar.kind).toBe('ok');
    expect((radar as { data: { tracks: unknown[] } }).data.tracks).toEqual([track('r')]);
    expect((await fetchDiscoveryWeekly()).kind).toBe('ok');

    stub({ radar: 'error' });
    expect((await fetchReleaseRadar()).kind).toBe('error');
  });

  it('keys the seasonal playlist path by the season', async () => {
    stub({ seasonalPlaylist: [track('s')] });
    const out = await fetchSeasonalPlaylist('winter');
    expect(out.kind).toBe('ok');
    expect(hits).toContain('seasonal-playlist');
  });
});
