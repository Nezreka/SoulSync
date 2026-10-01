import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscogRelease } from './-artist-detail.discography-modal';

import {
  buildDiscographyPayload,
  DISCOG_DEFAULT_FILTERS,
  discogCardView,
  discogCardVisible,
  discogFooter,
  discogItemStatus,
  loadDiscographyForModal,
  releasesFromPageDiscography,
  streamDiscographyDownload,
} from './-artist-detail.discography-modal';

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
      disabled: false,
    });
    expect(discogFooter([])).toEqual({
      info: '0 releases · 0 tracks',
      submitText: 'Select releases',
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
    expect(complete).toHaveBeenCalledWith({ total_added: 3, total_skipped: 1 });
  });
});
