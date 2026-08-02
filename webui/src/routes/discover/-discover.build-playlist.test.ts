import { describe, expect, it } from 'vitest';

import type { SeedArtist } from './-discover.build-playlist';

import {
  BP_ALL_SELECTED,
  BP_ALREADY_SELECTED,
  BP_ARTIST_PLACEHOLDER,
  BP_DOWNLOAD_PLAYLIST_ID,
  BP_GENERATE_FAILED,
  BP_MAX_REACHED,
  BP_MAX_SEEDS,
  BP_NEED_ONE,
  BP_NO_PLAYLIST_TRACKS,
  BP_NO_TRACKS,
  BP_PLAYLIST_SIZE,
  BP_RESULT_TITLE,
  BP_SEARCH_DEBOUNCE_MS,
  bpAddArtist,
  bpArtistImage,
  bpDownloadName,
  bpGenerateBody,
  bpGenerateError,
  bpMetaStats,
  bpNoResultsMessage,
  bpQueryIsEmpty,
  bpRemoveArtist,
  bpResultSubtitle,
  bpSearchOutcome,
  bpSearchUrl,
  bpSelectionState,
} from './-discover.build-playlist';

const a = (id: string, name = id): SeedArtist => ({ id, name });

describe('the search box', () => {
  it('debounces at 400ms', () => {
    expect(BP_SEARCH_DEBOUNCE_MS).toBe(400);
  });

  it('short-circuits an empty query BEFORE the debounce', () => {
    // Backspacing to empty hides the list immediately and fires no request,
    // rather than leaving a stale list up for 400ms.
    expect(bpQueryIsEmpty('')).toBe(true);
    expect(bpQueryIsEmpty('   ')).toBe(true);
    expect(bpQueryIsEmpty(' x ')).toBe(false);
  });

  it('encodes the query', () => {
    expect(bpSearchUrl('a & b')).toBe(
      '/api/discover/build-playlist/search-artists?query=a%20%26%20b',
    );
  });
});

describe('the search outcome', () => {
  it('filters out already-selected artists', () => {
    const out = bpSearchOutcome({ success: true, artists: [a('1'), a('2')] }, 'q', [a('1')]);
    expect(out.kind).toBe('results');
    expect(out.kind === 'results' && out.artists.map((x) => x.id)).toEqual(['2']);
  });

  it('distinguishes "all selected" from "no results"', () => {
    // Saying "No artists found" when everything matched but was already picked
    // reads as a failed search.
    const out = bpSearchOutcome({ success: true, artists: [a('1')] }, 'q', [a('1')]);
    expect(out).toEqual({ kind: 'all-selected', message: BP_ALL_SELECTED });
  });

  it('reports a genuinely empty search with the query in it', () => {
    const out = bpSearchOutcome({ success: true, artists: [] }, 'aphex', []);
    expect(out).toEqual({ kind: 'none', message: 'No artists found for "aphex"' });
    expect(bpNoResultsMessage('x')).toBe('No artists found for "x"');
  });

  it('treats an unsuccessful response as empty', () => {
    expect(bpSearchOutcome({ success: false, artists: [a('1')] }, 'q', []).kind).toBe('none');
    expect(bpSearchOutcome(null, 'q', []).kind).toBe('none');
  });
});

