import { describe, expect, it } from 'vitest';

import type { BasicAlbum, BasicResult, BasicTrack, FilterState } from './-basic.types';

import {
  albumFormatLabel,
  applyFiltersAndSort,
  detectDiscBreaks,
  filterResults,
  formatBitrate,
  formatSize,
  relevanceScore,
  resultBitrate,
  resultDuration,
  resultFormat,
  resultSize,
  resultTitle,
} from './-basic.helpers';
import { DEFAULT_FILTERS } from './-basic.types';

function track(over: Partial<BasicTrack> = {}): BasicTrack {
  return {
    result_type: 'track',
    username: 'peer',
    filename: 'a.flac',
    size: 10_000_000,
    bitrate: 320,
    duration: 200_000,
    quality: 'flac',
    free_upload_slots: 1,
    upload_speed: 1_000_000,
    queue_length: 0,
    sample_rate: 44_100,
    bit_depth: 16,
    artist: 'Aphex Twin',
    title: 'Xtal',
    album: 'Selected Ambient Works',
    track_number: 1,
    quality_score: 0.9,
    ...over,
  };
}

function album(over: Partial<BasicAlbum> = {}): BasicAlbum {
  return {
    result_type: 'album',
    username: 'peer',
    album_path: '/music/saw',
    album_title: 'Selected Ambient Works',
    artist: 'Aphex Twin',
    track_count: 2,
    total_size: 80_000_000,
    tracks: [track(), track({ bitrate: 128, duration: 100_000, track_number: 2 })],
    dominant_quality: 'flac',
    year: '1992',
    free_upload_slots: 1,
    upload_speed: 1_000_000,
    queue_length: 0,
    quality_score: 0.8,
    ...over,
  };
}

const filters = (over: Partial<FilterState> = {}): FilterState => ({ ...DEFAULT_FILTERS, ...over });
const names = (results: BasicResult[]) => results.map(resultTitle);

describe('shape-aware field access', () => {
  // An album is not a track with extra fields. Reading only the track names
  // is what made every album rank zero on Size, Bitrate and Duration.
  it('reads the title from either shape', () => {
    expect(resultTitle(track({ title: 'Xtal' }))).toBe('Xtal');
    expect(resultTitle(album({ album_title: 'SAW' }))).toBe('SAW');
  });

  it('reads an album size from total_size, not the absent size', () => {
    expect(resultSize(track({ size: 500 }))).toBe(500);
    expect(resultSize(album({ total_size: 900 }))).toBe(900);
  });

  it('reads an album format from dominant_quality', () => {
    expect(resultFormat(track({ quality: 'mp3' }))).toBe('mp3');
    expect(resultFormat(album({ dominant_quality: 'flac' }))).toBe('flac');
  });

  it('takes an album bitrate as the best of its tracks', () => {
    // Matches AlbumResult.audio_quality server-side, which uses max().
    expect(
      resultBitrate(album({ tracks: [track({ bitrate: 128 }), track({ bitrate: 900 })] })),
    ).toBe(900);
  });

  it('takes an album duration as the sum of its tracks', () => {
    expect(
      resultDuration(album({ tracks: [track({ duration: 100 }), track({ duration: 250 })] })),
    ).toBe(350);
  });

  it('survives an album with no tracks', () => {
    const empty = album({ tracks: [] });
    expect(resultBitrate(empty)).toBe(0);
    expect(resultDuration(empty)).toBe(0);
  });
});

describe('filterResults', () => {
  it('filters by type', () => {
    const rows = [album(), track()];
    expect(filterResults(rows, filters({ type: 'album' }))).toEqual([rows[0]]);
    expect(filterResults(rows, filters({ type: 'track' }))).toEqual([rows[1]]);
    expect(filterResults(rows, filters({ type: 'all' }))).toHaveLength(2);
  });

  it('filters an album by its dominant quality', () => {
    // The vanilla read `dominant_quality || quality` here but `quality` alone
    // when labelling — the filter worked while the label always said "Mixed".
    const rows = [album({ dominant_quality: 'flac' }), album({ dominant_quality: 'mp3' })];
    expect(filterResults(rows, filters({ format: 'flac' }))).toEqual([rows[0]]);
  });

  it('matches the format case-insensitively', () => {
    const rows = [track({ quality: 'FLAC' })];
    expect(filterResults(rows, filters({ format: 'flac' }))).toHaveLength(1);
  });

  it('drops a result whose format is unknown when a format is selected', () => {
    expect(filterResults([track({ quality: '' })], filters({ format: 'mp3' }))).toEqual([]);
  });
});

