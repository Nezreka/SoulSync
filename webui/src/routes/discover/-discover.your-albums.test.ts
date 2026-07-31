import { describe, expect, it } from 'vitest';

import {
  ALBUM_COVER_FALLBACK,
  YOUR_ALBUMS_EMPTY,
  YOUR_ALBUMS_ERROR,
  albumBadge,
  albumCover,
  yourAlbumsNextPage,
  yourAlbumsPagination,
  yourAlbumsPrevPage,
  yourAlbumsQuery,
  yourAlbumsSubtitle,
} from './-discover.your-albums';

describe('the grid query', () => {
  it('always sends page, per_page, sort and status', () => {
    expect(yourAlbumsQuery({ page: 2 })).toEqual({
      page: '2',
      per_page: '48',
      sort: 'artist_name',
      status: 'all',
    });
  });

  it('OMITS search when it is empty or whitespace', () => {
    // Sending `search=` makes the server filter on an empty string rather than
    // skip filtering — the vanilla guards with `if (search)`.
    expect(yourAlbumsQuery({ page: 1, search: '' })).not.toHaveProperty('search');
    expect(yourAlbumsQuery({ page: 1, search: '   ' })).not.toHaveProperty('search');
  });

  it('trims and sends a real search', () => {
    expect(yourAlbumsQuery({ page: 1, search: '  aphex  ' }).search).toBe('aphex');
  });

  it('honours explicit status and sort', () => {
    const q = yourAlbumsQuery({ page: 1, status: 'missing', sort: 'year' });
    expect([q.status, q.sort]).toEqual(['missing', 'year']);
  });
});

describe('the ownership badge', () => {
  it('reads in_library, the field the server actually sends', () => {
    expect(albumBadge({ in_library: true })).toEqual({ className: 'owned', icon: '✓' });
    expect(albumBadge({ in_library: false })).toEqual({ className: 'missing', icon: '↓' });
  });

  it('treats an absent flag as missing', () => {
    expect(albumBadge({}).className).toBe('missing');
    expect(albumBadge(null).className).toBe('missing');
  });
});

describe('cover art', () => {
  it('uses the album image when present', () => {
    expect(albumCover({ image_url: '/art.jpg' })).toBe('/art.jpg');
  });

  it('falls back to the placeholder', () => {
    expect(albumCover({})).toBe(ALBUM_COVER_FALLBACK);
    expect(albumCover(null)).toBe('/static/placeholder-album.png');
  });
});

describe('the subtitle', () => {
  it('reads the vanilla format exactly', () => {
    expect(yourAlbumsSubtitle({ total: 120, owned: 40, missing: 80 })).toBe(
      '120 albums · 40 owned · 80 missing',
    );
  });

  it('renders zeros rather than blanks for a partial stats object', () => {
    expect(yourAlbumsSubtitle({ total: 5 })).toBe('5 albums · 0 owned · 0 missing');
  });

  it('is null without stats, so the caller leaves the subtitle alone', () => {
    expect(yourAlbumsSubtitle(null)).toBeNull();
    expect(yourAlbumsSubtitle(undefined)).toBeNull();
  });
});

describe('pagination', () => {
  it('is HIDDEN when everything fits on one page', () => {
    // An all-disabled pager is noise; the vanilla display:none's it.
    expect(yourAlbumsPagination(48, 1).visible).toBe(false);
    expect(yourAlbumsPagination(10, 1).visible).toBe(false);
    expect(yourAlbumsPagination(0, 1).visible).toBe(false);
  });

  it('appears the moment there is a second page', () => {
    expect(yourAlbumsPagination(49, 1).visible).toBe(true);
  });

  it('computes the 1-based inclusive range', () => {
    const p1 = yourAlbumsPagination(100, 1);
    expect([p1.start, p1.end, p1.label]).toEqual([1, 48, '1–48 of 100']);
    const p2 = yourAlbumsPagination(100, 2);
    expect([p2.start, p2.end, p2.label]).toEqual([49, 96, '49–96 of 100']);
  });

  it('clamps the last page’s end to the total, not the page boundary', () => {
    const last = yourAlbumsPagination(100, 3);
    expect([last.start, last.end]).toEqual([97, 100]);
  });

  it('disables prev on the first page and next on the last', () => {
    expect(yourAlbumsPagination(100, 1).prevDisabled).toBe(true);
    expect(yourAlbumsPagination(100, 1).nextDisabled).toBe(false);
    expect(yourAlbumsPagination(100, 3).prevDisabled).toBe(false);
    expect(yourAlbumsPagination(100, 3).nextDisabled).toBe(true);
  });

  it('counts pages by ceiling', () => {
    expect(yourAlbumsPagination(97, 1).totalPages).toBe(3);
    expect(yourAlbumsPagination(96, 1).totalPages).toBe(2);
  });
});

describe('the page steppers refuse rather than clamp', () => {
  it('does not go below page 1', () => {
    expect(yourAlbumsPrevPage(1)).toBe(1);
    expect(yourAlbumsPrevPage(3)).toBe(2);
  });

  it('does not go past the last page', () => {
    // 100 albums at 48/page = 3 pages.
    expect(yourAlbumsNextPage(3, 100)).toBe(3);
    expect(yourAlbumsNextPage(1, 100)).toBe(2);
  });

  it('returns the SAME page when refused, so the caller knows not to refetch', () => {
    expect(yourAlbumsNextPage(1, 10)).toBe(1); //  single page
    expect(yourAlbumsNextPage(1, 0)).toBe(1); //   nothing at all
  });
});

describe('grid copy', () => {
  it('keeps the vanilla strings', () => {
    expect(YOUR_ALBUMS_EMPTY).toBe('No albums found');
    expect(YOUR_ALBUMS_ERROR).toBe('Failed to load albums');
  });
});
