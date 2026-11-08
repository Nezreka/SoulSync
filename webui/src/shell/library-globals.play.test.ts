import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { playLibraryTrack } from './library-globals';

/**
 * playLibraryTrack's metadata refresh, and the opt-out that makes auditioning
 * duplicates possible (#1214).
 *
 * The refresh asks /api/stats/resolve-track for the canonical row by TITLE +
 * ARTIST and overwrites file_path with what comes back. That query is LIMIT 1.
 * Two copies of one song share a title and artist, so without an opt-out both
 * would play whichever copy the query happened to return - the Tools duplicate
 * rows would look like they were comparing two files while playing one twice.
 */

const setTrackInfo = vi.fn();
const startAudioPlayback = vi.fn(async () => {});
const fetchMock = vi.fn();

function stubShell() {
  vi.stubGlobal('audioPlayer', null);
  vi.stubGlobal('npRepeatMode', 'off');
  vi.stubGlobal('setTrackInfo', setTrackInfo);
  vi.stubGlobal('showLoadingAnimation', vi.fn());
  vi.stubGlobal('hideLoadingAnimation', vi.fn());
  vi.stubGlobal('startAudioPlayback', startAudioPlayback);
  vi.stubGlobal('startStream', vi.fn());
  vi.stubGlobal('clearTrack', vi.fn());
  vi.stubGlobal('showToast', vi.fn());
  vi.stubGlobal('fetch', fetchMock);
}

/** The canonical row resolve-track hands back for "Song" by "Band". */
function respondJson(url: string) {
  if (url.includes('/api/stats/resolve-track')) {
    return new Response(
      JSON.stringify({ success: true, track: { id: 1, title: 'Song', file_path: '/a/first.mp3' } }),
    );
  }
  return new Response(JSON.stringify({ success: true }));
}

function bodyOf(url: string): Record<string, unknown> {
  const call = fetchMock.mock.calls.find(([u]) => String(u).includes(url));
  expect(call, `expected a POST to ${url}`).toBeDefined();
  const body = (call?.[1] as RequestInit | undefined)?.body;
  return JSON.parse(typeof body === 'string' ? body : '{}') as Record<string, unknown>;
}

beforeEach(() => {
  fetchMock.mockReset().mockImplementation(async (url: string) => respondJson(String(url)));
  setTrackInfo.mockReset();
  startAudioPlayback.mockReset();
  stubShell();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('playLibraryTrack', () => {
  it('plays the exact file and skips the re-resolve when exact_path is set', async () => {
    await playLibraryTrack(
      { id: 2, title: 'Song', file_path: '/a/second.flac', exact_path: true },
      'LP',
      'Band',
    );
    const resolved = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes('/api/stats/resolve-track'),
    );
    expect(resolved).toHaveLength(0);
    // The second copy is what plays - not the row resolve-track would have won.
    expect(bodyOf('/api/library/play')).toMatchObject({ file_path: '/a/second.flac', track_id: 2 });
    expect(startAudioPlayback).toHaveBeenCalled();
  });

  it('still refreshes from the DB for an ordinary play', async () => {
    // The negative control: the refresh is what makes a stale caller-supplied
    // path play the right file, so exact_path must not have disabled it wholesale.
    await playLibraryTrack({ id: 1, title: 'Song', file_path: '/a/stale.mp3' }, 'LP', 'Band');
    expect(bodyOf('/api/stats/resolve-track')).toEqual({ title: 'Song', artist: 'Band' });
    expect(bodyOf('/api/library/play')).toMatchObject({ file_path: '/a/first.mp3' });
  });

  it('refuses a track with no file at all', async () => {
    await playLibraryTrack({ id: 3, title: 'Song', exact_path: true }, 'LP', 'Band');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('stops at a kids-limits refusal instead of falling back to a stream', async () => {
    // a hide_explicit profile asking for an explicit track gets 403 restricted.
    // the shared fetch hook toasts it; a streaming fallback would be refused
    // too and add a second, confusing error
    fetchMock.mockImplementation(async (url: string) =>
      String(url).includes('/api/library/play')
        ? new Response(JSON.stringify({ success: false, error: 'restricted', restricted: true }), {
            status: 403,
          })
        : respondJson(String(url)),
    );
    await playLibraryTrack({ id: 1, title: 'Song', file_path: '/a/first.mp3' }, 'LP', 'Band');
    const streamed = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes('/api/enhanced-search/stream-track'),
    );
    expect(streamed).toHaveLength(0);
    expect(startAudioPlayback).not.toHaveBeenCalled();
    expect(window.showToast).not.toHaveBeenCalled();
  });

  it('keeps the v2 ids the refresh hands back, so Now Playing can report the track', async () => {
    // resolve-track answers with lib2 rows. the refresh copied id, path and
    // album, and dropped lib2_track_id: the player then had no library row to
    // point a report at and hid Report issue for every title+artist play
    fetchMock.mockImplementation(async (url: string) =>
      String(url).includes('/api/stats/resolve-track')
        ? new Response(
            JSON.stringify({
              success: true,
              track: {
                id: 41,
                title: 'Song',
                file_path: '/a/first.mp3',
                lib2_track_id: 41,
                lib2_artist_id: 7,
              },
            }),
          )
        : respondJson(String(url)),
    );
    await playLibraryTrack({ id: 1, title: 'Song', file_path: '/a/stale.mp3' }, 'LP', 'Band');
    expect(setTrackInfo).toHaveBeenCalledWith(
      expect.objectContaining({ is_library: true, lib2_track_id: 41, lib2_artist_id: 7 }),
    );
  });

  it('plays the clicked copy of a migrated v2 row, not a title+artist twin', async () => {
    // review 5 R5-L-1: a 3.6.1-migrated row carries legacy_track_id and
    // lib2_track_id. the name lookup answered the album's copy, so Play on the
    // single played the album file and Now Playing named the album
    fetchMock.mockImplementation(async (url: string) =>
      String(url).includes('/api/stats/resolve-track')
        ? new Response(
            JSON.stringify({
              success: true,
              track: {
                id: 1,
                title: 'Royals',
                file_path: '/music/Lorde/Pure Heroine/03 Royals.flac',
                lib2_track_id: 1,
                album_title: 'Pure Heroine',
              },
            }),
          )
        : respondJson(String(url)),
    );
    await playLibraryTrack(
      {
        id: 502,
        lib2_track_id: 2,
        legacy_track_id: 502,
        title: 'Royals',
        file_path: '/music/Lorde/Royals - Single/01 Royals.mp3',
        _stats_image: '/api/library/v2/albums/2/image',
      },
      'Royals - Single',
      'Lorde',
    );
    const resolved = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes('/api/stats/resolve-track'),
    );
    expect(resolved).toHaveLength(0);
    expect(bodyOf('/api/library/play')).toMatchObject({
      file_path: '/music/Lorde/Royals - Single/01 Royals.mp3',
      album: 'Royals - Single',
    });
    expect(setTrackInfo).toHaveBeenCalledWith(
      expect.objectContaining({
        lib2_track_id: 2,
        album: 'Royals - Single',
        image_url: '/api/library/v2/albums/2/image',
      }),
    );
  });
});
