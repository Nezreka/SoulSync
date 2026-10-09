import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscogRelease } from './-artist-detail.discography-modal';

import {
  buildDiscographyPayload,
  defaultFutureReleases,
  DISCOG_DEFAULT_FILTERS,
  discogCardView,
  discogCardVisible,
  discogFooter,
  discogItemStatus,
  loadDiscographyForModal,
  releasesFromPageDiscography,
  streamDiscographyDownload,
  watchArtistWithSettings,
  type FutureReleases,
} from './-artist-detail.discography-modal';

/** Mirror of the modal's future-releases defaults for the watch-flow tests. */
const WATCHLIST_ADD_DEFAULTS_FOR_TEST: FutureReleases = {
  include_albums: true,
  include_eps: true,
  include_singles: true,
  include_live: false,
  include_remixes: false,
  include_acoustic: false,
  include_compilations: false,
  include_instrumentals: false,
  auto_download_pref: null,
};

/**
 * The Download Discography layer: the page's releases (gap cards included,
 * #1067), the #877 filter gate, Deluxe-first payload ordering, and the #830
 * honest per-album status.
 */

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.removeItem('discog_gapfill');
});

describe('releasesFromPageDiscography', () => {
  it('flattens the page buckets in order, tagging each with its section', () => {
    const releases = releasesFromPageDiscography({
      albums: [{ id: 'a', title: 'Meteora', image_url: null }],
      eps: [{ id: 'e', name: 'Collision Course', track_count: 6 }],
      singles: [{ id: 's', title: 'Two Faced' }],
    });
    expect(releases.map((r) => [r.id, r._type, r.name])).toEqual([
      ['a', 'album', 'Meteora'],
      ['e', 'ep', 'Collision Course'],
      ['s', 'single', 'Two Faced'],
    ]);
    expect(releases[0].image_url).toBeUndefined();
    expect(releases[1].total_tracks).toBe(6);
  });
});

describe('loadDiscographyForModal', () => {
  const stubEnhanced = (artist: object | null) => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        calls.push(url);
        return new Response(
          JSON.stringify(artist ? { success: true, artist } : { success: false }),
        );
      }),
    );
    return calls;
  };

  it('lists exactly what the page shows, and never refetches the discography', async () => {
    // discord, SeadogsBooty: the page showed deezer's 2 EPs, the modal refetched
    // with no source and pulled musicbrainz's Underground EPs on top
    const calls = stubEnhanced({ spotify_artist_id: 'sp1' });
    const data = await loadDiscographyForModal(42, 'Linkin Park', {
      source: 'deezer',
      albums: [{ id: 'a1', name: 'Meteora', track_count: 13, release_date: '2003-03-25' }],
      eps: [
        { id: 'e1', title: 'A Thousand Suns: Puerta De Alcalá', track_count: 6 },
        { id: 'e2', title: 'Collision Course', track_count: 6 },
      ],
      singles: [],
    });
    expect(calls).toEqual(['/api/library/artist/42/enhanced']);
    expect(data?.artist).toEqual({ id: 'sp1', name: 'Linkin Park', source: 'deezer' });
    expect(data?.releases.map((r) => [r._type, r.name, r.total_tracks])).toEqual([
      ['album', 'Meteora', 13],
      ['ep', 'A Thousand Suns: Puerta De Alcalá', 6],
      ['ep', 'Collision Course', 6],
    ]);
  });

  it('keeps gap cards the page merged in, with their own source (#1067)', async () => {
    stubEnhanced(null);
    const data = await loadDiscographyForModal(42, 'Aphex Twin', {
      source: 'spotify',
      albums: [
        { id: 'a1', name: 'SAW' },
        { id: 'g1', title: 'Druqks', _gap_source: 'deezer', _gap_track_count: 30 },
      ],
    });
    expect(data?.artist.id).toBe(42);
    expect(data?.releases[1]).toMatchObject({
      name: 'Druqks',
      total_tracks: 30,
      _gap_source: 'deezer',
      _type: 'album',
    });
    expect(data?.releases[0]._gap_source).toBeUndefined();
  });

  it('an empty page discography resolves to null without fetching', async () => {
    const calls = stubEnhanced(null);
    expect(await loadDiscographyForModal(42, 'X', { albums: [], eps: [], singles: [] })).toBeNull();
    expect(calls).toEqual([]);
  });
});

