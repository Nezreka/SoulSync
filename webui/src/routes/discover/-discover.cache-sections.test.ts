import { describe, expect, it } from 'vitest';

import {
  ALBUM_PLACEHOLDER,
  ALBUM_UNAVAILABLE,
  CACHE_SECTIONS,
  GENRE_DIVE_DEFAULT_SUBTITLE,
  GENRE_DIVE_EMPTY,
  GENRE_EXPLORER_SECTION,
  GRID_CLAMP_LIMIT,
  NO_ALBUM_ID,
  NO_ARTIST_DATA,
  TRACK_SECTIONS,
  albumFetchUrl,
  cacheDiscoverCard,
  cacheSectionItems,
  cacheSectionShouldRender,
  cacheVirtualAlbumId,
  diveArtistHasLink,
  diveTrackSubtitle,
  formatDuration,
  formatFollowers,
  genreDiveSubtitle,
  genreDiveUrl,
  genrePill,
  gridClamp,
  isTrackSection,
  resolveCacheAlbumUrl,
  resolvedIdIsUseful,
  shouldResolveAlbum,
  shouldResolveTrackAlbum,
  sourceDotClass,
} from './-discover.cache-sections';

describe('the section definitions', () => {
  it('covers the four loader-backed sections', () => {
    expect(CACHE_SECTIONS.map((s) => s.key)).toEqual([
      'undiscovered',
      'genre_releases',
      'label_explorer',
      'deep_cuts',
    ]);
  });

  it('keeps each title and subtitle verbatim', () => {
    const byKey = Object.fromEntries(CACHE_SECTIONS.map((s) => [s.key, s]));
    // The streaming-voice pass: personal titles, ownership up front.
    expect([byKey.undiscovered.title, byKey.undiscovered.subtitle]).toEqual([
      "Albums You're Missing",
      'From artists you already love — one click to own them',
    ]);
    expect([byKey.genre_releases.title, byKey.genre_releases.subtitle]).toEqual([
      'New In Your Genres',
      'Fresh releases from the sounds you collect',
    ]);
    expect([byKey.label_explorer.title, byKey.label_explorer.subtitle]).toEqual([
      'More From Your Labels',
      'Because you collect these labels',
    ]);
    expect([byKey.deep_cuts.title, byKey.deep_cuts.subtitle]).toEqual([
      'Deep Cuts',
      'B-sides and buried tracks from artists you know',
    ]);
  });

  it('reads TRACKS for deep cuts and albums for the rest', () => {
    const byKey = Object.fromEntries(CACHE_SECTIONS.map((s) => [s.key, s]));
    expect(byKey.deep_cuts.field).toBe('tracks');
    expect(CACHE_SECTIONS.filter((s) => s.field === 'albums')).toHaveLength(3);
  });

  it('puts the Genre Explorer at the TOP and unwrapped', () => {
    // It renders pills, not cards, so it must not get a .discover-grid wrapper
    // (and therefore is never clamped).
    expect(GENRE_EXPLORER_SECTION.position).toBe('top');
    expect(GENRE_EXPLORER_SECTION.wrapGrid).toBe(false);
  });
});

describe('reading a section response', () => {
  it('needs success AND a non-empty array', () => {
    expect(cacheSectionItems({ success: true, albums: [{ name: 'a' }] }, 'albums')).toHaveLength(1);
    expect(cacheSectionItems({ success: false, albums: [{ name: 'a' }] }, 'albums')).toEqual([]);
    expect(cacheSectionItems({ success: true, albums: [] }, 'albums')).toEqual([]);
    expect(cacheSectionItems({ success: true }, 'albums')).toEqual([]);
    expect(cacheSectionItems(null, 'albums')).toEqual([]);
  });

  it('ignores a non-array payload rather than throwing', () => {
    expect(cacheSectionItems({ success: true, albums: 'nope' }, 'albums')).toEqual([]);
  });

  it('reads the right field per section', () => {
    expect(cacheSectionItems({ success: true, tracks: [{}] }, 'tracks')).toHaveLength(1);
    expect(cacheSectionItems({ success: true, tracks: [{}] }, 'albums')).toEqual([]);
  });

  it('creates NO section for an empty response, rather than an empty one', () => {
    // Each loader returns early before _insertCacheSection, so "you have no
    // undiscovered albums" is silent instead of a titled empty box.
    expect(cacheSectionShouldRender({ success: true, albums: [{}] }, 'albums')).toBe(true);
    expect(cacheSectionShouldRender({ success: true, albums: [] }, 'albums')).toBe(false);
    expect(cacheSectionShouldRender({ success: true }, 'albums')).toBe(false);
    expect(cacheSectionShouldRender({ success: false, albums: [{}] }, 'albums')).toBe(false);
    expect(cacheSectionShouldRender(null, 'albums')).toBe(false);
  });
});

