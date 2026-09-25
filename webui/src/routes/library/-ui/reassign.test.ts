import { describe, expect, it } from 'vitest';

import { albumBits, describeMapping, describeMatch, reassignSubject } from './reassign';

/**
 * The pure helpers behind the reassign modal. These are what the user reads
 * before agreeing to move an album, so they have to be honest about an
 * incomplete mapping rather than rounding it up to "looks fine".
 */

describe('describeMapping', () => {
  it('says so plainly when everything lines up', () => {
    expect(describeMapping({ success: true, mapped_count: 12, unmapped_count: 0 })).toBe(
      'All 12 tracks line up',
    );
  });

  it('names what would be LEFT BEHIND, not just what moves', () => {
    // The user is about to split their album if they proceed. Reporting only
    // "9 tracks line up" would read like success.
    expect(describeMapping({ success: true, mapped_count: 9, unmapped_count: 3 })).toBe(
      '9 of 12 tracks line up — 3 would stay with the current artist',
    );
  });

  it('handles nothing at all without producing "0 of 0"', () => {
    expect(describeMapping({ success: true, mapped_count: 0, unmapped_count: 0 })).toBe(
      'Nothing to line up',
    );
  });

  it('survives a payload with the counts missing', () => {
    expect(describeMapping({ success: true })).toBe('Nothing to line up');
  });
});

describe('describeMatch', () => {
  const base = {
    local_id: 1,
    local_title: 'x',
    local_track_number: 1,
    target_title: 'x',
    target_track_number: 1,
  };

  it('explains WHY a pairing was proposed', () => {
    expect(describeMatch({ ...base, mapped: true, matched_by: 'track_number' })).toBe(
      'by track number',
    );
    expect(describeMatch({ ...base, mapped: true, matched_by: 'title' })).toBe('by title');
  });

  it('says no match rather than inventing a reason', () => {
    expect(describeMatch({ ...base, mapped: false, matched_by: null })).toBe('no match');
  });

  it('falls back without crashing on an unknown reason', () => {
    expect(describeMatch({ ...base, mapped: true, matched_by: 'future-thing' })).toBe('matched');
  });
});

describe('reassignSubject', () => {
  it('labels the album as a Library v2 row', () => {
    // Not decoration: the service refuses a bare id, because the hint this
    // flow writes is resolved against lib2_track_files and a legacy id would
    // quietly name a different track's file for deletion.
    expect(reassignSubject(4242)).toBe('lib2:4242');
  });
});

describe('albumBits', () => {
  it('reads as one line', () => {
    expect(
      albumBits({ id: '1', name: 'X', year: '2019-03-04', album_type: 'album', total_tracks: 12 }),
    ).toBe('2019 · album · 12 tracks');
  });

  it('skips blanks instead of leaving empty separators', () => {
    expect(albumBits({ id: '1', name: 'X' })).toBe('');
    expect(albumBits({ id: '1', name: 'X', album_type: 'single' })).toBe('single');
  });
});