describe('sort direction', () => {
  // The bug the port fixes: the vanilla sorted into the natural order and THEN
  // reversed whenever the user had NOT asked for a reversal, so the default
  // view of every search was backwards while the arrow rendered ↓.
  const rows = [
    track({ title: 'low', quality_score: 0.1 }),
    track({ title: 'high', quality_score: 0.9 }),
    track({ title: 'mid', quality_score: 0.5 }),
  ];

  it('puts the best quality first by default', () => {
    expect(names(applyFiltersAndSort(rows, filters(), ''))).toEqual(['high', 'mid', 'low']);
  });

  it('reverses only when the user asks', () => {
    expect(names(applyFiltersAndSort(rows, filters({ reversed: true }), ''))).toEqual([
      'low',
      'mid',
      'high',
    ]);
  });

  it('runs text ascending by default and descending when reversed', () => {
    const text = [track({ title: 'Zulu' }), track({ title: 'Alpha' })];
    expect(names(applyFiltersAndSort(text, filters({ sort: 'title' }), ''))).toEqual([
      'Alpha',
      'Zulu',
    ]);
    expect(
      names(applyFiltersAndSort(text, filters({ sort: 'title', reversed: true }), '')),
    ).toEqual(['Zulu', 'Alpha']);
  });
});

describe('sorting a mixed album/track list', () => {
  // Every case here ranked albums at zero in the vanilla, because the value
  // came from `a[key]` and albums carry none of those keys.
  it('sorts by size across both shapes', () => {
    const rows = [
      track({ title: 'small track', size: 100 }),
      album({ album_title: 'big album', total_size: 900 }),
      track({ title: 'big track', size: 500 }),
    ];
    expect(names(applyFiltersAndSort(rows, filters({ sort: 'size' }), ''))).toEqual([
      'big album',
      'big track',
      'small track',
    ]);
  });

  it('sorts by name across both shapes', () => {
    const rows = [
      album({ album_title: 'Zulu' }),
      track({ title: 'Mike' }),
      album({ album_title: 'Alpha' }),
    ];
    expect(names(applyFiltersAndSort(rows, filters({ sort: 'title' }), ''))).toEqual([
      'Alpha',
      'Mike',
      'Zulu',
    ]);
  });

  it('sorts by uploader — by USERNAME, not by title', () => {
    // The vanilla's string branch compared `album_title || title` whatever the
    // key was, so Uploader silently sorted by name.
    const rows = [
      track({ title: 'aaa', username: 'zoe' }),
      album({ album_title: 'zzz', username: 'anna' }),
    ];
    expect(
      applyFiltersAndSort(rows, filters({ sort: 'username' }), '').map((r) => r.username),
    ).toEqual(['anna', 'zoe']);
  });

  it('sorts by bitrate across both shapes', () => {
    const rows = [
      track({ title: 'lossy', bitrate: 128 }),
      album({ album_title: 'lossless album', tracks: [track({ bitrate: 1000 })] }),
    ];
    expect(names(applyFiltersAndSort(rows, filters({ sort: 'bitrate' }), ''))).toEqual([
      'lossless album',
      'lossy',
    ]);
  });

  it('sorts by duration across both shapes', () => {
    const rows = [
      track({ title: 'short', duration: 100 }),
      album({ album_title: 'long album', tracks: [track({ duration: 500 })] }),
    ];
    expect(names(applyFiltersAndSort(rows, filters({ sort: 'duration' }), ''))).toEqual([
      'long album',
      'short',
    ]);
  });

  it('keeps the server order for ties', () => {
    // The server ranks by quality before sending; a stable sort means equal
    // rows keep that ranking instead of shuffling on every re-render.
    const rows = [
      track({ title: 'first', size: 100 }),
      track({ title: 'second', size: 100 }),
      track({ title: 'third', size: 100 }),
    ];
    expect(names(applyFiltersAndSort(rows, filters({ sort: 'size' }), ''))).toEqual([
      'first',
      'second',
      'third',
    ]);
  });

  it('does not mutate the input array', () => {
    const rows = [track({ title: 'b', size: 1 }), track({ title: 'a', size: 2 })];
    applyFiltersAndSort(rows, filters({ sort: 'size' }), '');
    expect(names(rows)).toEqual(['b', 'a']);
  });
});

