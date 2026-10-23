import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  fetchSinCharts,
  openSinChart,
  type SinChart,
  sinChartStatus,
  takePendingMirroredOpen,
} from './charts';

const chart = (over: Partial<SinChart> = {}): SinChart => ({
  key: 'trending',
  name: 'Trending in SoulSync',
  week: '2026-W43',
  image_url: null,
  cover_tiles: [],
  track_count: 125,
  matched: 125,
  ready: true,
  playlist_id: null,
  ...over,
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe('SIN charts', () => {
  it('offers only charts that are fully matched', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({ charts: [chart(), chart({ key: 'rising', ready: false, matched: 40 })] }),
      ),
    );
    expect((await fetchSinCharts()).map((c) => c.key)).toEqual(['trending']);
  });

  it('a failed fetch offers nothing rather than throwing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('nope', { status: 500 })),
    );
    expect(await fetchSinCharts()).toEqual([]);
  });

  it('opening a chart hands its playlist to Sync and goes there; nothing else', async () => {
    const fetchMock = vi.fn(async () => Response.json({ playlist_id: 42 }));
    vi.stubGlobal('fetch', fetchMock);
    const navigate = vi.fn();
    window.navigateToPage = navigate as never;
    expect(await openSinChart(chart())).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith('/api/metasync/charts/trending/open', {
      method: 'POST',
    });
    expect(navigate).toHaveBeenCalledWith('sync');
    expect(takePendingMirroredOpen()).toBe(42);
    expect(takePendingMirroredOpen()).toBeNull(); // read once
  });

  it('says why a chart would not open', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({ error: 'That chart is no longer offered' }, { status: 404 }),
      ),
    );
    expect(await openSinChart(chart())).toBe('That chart is no longer offered');
  });

  it('the card line', () => {
    expect(sinChartStatus(chart())).toBe('125 tracks');
    expect(sinChartStatus(chart({ playlist_id: 7 }))).toBe('125 tracks · in your playlists');
  });
});