describe('picking seeds', () => {
  it('adds an artist', () => {
    const r = bpAddArtist([], a('1', 'Aphex Twin'));
    expect(r.added).toBe(true);
    expect(r.added && r.selected.map((x) => x.name)).toEqual(['Aphex Twin']);
  });

  it('refuses a duplicate', () => {
    expect(bpAddArtist([a('1')], a('1'))).toEqual({
      added: false,
      warning: BP_ALREADY_SELECTED,
    });
  });

  it('caps at five', () => {
    const five = ['1', '2', '3', '4', '5'].map((i) => a(i));
    expect(bpAddArtist(five, a('6'))).toEqual({ added: false, warning: BP_MAX_REACHED });
    expect(BP_MAX_SEEDS).toBe(5);
  });

  it('checks DUPLICATE before the cap, so the message is the accurate one', () => {
    // Re-adding an already-picked artist while at five must say "already
    // selected", not "maximum reached".
    const five = ['1', '2', '3', '4', '5'].map((i) => a(i));
    expect(bpAddArtist(five, a('3'))).toEqual({ added: false, warning: BP_ALREADY_SELECTED });
  });

  it('removes by id', () => {
    expect(bpRemoveArtist([a('1'), a('2')], '1').map((x) => x.id)).toEqual(['2']);
  });

  it('does not mutate the previous selection', () => {
    const before = [a('1')];
    bpAddArtist(before, a('2'));
    expect(before).toHaveLength(1);
  });

  it('carries the count and disables generate at zero', () => {
    expect(bpSelectionState([])).toEqual({
      count: 0,
      counterLabel: '0 / 5',
      generateDisabled: true,
      showEmptyHint: true,
    });
    expect(bpSelectionState([a('1')])).toEqual({
      count: 1,
      counterLabel: '1 / 5',
      generateDisabled: false,
      showEmptyHint: false,
    });
  });

  it('falls back to the placeholder image', () => {
    expect(bpArtistImage(a('1'))).toBe(BP_ARTIST_PLACEHOLDER);
    expect(bpArtistImage({ id: '1', name: 'n', image_url: '/x.jpg' })).toBe('/x.jpg');
  });

  it('keeps the copy', () => {
    expect(BP_NEED_ONE).toBe('Please select at least 1 artist');
    expect(BP_MAX_REACHED).toBe('Maximum 5 seed artists');
  });
});

describe('generating', () => {
  it('sends the seed ids and a FIXED size of 50', () => {
    expect(bpGenerateBody([a('1'), a('2')])).toEqual({
      seed_artist_ids: ['1', '2'],
      playlist_size: 50,
    });
    expect(BP_PLAYLIST_SIZE).toBe(50);
  });

  it('reads data.error when the REQUEST failed', () => {
    expect(bpGenerateError(false, { error: 'upstream down' })).toBe('upstream down');
    expect(bpGenerateError(true, { success: false })).toBe(BP_GENERATE_FAILED);
  });

  it('reads data.playlist.error when the request SUCCEEDED but found nothing', () => {
    // Two different fallbacks on purpose — the generator explains why it found
    // nothing, and flattening these loses that.
    expect(
      bpGenerateError(true, {
        success: true,
        playlist: { tracks: [], error: 'seeds too obscure' },
      }),
    ).toBe('seeds too obscure');
    expect(bpGenerateError(true, { success: true, playlist: { tracks: [] } })).toBe(BP_NO_TRACKS);
    expect(bpGenerateError(true, { success: true })).toBe(BP_NO_TRACKS);
  });

  it('returns null when there are tracks', () => {
    expect(bpGenerateError(true, { success: true, playlist: { tracks: [{}] } })).toBeNull();
  });

  it('titles the result and lists the seeds with COMMAS', () => {
    expect(BP_RESULT_TITLE).toBe('Custom Playlist');
    expect(bpResultSubtitle([a('1', 'A'), a('2', 'B')])).toBe('Based on: A, B');
  });

  it('shows the three stat tiles in order', () => {
    expect(bpMetaStats({ total_tracks: 50, similar_artists_count: 12, albums_count: 30 })).toEqual([
      { value: 50, label: 'Tracks' },
      { value: 12, label: 'Similar Artists' },
      { value: 30, label: 'Albums Sampled' },
    ]);
  });

  it('shows zeros rather than undefined for missing metadata', () => {
    expect(bpMetaStats(undefined).map((s) => s.value)).toEqual([0, 0, 0]);
  });
});

describe('downloading the result', () => {
  it('uses a virtual id that does NOT follow the sync convention', () => {
    // The sync path builds discover_${type}; this one is build_playlist_custom.
    // Two ids for the same playlist through two systems — unifying them breaks
    // whichever caller is not updated.
    expect(BP_DOWNLOAD_PLAYLIST_ID).toBe('build_playlist_custom');
    expect(BP_DOWNLOAD_PLAYLIST_ID).not.toBe('discover_build_playlist');
  });

  it('names it with a HYPHEN, where the subtitle uses a colon', () => {
    expect(bpDownloadName([a('1', 'A'), a('2', 'B')])).toBe('Custom Playlist - A, B');
    expect(bpResultSubtitle([a('1', 'A')])).toContain('Based on:');
  });

  it('keeps the no-tracks warning', () => {
    expect(BP_NO_PLAYLIST_TRACKS).toBe('No playlist tracks available');
  });
});