describe('the card', () => {
  it('falls back to the placeholder cover', () => {
    expect(cacheDiscoverCard({}).cover).toBe(ALBUM_PLACEHOLDER);
    expect(cacheDiscoverCard({ image_url: '/a.jpg' }).cover).toBe('/a.jpg');
  });

  it('shows a tick ONLY for in-library items', () => {
    // There is no "missing" counterpart badge here — unlike Your Albums, which
    // badges both states.
    expect(cacheDiscoverCard({ in_library: true }).ownedBadge).toBe(true);
    expect(cacheDiscoverCard({ in_library: false }).ownedBadge).toBe(false);
    expect(cacheDiscoverCard({}).ownedBadge).toBe(false);
  });

  it('defaults name and artist to empty strings', () => {
    expect(cacheDiscoverCard({})).toMatchObject({ title: '', subtitle: '' });
  });
});

describe('the show-all clamp', () => {
  it('shows no toggle when everything fits', () => {
    expect(gridClamp(12, false)).toEqual({ toggleVisible: false, visibleCount: 12, label: '' });
    expect(gridClamp(0, false).toggleVisible).toBe(false);
    expect(GRID_CLAMP_LIMIT).toBe(12);
  });

  it('appears at one over the limit', () => {
    expect(gridClamp(13, false)).toEqual({
      toggleVisible: true,
      visibleCount: 12,
      label: 'Show all 13',
    });
  });

  it('labels with the FULL count, not the hidden remainder', () => {
    expect(gridClamp(30, false).label).toBe('Show all 30');
  });

  it('flips to Show less when expanded', () => {
    expect(gridClamp(30, true)).toEqual({
      toggleVisible: true,
      visibleCount: 30,
      label: 'Show less',
    });
  });

  it('honours a custom limit', () => {
    expect(gridClamp(8, false, 5).visibleCount).toBe(5);
  });
});

describe('genre pills', () => {
  it('pluralises the artist count', () => {
    expect(genrePill({ genre: 'techno', artist_count: 1 }).countLabel).toBe('1 artist');
    expect(genrePill({ genre: 'techno', artist_count: 4 }).countLabel).toBe('4 artists');
    expect(genrePill({ genre: 'techno', artist_count: 0 }).countLabel).toBe('0 artists');
  });

  it('marks unexplored genres New — the inverse of explored, not a field', () => {
    expect(genrePill({ explored: true }).isNew).toBe(false);
    expect(genrePill({ explored: false }).isNew).toBe(true);
    expect(genrePill({}).isNew).toBe(true);
  });

  it('defaults a missing count to zero rather than NaN', () => {
    expect(genrePill({ genre: 'x' }).countLabel).toBe('0 artists');
  });
});

describe('the deep dive header', () => {
  it('counts each present section, in order', () => {
    expect(genreDiveSubtitle({ artists: [1], tracks: [1, 2], albums: [1, 2, 3] })).toBe(
      '1 artist · 2 tracks · 3 albums',
    );
  });

  it('omits absent sections without a dangling separator', () => {
    expect(genreDiveSubtitle({ tracks: [1, 2] })).toBe('2 tracks');
    expect(genreDiveSubtitle({ artists: [1], albums: [1] })).toBe('1 artist · 1 album');
  });

  it('falls back to the literal title when everything is empty', () => {
    expect(genreDiveSubtitle({})).toBe(GENRE_DIVE_DEFAULT_SUBTITLE);
    expect(genreDiveSubtitle({ artists: [], tracks: [] })).toBe('Genre Deep Dive');
  });

  it('encodes the genre into the url', () => {
    expect(genreDiveUrl('drum & bass')).toBe(
      '/api/discover/genre-deep-dive?genre=drum%20%26%20bass',
    );
  });

  it('keeps the empty copy', () => {
    expect(GENRE_DIVE_EMPTY).toBe('No cached data found for this genre yet');
  });
});

