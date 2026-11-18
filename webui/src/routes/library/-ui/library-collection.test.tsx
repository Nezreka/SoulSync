import { describe, expect, it } from 'vitest';

import type { LibraryV2ArtistSummary } from '../-library-v2.types';

import {
  artistCompletion,
  completionPercent,
  indexParamsOf,
  sortIsDescending,
} from './library-collection';

const artist = (over: Partial<LibraryV2ArtistSummary>) =>
  ({ id: 1, name: 'A', track_count: 10, tracks_present: 10, ...over }) as LibraryV2ArtistSummary;

describe('#2106 an artist is only complete when there is something to be complete against', () => {
  it('reads the state the same way the server does', () => {
    expect(artistCompletion(artist({}))).toBe('complete');
    expect(artistCompletion(artist({ tracks_present: 7 }))).toBe('incomplete');
    expect(artistCompletion(artist({ unverified_albums: 1 }))).toBe('unchecked');
    expect(artistCompletion(artist({ track_count: 0, tracks_present: 0 }))).toBe('unknown');
    // the server's word wins
    expect(artistCompletion(artist({ completion: 'unchecked' }))).toBe('unchecked');
  });

  it('has no percentage when nothing is expected', () => {
    expect(completionPercent(0, 0)).toBeNull();
    expect(completionPercent(341, 345)).toBeCloseTo(98.84, 1);
  });
});

describe('#2107 sorting and remembering the list', () => {
  it('knows which way each sort runs', () => {
    expect(sortIsDescending('name', '')).toBe(false);
    expect(sortIsDescending('missing', '')).toBe(true);
    expect(sortIsDescending('completion', '')).toBe(false);
    expect(sortIsDescending('completion', 'desc')).toBe(true);
    expect(sortIsDescending('missing', 'asc')).toBe(false);
  });

  it('remembers only what differs from a fresh list', () => {
    expect(
      indexParamsOf({
        view: 'cards',
        sort: 'name',
        monitored: 'all',
        letter: 'all',
        completion: '',
        page: 4,
        artist: 9,
      }),
    ).toEqual({});
    expect(indexParamsOf({ view: 'table', sort: 'missing', dir: 'asc', missingMin: 2 })).toEqual({
      view: 'table',
      sort: 'missing',
      dir: 'asc',
      missingMin: 2,
    });
  });
});
