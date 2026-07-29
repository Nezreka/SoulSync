import { describe, expect, it } from 'vitest';

import type { LabelRelease } from './-label-detail.types';

import { LABEL_PAGE_SIZE } from './-label-detail.api';
import {
  catalogMetaLine,
  coverUrl,
  emptyStateText,
  filterCounts,
  normalizeForMatch,
  ownershipOverlay,
  releaseKey,
  visibleReleases,
} from './-label-detail.helpers';

const rel = (over: Partial<LabelRelease> = {}): LabelRelease => ({
  artist: 'Aphex Twin',
  album: 'Selected Ambient Works',
  year: '1992',
  ...over,
});

describe('releaseKey', () => {
  it('is artist||album, lowercased', () => {
    expect(releaseKey(rel())).toBe('aphex twin||selected ambient works');
  });

  it('keeps punctuation and spacing, so two different releases stay different', () => {
    // Deliberately NOT normalised: the key joins the ownership response, the
    // cover cache and the click lookup. Flattening it would merge a
    // self-titled album with a same-named EP.
    expect(releaseKey(rel({ album: 'Drukqs (Deluxe)' }))).not.toBe(
      releaseKey(rel({ album: 'Drukqs' })),
    );
  });

  it('survives a release with nothing on it', () => {
    expect(releaseKey({})).toBe('||');
    expect(releaseKey(undefined)).toBe('||');
  });
});

describe('normalizeForMatch', () => {
  it('flattens punctuation to single spaces', () => {
    expect(normalizeForMatch('Drukqs (Deluxe Edition)!')).toBe('drukqs deluxe edition');
  });

  it('is empty for junk', () => {
    expect(normalizeForMatch('!!!')).toBe('');
    expect(normalizeForMatch(null)).toBe('');
  });
});

describe('coverUrl', () => {
  it('prefers the exact release id and always cache-busts', () => {
    const url = coverUrl(rel({ release_id: 'r-1' }));
    expect(url).toContain('release_id=r-1');
    expect(url).toContain('artist=Aphex%20Twin');
    expect(url).toContain('cb=2');
  });

  it('returns nothing when there is nothing to look up', () => {
    // The empty string is what suppresses the lazy-load attempt entirely —
    // a URL of "/api/labels/cover?" would 400 on every such card.
    expect(coverUrl({})).toBe('');
    expect(coverUrl(null)).toBe('');
  });

  it('escapes values that would otherwise break the query', () => {
    expect(coverUrl(rel({ artist: 'AC/DC', album: 'Back in Black & Blue' }))).toContain(
      'artist=AC%2FDC',
    );
  });
});

describe('visibleReleases', () => {
  const rows = [
    rel({ artist: 'B', album: 'New', year: '2020' }),
    rel({ artist: 'A', album: 'Mid', year: '2010' }),
    rel({ artist: 'A', album: 'Old', year: '2000' }),
  ];
  const owned = new Set([releaseKey(rows[1])]);

  it('leaves the server order alone for newest', () => {
    expect(visibleReleases(rows, owned, 'all', 'newest').map((r) => r.album)).toEqual([
      'New',
      'Mid',
      'Old',
    ]);
  });

  it('reverses for oldest rather than re-sorting by year', () => {
    // The catalog arrives newest-first, so reversing IS oldest-first — and it
    // keeps releases with no year in a stable place, which a year sort would
    // not.
    expect(visibleReleases(rows, owned, 'all', 'oldest').map((r) => r.album)).toEqual([
      'Old',
      'Mid',
      'New',
    ]);
  });

  it('sorts by artist with the NEWEST of that artist first', () => {
    // The year tiebreak is b-vs-a: descending, so an artist's latest leads.
    expect(visibleReleases(rows, owned, 'all', 'artist').map((r) => r.album)).toEqual([
      'Mid',
      'Old',
      'New',
    ]);
  });

  it('filters to owned and to missing', () => {
    expect(visibleReleases(rows, owned, 'owned', 'newest').map((r) => r.album)).toEqual(['Mid']);
    expect(visibleReleases(rows, owned, 'missing', 'newest').map((r) => r.album)).toEqual([
      'New',
      'Old',
    ]);
  });

  it('does not mutate the array it was given', () => {
    const input = rows.slice();
    visibleReleases(input, owned, 'all', 'oldest');
    expect(input.map((r) => r.album)).toEqual(['New', 'Mid', 'Old']);
  });
});

describe('filterCounts', () => {
  it('counts over everything loaded, not the visible rows', () => {
    // Switching the filter must not change the numbers on the pills.
    const rows = [rel({ album: 'A' }), rel({ album: 'B' }), rel({ album: 'C' })];
    const owned = new Set([releaseKey(rows[0])]);
    expect(filterCounts(rows, owned)).toEqual({ all: 3, owned: 1, missing: 2 });
  });

  it('is all-missing before ownership resolves', () => {
    expect(filterCounts([rel()], new Set())).toEqual({ all: 1, owned: 0, missing: 1 });
  });
});

describe('catalogMetaLine', () => {
  it('pluralises each half independently', () => {
    expect(catalogMetaLine(1, 1)).toBe('1 release · 1 artist');
    expect(catalogMetaLine(12, 3)).toBe('12 releases · 3 artists');
    expect(catalogMetaLine(0, 0)).toBe('0 releases · 0 artists');
  });

  it('treats a missing count as zero', () => {
    expect(catalogMetaLine(undefined, undefined)).toBe('0 releases · 0 artists');
  });
});

describe('emptyStateText', () => {
  it('distinguishes an empty catalog from an excluding filter', () => {
    expect(emptyStateText(0, 'all')).toBe('No releases to show.');
    expect(emptyStateText(40, 'owned')).toBe('No owned releases in this label.');
    expect(emptyStateText(40, 'missing')).toBe('No missing releases in this label.');
  });
});

describe('ownershipOverlay', () => {
  const key = 'a||b';

  it('shows nothing until the release has been checked', () => {
    // Rendering "Missing" first would flash a wrong answer across the grid on
    // every page load.
    expect(ownershipOverlay(key, new Set(), new Set())).toBeNull();
  });

  it('shows missing once checked and not owned', () => {
    expect(ownershipOverlay(key, new Set(), new Set([key]))).toBe('missing');
  });

  it('shows owned regardless of the checked set', () => {
    expect(ownershipOverlay(key, new Set([key]), new Set())).toBe('owned');
  });
});

describe('values recorded from static/label-detail.js before it was deleted', () => {
  // These three were differential checks against the vanilla source while both
  // existed. The vanilla page is gone in this same PR, so the values it held
  // are pinned here with their provenance — losing the check entirely would
  // let them drift silently, and they are all shared with the BACKEND.

  it('pages in 60s, as the endpoint was tuned for', () => {
    // label-detail.js: `const PAGE_SIZE = 60;`
    expect(LABEL_PAGE_SIZE).toBe(60);
  });

  it('keeps the cover cache-bust token', () => {
    // label-detail.js: `p.push('cb=2')` — an earlier build cached the 302 for a
    // day and pinned a dead Cover Art Archive target. Bump it (here) if that
    // ever needs breaking again.
    expect(coverUrl({ artist: 'x' })).toContain('cb=2');
  });

  it('keeps the ownership key shape the library-check response is matched on', () => {
    // label-detail.js:
    //   const _key = (r) => `${(r.artist || '').toLowerCase()}||${(r.album || '').toLowerCase()}`;
    expect(releaseKey({ artist: 'Aphex Twin', album: 'Drukqs' })).toBe('aphex twin||drukqs');
  });
});