describe('relevanceScore', () => {
  it('rewards a result that matches every query term', () => {
    const hit = track({ title: 'Xtal', artist: 'Aphex Twin' });
    const miss = track({ title: 'Unrelated', artist: 'Nobody', album: '' });
    expect(relevanceScore(hit, 'aphex xtal')).toBeGreaterThan(relevanceScore(miss, 'aphex xtal'));
  });

  it('matches an album on its album_title', () => {
    const hit = album({ album_title: 'Selected Ambient Works', artist: 'Nobody' });
    const miss = album({ album_title: 'Nothing', artist: 'Nobody' });
    expect(relevanceScore(hit, 'ambient works')).toBeGreaterThan(
      relevanceScore(miss, 'ambient works'),
    );
  });

  it('counts the quality score, which used to be absent from every payload', () => {
    const good = track({ quality_score: 1 });
    const poor = track({ quality_score: 0 });
    expect(relevanceScore(good, 'xtal') - relevanceScore(poor, 'xtal')).toBeCloseTo(0.25, 10);
  });

  it('never returns NaN for a query with no usable terms', () => {
    // 'a' is one character, so it is dropped as a term and the vanilla divided
    // by zero — one NaN poisons every comparison it takes part in.
    for (const query of ['a', '', '  ', 'x y']) {
      const score = relevanceScore(track(), query);
      expect(Number.isNaN(score)).toBe(false);
    }
  });

  it('keeps a NaN-free list sortable', () => {
    const rows = [track({ title: 'b' }), track({ title: 'a' })];
    const sorted = applyFiltersAndSort(rows, filters({ sort: 'relevance' }), 'a');
    expect(sorted).toHaveLength(2);
    expect(sorted.every((r) => !Number.isNaN(relevanceScore(r, 'a')))).toBe(true);
  });

  it('stays within 0..1', () => {
    const best = track({
      quality_score: 1,
      free_upload_slots: 5,
      upload_speed: 10_000_000,
      bitrate: 5000,
      duration: 100,
    });
    expect(relevanceScore(best, 'xtal')).toBeLessThanOrEqual(1);
    expect(relevanceScore(track({ quality_score: 0 }), 'zzzz')).toBeGreaterThanOrEqual(0);
  });
});

describe('display formatting', () => {
  it('formats megabytes to one decimal', () => {
    expect(formatSize(10 * 1024 * 1024)).toBe('10.0 MB');
  });

  it('says so when there is no size rather than showing 0.0 MB', () => {
    expect(formatSize(0)).toBe('Unknown size');
    expect(formatSize(null)).toBe('Unknown size');
    expect(formatSize(undefined)).toBe('Unknown size');
  });

  it('renders nothing for an absent bitrate', () => {
    expect(formatBitrate(320)).toBe('320kbps');
    expect(formatBitrate(null)).toBe('');
    expect(formatBitrate(0)).toBe('');
  });

  it('labels an album by its dominant quality, not "Mixed"', () => {
    // The vanilla read `result.quality`, which an album never has, so a pure
    // FLAC album was labelled "Mixed".
    expect(albumFormatLabel(album({ dominant_quality: 'flac' }))).toBe('flac');
    expect(albumFormatLabel(album({ dominant_quality: '' }))).toBe('Mixed');
  });
});

describe('detectDiscBreaks', () => {
  it('finds nothing in a single-disc album', () => {
    const tracks = [1, 2, 3].map((n) => track({ track_number: n }));
    expect(detectDiscBreaks(tracks)).toEqual(new Set());
  });

  it('breaks where the track number resets', () => {
    const tracks = [1, 2, 1, 2].map((n) => track({ track_number: n }));
    expect(detectDiscBreaks(tracks)).toEqual(new Set([2]));
  });

  it('breaks on a repeat as well as a reset', () => {
    const tracks = [1, 2, 2].map((n) => track({ track_number: n }));
    expect(detectDiscBreaks(tracks)).toEqual(new Set([2]));
  });

  it('never marks index 0', () => {
    expect(detectDiscBreaks([track({ track_number: 5 })])).toEqual(new Set());
  });

  it('ignores tracks with no number instead of breaking on them', () => {
    const tracks = [
      track({ track_number: 1 }),
      track({ track_number: null }),
      track({ track_number: 2 }),
    ];
    expect(detectDiscBreaks(tracks)).toEqual(new Set());
  });

  it('handles three discs', () => {
    const tracks = [1, 2, 1, 2, 1].map((n) => track({ track_number: n }));
    expect(detectDiscBreaks(tracks)).toEqual(new Set([2, 4]));
  });
});
