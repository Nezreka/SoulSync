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
});
