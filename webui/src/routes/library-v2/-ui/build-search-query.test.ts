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
      buildSearchQuery('Public Enemy', 'Interactive Search: Track Suit and Tie (Fear of a Black Planet)'),
    ).toBe('Public Enemy Track Suit and Tie');
  });
});
