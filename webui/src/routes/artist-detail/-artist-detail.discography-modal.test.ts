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
  streamDiscographyDownload,
} from './-artist-detail.discography-modal';

/**
 * The Download Discography layer: metadata-id resolution, gap-fill merge
 * (#1067), the #877 filter gate, Deluxe-first payload ordering, and the #830
 * honest per-album status.
 */

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.removeItem('discog_gapfill');
});

describe('loadDiscographyForModal', () => {
  it('resolves the metadata id first and fetches through it', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        calls.push(url);
        if (url.includes('/enhanced')) {
          return new Response(
            JSON.stringify({ success: true, artist: { spotify_artist_id: 'sp1' } }),
          );
        }
        return new Response(
          JSON.stringify({
            albums: [{ id: 'a1', name: 'SAW' }],
            eps: [{ id: 'e1', name: 'On' }],
            singles: [],
            source: 'spotify',
          }),
        );
      }),
    );
    const data = await loadDiscographyForModal(42, 'Aphex Twin');
    expect(calls[0]).toBe('/api/library/artist/42/enhanced');
    expect(calls[1]).toBe('/api/artist/sp1/discography?artist_name=Aphex%20Twin');
    expect(data?.artist).toEqual({ id: 'sp1', name: 'Aphex Twin', source: 'spotify' });
    expect(data?.releases.map((r) => r._type)).toEqual(['album', 'ep']);
  });

  it('merges gap-fill releases when enabled, deduped against the base list', async () => {
    localStorage.setItem('discog_gapfill', '1');
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.includes('/enhanced')) return new Response(JSON.stringify({ success: false }));
        if (url.includes('gap-fill')) {
          return new Response(
            JSON.stringify({
              success: true,
              gaps: {
                albums: [
                  // Duplicate of the base release — must be dropped.
                  { id: 'g0', title: 'SAW', release_date: '1992-11-09', gap_source: 'deezer' },
                  { id: 'g1', title: 'Druqks', release_date: '2001-10-22', gap_source: 'deezer' },
                ],
              },
            }),
          );
        }
        return new Response(
          JSON.stringify({
            albums: [{ id: 'a1', name: 'SAW', release_date: '1992-11-09' }],
            eps: [],
            singles: [],
            source: 'spotify',
          }),
        );
      }),
    );
    const data = await loadDiscographyForModal(42, 'Aphex Twin');
    expect(data?.releases.map((r) => r.name)).toEqual(['SAW', 'Druqks']);
    expect(data?.releases[1]._gap_source).toBe('deezer');
  });

  it('an empty discography resolves to null', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (_input: RequestInfo | URL, _init?: RequestInit) =>
          new Response(JSON.stringify({ albums: [], eps: [], singles: [] })),
      ),
    );
    expect(await loadDiscographyForModal(42, 'X')).toBeNull();
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
