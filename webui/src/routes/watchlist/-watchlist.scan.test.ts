import { describe, expect, it } from 'vitest';

import {
  prettyScanPhase,
  scanAlbumLine,
  scanCompletionMessage,
  scanProgressPercent,
  scanProgressText,
} from './-watchlist.scan';

describe('prettyScanPhase', () => {
  it('parses the numeric album phase', () => {
    expect(prettyScanPhase('checking_album_2_of_5')).toBe('Checking album 2 of 5');
    expect(prettyScanPhase('checking_album_11_of_120')).toBe('Checking album 11 of 120');
  });

  it('maps the known phases', () => {
    expect(prettyScanPhase('starting')).toBe('Starting…');
    expect(prettyScanPhase('fetching_discography')).toBe('Fetching releases…');
    expect(prettyScanPhase('scanning_labels')).toBe('Scanning record labels…');
    expect(prettyScanPhase('populating_discovery_pool')).toBe('Populating discovery…');
    expect(prettyScanPhase('updating_listenbrainz')).toBe('Updating ListenBrainz…');
  });

  it('falls back to a de-underscored phase for anything unknown', () => {
    // A new server-side phase must read as words, not as a raw key.
    expect(prettyScanPhase('doing_something_new')).toBe('doing something new');
  });

  it('says "Working…" when there is no phase yet', () => {
    expect(prettyScanPhase('')).toBe('Working…');
    expect(prettyScanPhase(null)).toBe('Working…');
    expect(prettyScanPhase(undefined)).toBe('Working…');
  });
});

describe('scanProgressText / scanProgressPercent', () => {
  it('counts from one, not zero', () => {
    // current_artist_index is 0-based; the first artist reads "1 / 40".
    expect(scanProgressText({ current_artist_index: 0, total_artists: 40 })).toBe('1 / 40 artists');
    // 12, not 3: the artist loop now occupies 10-100 because the source-matching
    // phase that runs before it owns the first ten. Without that split the bar
    // jumped BACKWARDS - matching finishing at 10%, then artist 1 restarting at
    // 3% - which looks more broken than starting slightly high. A scan with
    // nothing to match skips the phase entirely and simply opens at 10.
    expect(scanProgressPercent({ current_artist_index: 0, total_artists: 40 })).toBe(12);
  });

  it('never exceeds the total', () => {
    // The index can run past the end as the last artist finishes.
    expect(scanProgressText({ current_artist_index: 40, total_artists: 40 })).toBe(
      '40 / 40 artists',
    );
    expect(scanProgressPercent({ current_artist_index: 99, total_artists: 40 })).toBe(100);
  });

  it('is blank and zero before the total is known', () => {
    expect(scanProgressText({})).toBe('');
    expect(scanProgressPercent({})).toBe(0);
    expect(scanProgressText({ total_artists: 0 })).toBe('');
  });
});

describe('scanAlbumLine', () => {
  it('shows the album when there is one', () => {
    expect(scanAlbumLine({ current_album: 'Syro' })).toBe('Syro');
  });

  it('explains what it is doing instead of going blank', () => {
    expect(scanAlbumLine({ current_phase: 'fetching_discography' })).toBe('Fetching releases…');
    expect(scanAlbumLine({})).toBe('Looking for new releases…');
    expect(scanAlbumLine({ current_phase: 'checking_album_1_of_3' })).toBe(
      'Looking for new releases…',
    );
  });
});

describe('scanCompletionMessage', () => {
  it('reports found and added when there were new tracks', () => {
    expect(
      scanCompletionMessage({
        total_artists: 40,
        successful_scans: 38,
        new_tracks_found: 19,
        tracks_added_to_wishlist: 10,
      }),
    ).toBe('Scan completed: 38/40 artists scanned, found 19 new tracks, added 10 to wishlist');
  });

  it('omits the added clause when nothing was added', () => {
    expect(
      scanCompletionMessage({
        total_artists: 5,
        successful_scans: 5,
        new_tracks_found: 3,
        tracks_added_to_wishlist: 0,
      }),
    ).toBe('Scan completed: 5/5 artists scanned, found 3 new tracks');
  });

  it('singularises one track', () => {
    expect(
      scanCompletionMessage({
        total_artists: 1,
        successful_scans: 1,
        new_tracks_found: 1,
        tracks_added_to_wishlist: 1,
      }),
    ).toBe('Scan completed: 1/1 artists scanned, found 1 new track, added 1 to wishlist');
  });

  it('says so plainly when nothing was found', () => {
    expect(
      scanCompletionMessage({ total_artists: 12, successful_scans: 12, new_tracks_found: 0 }),
    ).toBe('Scan completed: 12/12 artists scanned, no new tracks found');
  });

  it('treats an empty summary as a zero scan rather than crashing', () => {
    expect(scanCompletionMessage({})).toBe(
      'Scan completed: 0/0 artists scanned, no new tracks found',
    );
  });
});

describe('the source-matching phase', () => {
  // Every artist is looked up on every other metadata provider BEFORE the
  // artist loop starts. For a few hundred artists that is the longest part of a
  // scan, and nothing about the artist counter moves during it — so the page
  // read "0 / 379 artists" the whole time and the scan looked hung (#1240).
  const matching = (done: number, total = 379, source = 'deezer') => ({
    current_phase: 'matching_sources',
    matching_source: source,
    matching_artists_done: done,
    matching_artists_total: total,
    current_artist_index: 0,
    total_artists: 379,
  });

  it('counts the artists being matched, not the ones being scanned', () => {
    expect(scanProgressText(matching(120))).toBe('Matching 120 / 379 artists to deezer');
  });

  it('does not fall back to the frozen artist counter', () => {
    expect(scanProgressText(matching(120))).not.toContain('1 / 379 artists');
  });

  it('names the phase readably', () => {
    expect(prettyScanPhase('matching_sources')).toBe('Matching artists to sources…');
  });

  it('drops the source when it is not known yet', () => {
    expect(scanProgressText(matching(5, 379, ''))).toBe('Matching 5 / 379 artists');
  });

  it('moves the bar while matching', () => {
    expect(scanProgressPercent(matching(0))).toBe(0);
    expect(scanProgressPercent(matching(379))).toBe(10);
  });

  it('never sends the bar backwards when the artist loop starts', () => {
    // matching finishes at 10; artist 1 of 379 must not restart below it
    const matchingDone = scanProgressPercent(matching(379));
    const firstArtist = scanProgressPercent({ current_artist_index: 0, total_artists: 379 });
    expect(firstArtist).toBeGreaterThanOrEqual(matchingDone);
  });

  it('still reaches 100 on the last artist', () => {
    expect(scanProgressPercent({ current_artist_index: 378, total_artists: 379 })).toBe(100);
  });

  it('leaves a normal scan frame alone', () => {
    expect(scanProgressText({ current_artist_index: 2, total_artists: 40 })).toBe('3 / 40 artists');
  });
});
