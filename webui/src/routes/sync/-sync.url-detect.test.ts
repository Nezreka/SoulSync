/**
 * URL detection — the thing that makes four paste-a-link tabs unnecessary.
 *
 * The cases that matter most are the ones that used to produce a WRONG error:
 * a good link pasted on the wrong tab, and a Deezer share link, which the page
 * called invalid when it came from Deezer's own share button.
 */

import { describe, expect, it } from 'vitest';

import type { DetectedSource } from './-sync.url-detect';

import { SYNC_TABS } from './-sync.shell';
import {
  DETECTED_SOURCE_SERVICE_LABELS,
  DETECTED_SOURCE_TAB_IDS,
  DETECTED_SOURCE_TAB_LABELS,
  detectPlaylistUrl,
  isDetected,
  wrongTabError,
} from './-sync.url-detect';
import { deezerInputResult, spotifyPublicUrlError } from './-sync.url-tabs';
import { extractDeezerPlaylistId, extractITunesLinkId, extractSpotifyPublicId } from './-sync.urls';
import { SYNC_VERTICAL_IDS } from './-sync.verticals';

describe('detectPlaylistUrl — the happy routes', () => {
  it('routes a Spotify playlist, carrying the id and kind', () => {
    const d = detectPlaylistUrl('https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M');
    expect(d).toEqual({
      source: 'spotify_public',
      url: 'https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M',
      id: '37i9dQZF1DXcBWIGoYBM5M',
      kind: 'playlist',
    });
  });

  it('routes a Spotify ALBUM, keeping the kind distinct from a playlist', () => {
    const d = detectPlaylistUrl('https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3');
    expect(isDetected(d) && d.kind).toBe('album');
  });

  it('routes a Deezer playlist link, including a regional locale segment', () => {
    expect(detectPlaylistUrl('https://www.deezer.com/en-us/playlist/1234567')).toMatchObject({
      source: 'deezer',
      id: '1234567',
    });
    expect(detectPlaylistUrl('https://www.deezer.com/playlist/908622995')).toMatchObject({
      source: 'deezer',
      id: '908622995',
    });
  });

  it('routes an Apple Music album and a bare applemusic: URI', () => {
    expect(detectPlaylistUrl('https://music.apple.com/us/album/blonde/1146195596')).toMatchObject({
      source: 'itunes_link',
      id: '1146195596',
      kind: 'album',
    });
    expect(detectPlaylistUrl('applemusic:album:1146195596')).toMatchObject({
      source: 'itunes_link',
      kind: 'album',
    });
  });

  it('routes both YouTube playlist hosts', () => {
    expect(detectPlaylistUrl('https://www.youtube.com/playlist?list=PLabc').source).toBe('youtube');
    expect(detectPlaylistUrl('https://music.youtube.com/playlist?list=PLabc').source).toBe(
      'youtube',
    );
  });

  it('tolerates surrounding whitespace, which pasting routinely adds', () => {
    expect(detectPlaylistUrl('  https://open.spotify.com/playlist/abc123  ').source).toBe(
      'spotify_public',
    );
  });
});

describe('detectPlaylistUrl — the errors that used to be wrong', () => {
  it('names the fix for a Deezer SHARE link instead of calling it invalid', () => {
    // This is what Deezer's own Share button copies. Telling the user it is
    // invalid reads as the feature being broken.
    for (const url of ['https://link.deezer.com/s/30abcdef', 'https://deezer.page.link/xyz123']) {
      const d = detectPlaylistUrl(url);
      expect(d.source).toBeNull();
      expect(isDetected(d) ? '' : d.error).toContain('share link');
    }
  });

  it('says a Deezer link is missing its id, rather than "not recognised"', () => {
    const d = detectPlaylistUrl('https://www.deezer.com/us/artist/12345');
    expect(d.source).toBeNull();
    expect(isDetected(d) ? '' : d.error).toContain('deezer.com/playlist');
  });

  it('spots a YouTube link that is not a playlist — the commonest near-miss', () => {
    const d = detectPlaylistUrl('https://www.youtube.com/watch?v=dQw4w9WgXcQ');
    expect(d.source).toBeNull();
    expect(isDetected(d) ? '' : d.error).toContain('not a playlist');
  });

  it('does NOT treat a bare number as Deezer, even though the extractor accepts one', () => {
    // extractDeezerPlaylistId('12345') returns an id, but a lone number is an
    // unfinished paste — guessing a service from it would be a coin flip.
    expect(extractDeezerPlaylistId('12345')).toBe('12345');
    expect(detectPlaylistUrl('12345').source).toBeNull();
  });

  it('asks for a link when the field is empty or whitespace', () => {
    for (const raw of ['', '   ']) {
      const d = detectPlaylistUrl(raw);
      expect(d.source).toBeNull();
      expect(isDetected(d) ? '' : d.error).toContain('Paste a playlist link');
    }
  });

  it('lists what it accepts for anything else, and never says just "invalid"', () => {
    const d = detectPlaylistUrl('https://tidal.com/browse/playlist/abc');
    expect(d.source).toBeNull();
    const error = isDetected(d) ? '' : d.error;
    expect(error).toContain('Spotify');
    expect(error).toContain('Deezer');
    expect(error.toLowerCase()).not.toMatch(/^invalid/);
  });
});