describe('cards and filters (#877)', () => {
  const LIVE: DiscogRelease = { id: 1, name: 'Live in Berlin', _type: 'album' };

  it('derives completion state, and unowned releases come pre-checked', () => {
    const owned = discogCardView(
      { id: 1, name: 'SAW', _type: 'album' },
      { albums: [{ id: 1, status: 'completed' }] },
    );
    expect(owned).toMatchObject({ statusClass: 'owned', statusIcon: '✓', checkedByDefault: false });
    const partial = discogCardView(
      { id: 1, name: 'SAW', _type: 'album' },
      { albums: [{ id: 1, status: 'partial' }] },
    );
    expect(partial).toMatchObject({
      statusClass: 'partial',
      statusIcon: '◐',
      checkedByDefault: true,
    });
  });

  it('hides on category OFF or an active content exclusion', () => {
    const view = discogCardView(LIVE, {});
    expect(view.isLive).toBe(true);
    expect(discogCardVisible(view, 'album', DISCOG_DEFAULT_FILTERS)).toBe(true);
    expect(discogCardVisible(view, 'album', { ...DISCOG_DEFAULT_FILTERS, live: false })).toBe(
      false,
    );
    expect(discogCardVisible(view, 'album', { ...DISCOG_DEFAULT_FILTERS, album: false })).toBe(
      false,
    );
  });

  it('counts the footer and gates the submit', () => {
    expect(discogFooter([{ tracks: 10 }, { tracks: 3 }])).toEqual({
      info: '2 releases · 13 tracks',
      submitText: 'Add 2 to Wishlist',
      bothText: 'Wishlist + Watchlist',
      disabled: false,
    });
    expect(discogFooter([])).toEqual({
      info: '0 releases · 0 tracks',
      submitText: 'Select releases',
      bothText: 'Select releases',
      disabled: true,
    });
  });
});

describe('the download payload', () => {
  it('sorts Deluxe-first and gives gap-fill entries THEIR source (#1067)', () => {
    const payload = buildDiscographyPayload(
      [
        { id: 'std', name: 'SAW', tracks: 13, gapSource: null },
        { id: 'gap', name: 'Druqks', tracks: 5, gapSource: 'deezer' },
        { id: 'deluxe', name: 'SAW (Deluxe)', tracks: 20, gapSource: null },
      ],
      { id: 'sp1', name: 'Aphex Twin', source: 'Spotify' },
    );
    expect(payload.albums.map((a) => a.id)).toEqual(['deluxe', 'std', 'gap']);
    expect(payload.albums[0].source).toBe('spotify');
    expect(payload.albums[2].source).toBe('deezer');
    expect(payload.source).toBe('spotify');
  });

  it('sends the section each release was shown in, so the server files it there', () => {
    const payload = buildDiscographyPayload(
      [
        { id: 'fs', name: 'Flow State Sampler', tracks: 3, gapSource: null, albumType: 'album' },
        { id: 'tb', name: 'Tranquility Base', tracks: 9, gapSource: null, albumType: 'ep' },
        { id: 'old', name: 'No Section', tracks: 1, gapSource: null },
      ],
      { id: 'ab', name: 'Above & Beyond', source: 'deezer' },
    );
    const byId = Object.fromEntries(payload.albums.map((a) => [a.id, a]));
    expect(byId.fs.album_type).toBe('album');
    expect(byId.tb.album_type).toBe('ep');
    expect('album_type' in byId.old).toBe(false);
  });
});

describe('discogItemStatus (#830)', () => {
  it('spells out every skip reason instead of "No new tracks"', () => {
    expect(
      discogItemStatus({
        tracks_added: 2,
        tracks_skipped_owned: 3,
        tracks_skipped: 1,
        tracks_skipped_artist: 4,
        tracks_skipped_filter: 5,
      }),
    ).toBe('2 added, 3 already owned, 1 already queued, 4 by other artists, 5 filtered out');
    expect(discogItemStatus({})).toBe('No tracks');
  });
});

