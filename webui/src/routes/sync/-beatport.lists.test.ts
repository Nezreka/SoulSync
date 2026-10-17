/**
 * every chart-type beatport list opens as a beatport discovery (maV, oct 12
 * 2026). the old flow sent lists to the download modal as a chart-named
 * compilation, so "Hype Top 10" became the album, and positional ids let a
 * second chart inherit the first one's sync matches.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BeatportListEnv } from './-beatport.lists';

import {
  beatportListUrl,
  beatportRefs,
  fetchBeatportList,
  openBeatportList,
  resetBeatportListLatch,
  saveBeatportSnapshot,
} from './-beatport.lists';
import { SYNC_SOURCES } from './-sync.sources';
import { freshSourceState } from './-sync.state';
import { defaultDiscoveryStartBody } from './-ui/source-modals';

const CHART = {
  success: true,
  hash: 'genre:trance-main-floor:7:hype-10',
  ref: 'genre:trance-main-floor:7:hype-10',
  name: 'Trance (Main Floor) Hype Top 10',
  image: 'http://img.jpg',
  url: 'https://www.beatport.com/genre/trance-main-floor/7',
  track_count: 1,
  tracks: [
    {
      id: 'beatport_30438457',
      name: 'Sturm Der Nacht (Nur Für Dich) (Extended Mix)',
      artists: ['Talla 2XLC', 'Marc van Linden'],
      isrc: 'DEA312600932',
    },
  ],
};

function makeEnv(phases: Record<string, string> = {}) {
  const order: string[] = [];
  const env: BeatportListEnv = {
    showToast: vi.fn(),
    showLoadingOverlay: vi.fn(() => order.push('overlay')),
    hideLoadingOverlay: vi.fn(() => order.push('hide')),
    phaseOf: (id) => phases[id],
    seed: vi.fn(() => order.push('seed')),
    open: vi.fn(() => order.push('open')),
    startDiscovery: vi.fn(() => {
      order.push('discover');
    }),
  };
  return { env, order };
}

function stubFetch(body: unknown, status = 200) {
  const urls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify(body), { status });
    }),
  );
  return urls;
}

beforeEach(() => resetBeatportListLatch());
afterEach(() => {
  vi.unstubAllGlobals();
  resetBeatportListLatch();
});

describe('the list url', () => {
  it('asks by ref, or by url for a chart card', () => {
    expect(beatportListUrl({ ref: 'top-100', label: 'x' })).toBe('/api/beatport/list?ref=top-100');
    expect(beatportListUrl({ ref: 'genre:house:5:hype-10', label: 'x', genreName: 'House' })).toBe(
      '/api/beatport/list?ref=genre%3Ahouse%3A5%3Ahype-10&genre_name=House',
    );
    expect(beatportListUrl({ url: 'https://www.beatport.com/chart/a/1', label: 'x' })).toBe(
      '/api/beatport/list?url=https%3A%2F%2Fwww.beatport.com%2Fchart%2Fa%2F1',
    );
  });

  it('builds the genre refs the backend parses', () => {
    expect(beatportRefs.genre({ slug: 'trance-main-floor', id: 7 }, 'hype-10')).toBe(
      'genre:trance-main-floor:7:hype-10',
    );
    expect(beatportRefs.home('top-100')).toBe('top-100');
  });
});

describe('openBeatportList', () => {
  it('fetches, seeds the vertical, opens the modal and starts identifying', async () => {
    const urls = stubFetch(CHART);
    const { env, order } = makeEnv();
    await openBeatportList(
      { ref: CHART.ref, label: 'Trance Hype Top 10', genreName: 'Trance (Main Floor)' },
      env,
    );
    expect(urls[0]).toContain('/api/beatport/list?ref=');
    expect(order).toEqual(['overlay', 'hide', 'seed', 'open', 'discover']);
    expect(env.seed).toHaveBeenCalledWith(CHART.ref, {
      name: CHART.name,
      tracks: CHART.tracks,
      description: '1 tracks from Trance (Main Floor) Hype Top 10',
      source: 'beatport',
      image_url: CHART.image,
      chart_data: expect.objectContaining({ ref: CHART.ref, tracks: CHART.tracks }),
    });
    expect(env.open).toHaveBeenCalledWith(CHART.ref);
    // the chart_data carries the real ids and the isrc discovery looks up
    const chart = (env.startDiscovery as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(chart.tracks[0].id).toBe('beatport_30438457');
    expect(chart.tracks[0].isrc).toBe('DEA312600932');
  });

  it('reopens a list that is mid-identify or synced without restarting it', async () => {
    stubFetch(CHART);
    for (const phase of ['discovering', 'discovered', 'syncing', 'sync_complete', 'downloading']) {
      const { env } = makeEnv({ [CHART.ref]: phase });
      await openBeatportList({ ref: CHART.ref, label: 'x' }, env);
      expect(env.open).toHaveBeenCalledWith(CHART.ref);
      expect(env.startDiscovery).not.toHaveBeenCalled();
    }
  });

  it('identifies a list whose state is still fresh', async () => {
    stubFetch(CHART);
    const { env } = makeEnv({ [CHART.ref]: 'fresh' });
    await openBeatportList({ ref: CHART.ref, label: 'x' }, env);
    expect(env.startDiscovery).toHaveBeenCalledTimes(1);
  });

  it('toasts the backend error and opens nothing', async () => {
    stubFetch({ success: false, error: 'Beatport is blocking requests' }, 502);
    const { env } = makeEnv();
    await openBeatportList({ ref: 'top-100', label: 'Beatport Top 100' }, env);
    expect(env.showToast).toHaveBeenCalledWith(
      'Error loading Beatport Top 100: Beatport is blocking requests',
      'error',
    );
    expect(env.hideLoadingOverlay).toHaveBeenCalled();
    expect(env.open).not.toHaveBeenCalled();
  });

  it('treats an empty list as an error', async () => {
    stubFetch({ ...CHART, tracks: [] });
    const { env } = makeEnv();
    await openBeatportList({ ref: CHART.ref, label: 'x' }, env);
    expect(env.showToast).toHaveBeenCalledWith(
      `Error loading x: No tracks found in ${CHART.name}`,
      'error',
    );
    expect(env.seed).not.toHaveBeenCalled();
  });

  it('survives a non-json answer', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>nope</html>', { status: 500 })),
    );
    const { env } = makeEnv();
    await openBeatportList({ ref: 'top-100', label: 'Top' }, env);
    expect(env.showToast).toHaveBeenCalledWith(
      'Error loading Top: Beatport list failed (500)',
      'error',
    );
  });

  it('refuses a card with no link', async () => {
    const urls = stubFetch(CHART);
    const { env } = makeEnv();
    await openBeatportList({ label: 'Broken Chart' }, env);
    expect(urls).toEqual([]);
    expect(env.showToast).toHaveBeenCalledWith('No link for Broken Chart', 'error');
  });

  it('drops a second click while the first list is loading', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let fetches = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        fetches++;
        await gate;
        return new Response(JSON.stringify(CHART), { status: 200 });
      }),
    );
    const { env } = makeEnv();
    const first = openBeatportList({ ref: CHART.ref, label: 'x' }, env);
    await openBeatportList({ ref: CHART.ref, label: 'x' }, env);
    expect(fetches).toBe(1);
    release();
    await first;
    expect(env.open).toHaveBeenCalledTimes(1);
    // and the latch lets go afterwards
    await openBeatportList({ ref: CHART.ref, label: 'x' }, env);
    expect(fetches).toBe(2);
  });
});

describe("the beatport modal's start body", () => {
  it('sends the seeded chart_data, which the endpoint requires', () => {
    // without a body the endpoint answers 400 and Start / Rediscover did nothing
    const state = {
      ...freshSourceState(SYNC_SOURCES.beatport, CHART.ref),
      playlist: { name: CHART.name, tracks: CHART.tracks, chart_data: CHART },
    };
    expect(defaultDiscoveryStartBody(SYNC_SOURCES.beatport, state)).toBe(CHART);
  });

  it('falls back to name and tracks for a state seeded without chart_data', () => {
    const state = {
      ...freshSourceState(SYNC_SOURCES.beatport, 'old'),
      playlist: { name: 'Old', tracks: [{ id: 'a' }] },
    };
    expect(defaultDiscoveryStartBody(SYNC_SOURCES.beatport, state)).toEqual({
      name: 'Old',
      tracks: [{ id: 'a' }],
    });
  });

  it('leaves listenbrainz and the bodiless sources as they were', () => {
    const state = {
      ...freshSourceState(SYNC_SOURCES.listenbrainz, 'm'),
      playlist: { name: 'LB', tracks: [] },
    };
    expect(defaultDiscoveryStartBody(SYNC_SOURCES.listenbrainz, state)).toEqual({
      playlist: { name: 'LB', tracks: [] },
    });
    expect(
      defaultDiscoveryStartBody(SYNC_SOURCES.tidal, freshSourceState(SYNC_SOURCES.tidal, 't')),
    ).toBeUndefined();
  });
});

describe('fetchBeatportList', () => {
  it('returns the list when it has a ref and tracks', async () => {
    const urls = stubFetch(CHART);
    const data = await fetchBeatportList({ ref: CHART.ref, label: 'Hype' });
    expect(data.ref).toBe(CHART.ref);
    expect(urls[0]).toBe(beatportListUrl({ ref: CHART.ref, label: 'Hype' }));
  });

  it('throws the server error, or the status when there is none', async () => {
    stubFetch({ success: false, error: 'chart not found' }, 404);
    await expect(fetchBeatportList({ ref: 'x', label: 'X' })).rejects.toThrow('chart not found');
    stubFetch({ success: false }, 502);
    await expect(fetchBeatportList({ ref: 'x', label: 'X' })).rejects.toThrow('(502)');
  });

  it('an empty list is an error that names it', async () => {
    stubFetch({ ...CHART, tracks: [] });
    await expect(fetchBeatportList({ ref: CHART.ref, label: 'Hype' })).rejects.toThrow(
      `No tracks found in ${CHART.name}`,
    );
  });

  it('a body that is not json reports the status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>', { status: 500 })),
    );
    await expect(fetchBeatportList({ ref: 'x', label: 'X' })).rejects.toThrow('(500)');
  });
});

describe('saveBeatportSnapshot', () => {
  it('posts the ref and hands back the result', async () => {
    const calls: Array<{ url: string; body: string }> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, body: typeof init?.body === 'string' ? init.body : '' });
        return new Response(JSON.stringify({ success: true, id: 'top-10@2026-10-16' }));
      }),
    );
    const out = await saveBeatportSnapshot('top-10');
    expect(calls).toEqual([
      { url: '/api/beatport/snapshot', body: JSON.stringify({ ref: 'top-10' }) },
    ]);
    expect(out.success).toBe(true);
  });

  it('a body that is not json comes back as a failure, not a throw', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('oops', { status: 503 })),
    );
    expect(await saveBeatportSnapshot('top-10')).toEqual({
      success: false,
      error: 'Snapshot failed (503)',
    });
  });
});