describe('detection never disagrees with the parse that follows it', () => {
  /*
   * The whole safety argument for one input: detection delegates to the SAME
   * extractors the per-source parse uses, so a URL that detects as source X
   * cannot then fail X's own parse.
   */
  it('every id it reports is the id that source’s extractor produces', () => {
    const spotify = 'https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M';
    const d1 = detectPlaylistUrl(spotify);
    expect(isDetected(d1) && d1.id).toBe(extractSpotifyPublicId(spotify)?.id);

    const deezer = 'https://www.deezer.com/en/playlist/908622995';
    const d2 = detectPlaylistUrl(deezer);
    expect(isDetected(d2) && d2.id).toBe(extractDeezerPlaylistId(deezer));

    const itunes = 'https://music.apple.com/us/album/blonde/1146195596';
    const d3 = detectPlaylistUrl(itunes);
    expect(isDetected(d3) && d3.id).toBe(extractITunesLinkId(itunes)?.id);
  });

  it('every source it can return is a REAL registered vertical', () => {
    // The seam that matters. Checked against the registry itself rather than
    // against strings I typed here — my first draft returned 'deezer_link',
    // which is not a vertical at all: the Deezer LINK tab owns `deezer`, and
    // the Deezer-ARL account tab is engine-driven with no vertical.
    const routed = [
      'https://open.spotify.com/playlist/a1',
      'https://music.apple.com/us/album/x/1',
      'https://www.deezer.com/playlist/1',
      'https://www.youtube.com/playlist?list=P1',
    ].map((u) => detectPlaylistUrl(u).source);

    expect(routed).toEqual(['spotify_public', 'itunes_link', 'deezer', 'youtube']);
    for (const source of routed) {
      expect(SYNC_VERTICAL_IDS).toContain(source);
    }
  });
});

describe('wrongTabError — a good link on the wrong tab', () => {
  it('names the tab that wants it, for every cross pairing', () => {
    expect(wrongTabError('https://www.deezer.com/playlist/1', 'spotify_public')).toContain(
      'Deezer Link',
    );
    expect(wrongTabError('https://open.spotify.com/playlist/a1', 'deezer')).toContain(
      'Spotify Link',
    );
    expect(wrongTabError('https://www.youtube.com/playlist?list=P1', 'itunes_link')).toContain(
      'YouTube',
    );
    expect(wrongTabError('https://music.apple.com/us/album/x/1', 'youtube')).toContain(
      'iTunes Link',
    );
  });

  it('says nothing when the link IS for this tab', () => {
    expect(wrongTabError('https://open.spotify.com/playlist/a1', 'spotify_public')).toBeNull();
    expect(wrongTabError('https://www.deezer.com/playlist/1', 'deezer')).toBeNull();
  });

  it('says nothing for an unrecognised link, so the tab keeps its own message', () => {
    // This only ever REPLACES a wrong answer; it must never suppress a right one.
    expect(wrongTabError('https://tidal.com/browse/playlist/abc', 'deezer')).toBeNull();
    expect(wrongTabError('', 'deezer')).toBeNull();
    expect(wrongTabError('nonsense', 'youtube')).toBeNull();
  });

  it('leaves the Deezer share-link message alone — it is more specific', () => {
    // deezerInputResult checks the share link FIRST, so its actionable message
    // wins over the generic cross-tab one.
    const result = deezerInputResult('https://link.deezer.com/s/30abcdef');
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.error).toContain('share link');
  });

  it('a Deezer link on the Spotify tab no longer reads as invalid Spotify input', () => {
    // The exact reported confusion, from the other side.
    const message = spotifyPublicUrlError('https://www.deezer.com/us/playlist/908622995');
    expect(message).toContain('Deezer Link');
    expect(message).not.toContain('valid Spotify');
  });
});

describe('the label maps', () => {
  it('covers every source a detection can return, in both maps', () => {
    // A missing entry would render "undefined" into a user-facing sentence.
    const sources: DetectedSource[] = ['spotify_public', 'itunes_link', 'deezer', 'youtube'];
    for (const s of sources) {
      expect(DETECTED_SOURCE_TAB_LABELS[s]).toBeTruthy();
      expect(DETECTED_SOURCE_SERVICE_LABELS[s]).toBeTruthy();
    }
  });

  it('keeps the SERVICE name distinct from the TAB name where they differ', () => {
    // Reusing one label produced "That is a Deezer Link link".
    expect(DETECTED_SOURCE_SERVICE_LABELS.deezer).toBe('Deezer');
    expect(DETECTED_SOURCE_TAB_LABELS.deezer).toBe('Deezer Link');
    expect(DETECTED_SOURCE_SERVICE_LABELS.itunes_link).toBe('Apple Music');
    expect(DETECTED_SOURCE_TAB_LABELS.itunes_link).toBe('iTunes Link');
  });
});

describe('DETECTED_SOURCE_TAB_IDS', () => {
  it('every routed tab id is a REAL tab', () => {
    // Routing to a tab that does not exist is a silent no-op, so this is
    // pinned against SYNC_TABS rather than against strings typed here.
    const ids = new Set(SYNC_TABS.map((t) => t.id));
    for (const tab of Object.values(DETECTED_SOURCE_TAB_IDS)) {
      expect(ids).toContain(tab);
    }
  });

  it('covers every source a detection can return', () => {
    const sources: DetectedSource[] = ['spotify_public', 'itunes_link', 'deezer', 'youtube'];
    for (const s of sources) {
      expect(DETECTED_SOURCE_TAB_IDS[s]).toBeTruthy();
    }
  });

  it('sends Deezer to the LINK tab, not to the account tab of the same service', () => {
    // The vertical is `deezer`; the paste-a-link tab is `deezer-link`. The
    // account tab called `deezer` is the ARL list — a different thing entirely.
    expect(DETECTED_SOURCE_TAB_IDS.deezer).toBe('deezer-link');
  });
});