describe('the download stream', () => {
  it('routes per-album updates and the completion line', async () => {
    const encoder = new TextEncoder();
    const lines = [
      '{"album_id":"a1","status":"done","tracks_added":3}\n',
      '{"status":"complete","total_added":3,"total_skipped":1}\n',
    ];
    let i = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (_input: RequestInfo | URL, _init?: RequestInit) =>
          new Response(
            new ReadableStream({
              pull(controller) {
                if (i < lines.length) controller.enqueue(encoder.encode(lines[i++]));
                else controller.close();
              },
            }),
          ),
      ),
    );
    const albums: unknown[] = [];
    const complete = vi.fn();
    await streamDiscographyDownload(
      'sp1',
      { albums: [], artist_name: 'A', source: null },
      (u) => albums.push(u),
      complete,
    );
    expect(albums).toEqual([{ album_id: 'a1', status: 'done', tracks_added: 3 }]);
    expect(complete).toHaveBeenCalledWith({
      total_added: 3,
      total_skipped: 1,
      failed_releases: [],
    });
  });

  it('carries failed releases through the completion line for retry', async () => {
    const encoder = new TextEncoder();
    const failed = [
      { album_id: 'a9', name: 'Lost EP', source: 'deezer', error: 'Album not found' },
    ];
    const lines = [
      '{"album_id":"a9","status":"error","message":"Album not found"}\n',
      `{"status":"complete","total_added":0,"total_skipped":0,"failed_releases":${JSON.stringify(failed)}}\n`,
    ];
    let i = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (_input: RequestInfo | URL, _init?: RequestInit) =>
          new Response(
            new ReadableStream({
              pull(controller) {
                if (i < lines.length) controller.enqueue(encoder.encode(lines[i++]));
                else controller.close();
              },
            }),
          ),
      ),
    );
    const complete = vi.fn();
    await streamDiscographyDownload(
      'sp1',
      { albums: [], artist_name: 'A', source: null },
      () => {},
      complete,
    );
    expect(complete).toHaveBeenCalledWith({
      total_added: 0,
      total_skipped: 0,
      failed_releases: failed,
    });
  });

  it('tolerates a completion line without failed_releases (older server)', async () => {
    const encoder = new TextEncoder();
    const lines = ['{"status":"complete","total_added":2,"total_skipped":0}\n'];
    let i = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (_input: RequestInfo | URL, _init?: RequestInit) =>
          new Response(
            new ReadableStream({
              pull(controller) {
                if (i < lines.length) controller.enqueue(encoder.encode(lines[i++]));
                else controller.close();
              },
            }),
          ),
      ),
    );
    const complete = vi.fn();
    await streamDiscographyDownload(
      'sp1',
      { albums: [], artist_name: 'A', source: null },
      () => {},
      complete,
    );
    expect(complete).toHaveBeenCalledWith({
      total_added: 2,
      total_skipped: 0,
      failed_releases: [],
    });
  });
});

describe('discogFooter combined button', () => {
  it('labels the combined button for download-capable profiles', () => {
    const footer = discogFooter([{ tracks: 10 }, { tracks: 4 }]);
    expect(footer.submitText).toBe('Add 2 to Wishlist');
    expect(footer.bothText).toBe('Wishlist + Watchlist');
    expect(footer.disabled).toBe(false);
  });

  it('labels the combined button for request-only profiles', () => {
    const footer = discogFooter([{ tracks: 10 }], true);
    expect(footer.submitText).toBe('Request 1');
    expect(footer.bothText).toBe('Request 1 + Watch');
  });

  it('disables both buttons with nothing selected', () => {
    const footer = discogFooter([]);
    expect(footer.disabled).toBe(true);
    expect(footer.submitText).toBe('Select releases');
    expect(footer.bothText).toBe('Select releases');
  });
});

describe('defaultFutureReleases', () => {
  it('mirrors the download filters one time: exclude-now means exclude-later', () => {
    const future = defaultFutureReleases({
      ...DISCOG_DEFAULT_FILTERS,
      live: false,
      compilations: false,
    });
    expect(future).toEqual({
      include_albums: true,
      include_eps: true,
      include_singles: true,
      include_live: false,
      include_remixes: false,
      include_acoustic: false,
      include_compilations: false,
      include_instrumentals: false,
      auto_download_pref: null,
    });
  });

  it('maps category filters onto the release-type includes', () => {
    const future = defaultFutureReleases({ ...DISCOG_DEFAULT_FILTERS, ep: false, single: false });
    expect(future.include_albums).toBe(true);
    expect(future.include_eps).toBe(false);
    expect(future.include_singles).toBe(false);
  });
});

