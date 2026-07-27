import { describe, expect, it } from 'vitest';

import { buildSearchQuery } from './library-v2-page';

describe('buildSearchQuery', () => {
  it('builds an artist-level query when the action has no scoped suffix', () => {
    expect(buildSearchQuery('Radiohead', 'Interactive Search')).toBe('Radiohead');
  });

  it('combines artist name with the album/track title', () => {
    expect(buildSearchQuery('Radiohead', 'Interactive Search: OK Computer')).toBe(
      'Radiohead OK Computer',
    );
  });

  it('drops a trailing "(album)" context group', () => {
    expect(buildSearchQuery('Radiohead', 'Interactive Search: Airbag (OK Computer)')).toBe(
      'Radiohead Airbag',
    );
  });

  it('drops a trailing "- missing" suffix', () => {
    expect(buildSearchQuery('Radiohead', 'Interactive Search: Airbag - missing')).toBe(
      'Radiohead Airbag',
    );
  });

  it('falls back to the album title instead of a placeholder track label', () => {
    // Untitled tracks render as "Track <n>" / "Track ?" for display — that
    // placeholder makes a guaranteed-empty search query, so the query
    // builder must fall back to the album context instead (iss27-01).
    expect(buildSearchQuery('Radiohead', 'Interactive Search: Track 7 (OK Computer)')).toBe(
      'Radiohead OK Computer',
    );
    expect(buildSearchQuery('Radiohead', 'Interactive Search: Track ? (OK Computer)')).toBe(
      'Radiohead OK Computer',
    );
  });

  it('keeps a real track title even if it happens to start with "Track"', () => {
    expect(
      buildSearchQuery(
        'Public Enemy',
        'Interactive Search: Track Suit and Tie (Fear of a Black Planet)',
      ),
    ).toBe('Public Enemy Track Suit and Tie');
  });

  it('drops only the outer album-context group when the album title itself has parens', () => {
    // Regression: an album/track title with a "(feat. X)" credit made the
    // naive `[^)]*` regex fail to find the trailing group boundary (it isn't
    // nested-paren aware), so the ENTIRE tail — track title AND the
    // album-context duplicate — leaked into the query verbatim. That produced
    // a long, doubled, garbage query with zero real matches.
    expect(
      buildSearchQuery(
        'Drenchill',
        'Interactive Search: Freed from Desire (feat. Indiiana) - DNF Extended Remix (Freed from Desire (feat. Indiiana))',
      ),
    ).toBe('Drenchill Freed from Desire (feat. Indiiana) - DNF Extended Remix');
  });

  it('falls back to the album title (with its own parens intact) for a placeholder track', () => {
    expect(
      buildSearchQuery(
        'Drenchill',
        'Interactive Search: Track ? (Freed from Desire (feat. Indiiana))',
      ),
    ).toBe('Drenchill Freed from Desire (feat. Indiiana)');
  });

  it('leaves an unbalanced trailing paren alone instead of mangling the query', () => {
    expect(buildSearchQuery('Artist', 'Interactive Search: Weird (Title')).toBe(
      'Artist Weird (Title',
    );
  });
});