describe('number formatting', () => {
  it('uses one decimal at millions and NONE at thousands', () => {
    expect(formatFollowers(1200000)).toBe('1.2M');
    expect(formatFollowers(45000)).toBe('45K');
    expect(formatFollowers(45500)).toBe('46K'); //  toFixed(0) rounds
  });

  it('switches unit exactly at the boundaries', () => {
    expect(formatFollowers(999)).toBe('999');
    expect(formatFollowers(1000)).toBe('1K');
    expect(formatFollowers(999999)).toBe('1000K'); //  not 1.0M — the vanilla's boundary
    expect(formatFollowers(1000000)).toBe('1.0M');
  });

  it('is EMPTY for zero, so the caller drops the whole line', () => {
    expect(formatFollowers(0)).toBe('');
    expect(formatFollowers(undefined)).toBe('');
    expect(formatFollowers(null)).toBe('');
  });

  it('formats durations as m:ss', () => {
    expect(formatDuration(305000)).toBe('5:05');
    expect(formatDuration(60000)).toBe('1:00');
    expect(formatDuration(3600000)).toBe('60:00'); //  no hours unit
  });

  it('zero-pads the seconds', () => {
    expect(formatDuration(61000)).toBe('1:01');
  });

  it('is EMPTY for a missing duration, not "0:00"', () => {
    expect(formatDuration(0)).toBe('');
    expect(formatDuration(undefined)).toBe('');
  });
});

describe('dive rows', () => {
  it('lowercases the source into the dot class', () => {
    expect(sourceDotClass('Spotify')).toBe('genre-dive-src-spotify');
    expect(sourceDotClass(undefined)).toBe('genre-dive-src-');
  });

  it('appends the album to the track subtitle only when there is one', () => {
    expect(diveTrackSubtitle({ artist_name: 'A', album_name: 'B' })).toBe('A · B');
    expect(diveTrackSubtitle({ artist_name: 'A' })).toBe('A');
    expect(diveTrackSubtitle({})).toBe('');
  });

  it('links an artist only with an entity_id', () => {
    expect(diveArtistHasLink({ entity_id: 'x' })).toBe(true);
    expect(diveArtistHasLink({})).toBe(false);
  });
});

describe('opening an album', () => {
  it('knows which two sections hold TRACKS', () => {
    expect(TRACK_SECTIONS).toEqual(['deep_cuts', 'genre_dive_tracks']);
    expect(isTrackSection('deep_cuts')).toBe(true);
    expect(isTrackSection('genre_dive_tracks')).toBe(true);
    expect(isTrackSection('undiscovered')).toBe(false);
    expect(isTrackSection('genre_dive_albums')).toBe(false);
  });

  it('retries the ALBUM path only on 404', () => {
    // A stale cache entry pointing at a removed album is worth resolving; a 500
    // is the server being unwell and would just fail twice.
    expect(shouldResolveAlbum(404)).toBe(true);
    expect(shouldResolveAlbum(500)).toBe(false);
    expect(shouldResolveAlbum(200)).toBe(false);
  });

  it('retries the TRACK path on any failure, or with no album_id at all', () => {
    expect(shouldResolveTrackAlbum(false, false)).toBe(true); //  never had an id
    expect(shouldResolveTrackAlbum(true, false)).toBe(true); //   had one, it failed
    expect(shouldResolveTrackAlbum(true, true)).toBe(false); //   worked
  });

  it('re-fetches only when the resolver returns a DIFFERENT id', () => {
    // Re-requesting the id that just 404'd fails identically.
    expect(resolvedIdIsUseful({ success: true, entity_id: 'new' }, 'old')).toBe(true);
    expect(resolvedIdIsUseful({ success: true, entity_id: 'same' }, 'same')).toBe(false);
    expect(resolvedIdIsUseful({ success: false, entity_id: 'new' }, 'old')).toBe(false);
    expect(resolvedIdIsUseful({ success: true }, 'old')).toBe(false);
    expect(resolvedIdIsUseful(null, 'old')).toBe(false);
  });

  it('builds the album and resolver urls', () => {
    expect(albumFetchUrl('spotify', 'a1', 'Album Name', 'Artist')).toBe(
      '/api/discover/album/spotify/a1?name=Album+Name&artist=Artist',
    );
    expect(resolveCacheAlbumUrl('A & B', 'C')).toBe(
      '/api/discover/resolve-cache-album?name=A%20%26%20B&artist=C',
    );
  });

  it('prefixes the virtual id', () => {
    expect(cacheVirtualAlbumId('a1')).toBe('discover_cache_a1');
  });

  it('keeps the failure copy', () => {
    expect(NO_ARTIST_DATA).toBe('No artist data available for this track');
    expect(NO_ALBUM_ID).toBe('No album ID available');
    expect(ALBUM_UNAVAILABLE).toBe(
      'Album not available — it may have been removed from the source',
    );
  });
});