describe('watchArtistWithSettings', () => {
  const json = (body: unknown) => new Response(JSON.stringify(body));

  it('adds then configures when not already watching', async () => {
    const calls: { url: string; body: unknown }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const body = init?.body ? JSON.parse(String(init.body)) : null;
        calls.push({ url, body });
        if (url.endsWith('/api/watchlist/check'))
          return json({ success: true, is_watching: false });
        if (url.endsWith('/api/watchlist/add')) return json({ success: true, message: 'Added.' });
        return json({ success: true });
      }),
    );
    const { watching, message } = await watchArtistWithSettings('sp1', 'Artist', {
      ...WATCHLIST_ADD_DEFAULTS_FOR_TEST,
    });
    expect(watching).toBe(true);
    expect(message).toBe('Added.');
    expect(calls.map((c) => c.url)).toEqual([
      '/api/watchlist/check',
      '/api/watchlist/add',
      '/api/watchlist/artist/sp1/config',
    ]);
    const configBody = calls[2].body as Record<string, unknown>;
    expect(configBody.include_albums).toBe(true);
    expect(configBody.include_live).toBe(false);
    expect(configBody.auto_download_pref).toBeNull();
  });

  it('skips the add when already watching, still applies settings', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url.endsWith('/api/watchlist/check')) return json({ success: true, is_watching: true });
        return json({ success: true });
      }),
    );
    const { watching } = await watchArtistWithSettings('sp1', 'Artist', {
      ...WATCHLIST_ADD_DEFAULTS_FOR_TEST,
      include_remixes: true,
    });
    expect(watching).toBe(true);
    expect(calls).toEqual(['/api/watchlist/check', '/api/watchlist/artist/sp1/config']);
  });

  it('throws when the check fails, before any write', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        calls.push(String(input));
        return json({ success: false, error: 'nope' });
      }),
    );
    await expect(
      watchArtistWithSettings('sp1', 'Artist', WATCHLIST_ADD_DEFAULTS_FOR_TEST),
    ).rejects.toThrow('nope');
    expect(calls).toEqual(['/api/watchlist/check']);
  });
});

describe('#1450 edition_preferred pre-checks', () => {
  const base: DiscogRelease = { id: 'n1', name: 'Nevermind', _type: 'album' };

  it('pre-checks only the preferred edition; others stay visible but unchecked', () => {
    const preferred = discogCardView({ ...base, edition_preferred: true }, {});
    const other = discogCardView(
      { ...base, id: 'n2', name: 'Nevermind (Deluxe)', edition_preferred: false },
      {},
    );
    expect(preferred.checkedByDefault).toBe(true);
    expect(other.checkedByDefault).toBe(false);
  });

  it('an owned preferred edition is still not pre-checked', () => {
    const view = discogCardView(
      { ...base, edition_preferred: true },
      { albums: [{ id: 'n1', status: 'completed' }] },
    );
    expect(view.checkedByDefault).toBe(false);
  });

  it('a missing flag reads as preferred (gap-fill cards, stale responses)', () => {
    const view = discogCardView({ ...base, edition_preferred: undefined }, {});
    expect(view.checkedByDefault).toBe(true);
  });

  it('releasesFromPageDiscography carries the backend flag through', () => {
    const releases = releasesFromPageDiscography({
      albums: [
        { id: 'n1', name: 'Nevermind', edition_preferred: true },
        { id: 'n2', name: 'Nevermind (Deluxe)', edition_preferred: false },
      ],
    } as never);
    expect(releases.map((r) => r.edition_preferred)).toEqual([true, false]);
  });

  it('the supersede list unchecks the base card the gap edition overturned', () => {
    // #1450 finding 2: under one_complete the base response stamped the
    // standard true, then the combined group picked the gap deluxe. The page
    // threads edition_superseded into the modal; the base card must flip off.
    const releases = releasesFromPageDiscography(
      {
        albums: [
          { id: 'b1', name: 'Album', edition_preferred: true },
          { id: 'g1', name: 'Album (Deluxe)', edition_preferred: true, _gap_source: 'deezer' },
          { id: 'n1', name: 'Nevermind', edition_preferred: true },
        ],
      } as never,
      new Set(['b1']),
    );
    const byId = Object.fromEntries(releases.map((r) => [r.id, r]));
    expect(byId.b1.edition_preferred).toBe(false);
    expect(discogCardView(byId.b1, {}).checkedByDefault).toBe(false);
    // the winning gap card and unrelated cards are untouched
    expect(discogCardView(byId.g1, {}).checkedByDefault).toBe(true);
    expect(discogCardView(byId.n1, {}).checkedByDefault).toBe(true);
  });

  it('a superseded owned card stays unchecked, like any owned card', () => {
    const [release] = releasesFromPageDiscography(
      { albums: [{ id: 'b1', name: 'Album', edition_preferred: true }] } as never,
      new Set(['b1']),
    );
    const view = discogCardView(release, { albums: [{ id: 'b1', status: 'completed' }] });
    expect(view.checkedByDefault).toBe(false);
  });
});
