/**
 * every chart-type beatport list imports into SoulSync as a live mirror keyed
 * by its ref. the oldest flow sent lists to the download modal as a
 * chart-named compilation, so "Hype Top 10" became the album, and positional
 * ids let a second chart inherit the first one's sync matches; the one after
 * identified lists in a beatport-only window.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { beatportRefs, importBeatportList, saveBeatportSnapshot } from './-beatport.lists';
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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the refs', () => {
  it('builds the genre refs the backend parses', () => {
    expect(beatportRefs.genre({ slug: 'trance-main-floor', id: 7 }, 'hype-10')).toBe(
      'genre:trance-main-floor:7:hype-10',
    );
    expect(beatportRefs.home('top-100')).toBe('top-100');
  });
});

describe('importBeatportList', () => {
  function stub(body: unknown, status = 200) {
    const calls: { url: string; body: unknown }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, body: JSON.parse(typeof init?.body === 'string' ? init.body : '{}') });
        return new Response(typeof body === 'string' ? body : JSON.stringify(body), { status });
      }),
    );
    return calls;
  }

  it('posts the ref (and the genre name) and answers the mirror id', async () => {
    const calls = stub({ success: true, playlist_id: 8, source_playlist_id: CHART.ref });
    await expect(
      importBeatportList({ ref: CHART.ref, label: 'Hype', genreName: 'Trance' }),
    ).resolves.toBe(8);
    expect(calls).toEqual([
      { url: '/api/beatport/import', body: { ref: CHART.ref, genre_name: 'Trance' } },
    ]);
  });

  it('a chart card with only a url posts the url', async () => {
    const calls = stub({ success: true, playlist_id: 3 });
    await importBeatportList({ url: 'https://www.beatport.com/chart/a/1', label: 'A' });
    expect(calls[0].body).toEqual({ url: 'https://www.beatport.com/chart/a/1' });
  });

  it("throws the server's words, or a plain one when there are none", async () => {
    stub({ success: false, error: "Beatport didn't return this list." }, 502);
    await expect(importBeatportList({ ref: 'x', label: 'X' })).rejects.toThrow(
      "Beatport didn't return this list.",
    );
    stub({ success: false }, 500);
    await expect(importBeatportList({ ref: 'x', label: 'X' })).rejects.toThrow("Couldn't import X");
    stub('<html>', 500);
    await expect(importBeatportList({ ref: 'x', label: 'X' })).rejects.toThrow('(500)');
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
