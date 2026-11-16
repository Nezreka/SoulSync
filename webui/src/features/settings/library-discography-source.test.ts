import { describe, expect, it } from 'vitest';

import {
  LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS,
  normalizeLibraryDiscographySource,
} from './library-discography-source';

// the picker is React now (tabs/connections-metadata.test.tsx covers it)
describe('Library discography source choices', () => {
  it('normalizes unknown values to the primary metadata source', () => {
    expect(normalizeLibraryDiscographySource('deezer')).toBe('deezer');
    expect(normalizeLibraryDiscographySource(' Deezer ')).toBe('deezer');
    expect(normalizeLibraryDiscographySource('not-valid')).toBe('primary');
    expect(normalizeLibraryDiscographySource(undefined)).toBe('primary');
  });

  it('offers the primary source first, then the catalogues', () => {
    expect(LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS.map((o) => o.value)).toEqual([
      'primary',
      'automatic',
      'itunes',
      'deezer',
      'musicbrainz',
      'spotify',
    ]);
  });
});
