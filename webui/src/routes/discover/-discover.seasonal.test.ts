import { describe, expect, it } from 'vitest';

import {
  SEASONAL_ALBUMS_EMPTY,
  SEASONAL_ALBUMS_ERROR,
  SEASONAL_ALBUM_PLACEHOLDER,
  SEASONAL_CURRENT_URL,
  SEASONAL_NO_ALBUM_ID,
  SEASONAL_NO_TRACKS,
  SEASONAL_PLAYLIST_EMPTY,
  buildDiscoverArtistContext,
  seasonalAlbumCover,
  seasonalAlbumSource,
  seasonalAlbumsData,
  seasonalControllerIsStale,
  seasonalHasPlaylist,
  seasonalHeader,
  seasonalIsActive,
  seasonalMixTitles,
  seasonalPlaylistUrl,
  seasonalVirtualAlbumId,
} from './-discover.seasonal';

describe('whether the season is on', () => {
  it('needs success AND a season key', () => {
    // A response can succeed and carry no season — that is the off-season.
    expect(seasonalIsActive({ success: true, season: 'winter' })).toBe(true);
    expect(seasonalIsActive({ success: true })).toBe(false);
    expect(seasonalIsActive({ success: false, season: 'winter' })).toBe(false);
    expect(seasonalIsActive(null)).toBe(false);
  });

  it('loads the playlist half only when the season advertises one', () => {
    expect(seasonalHasPlaylist({ playlist_available: true })).toBe(true);
    expect(seasonalHasPlaylist({ playlist_available: false })).toBe(false);
    expect(seasonalHasPlaylist({})).toBe(false);
  });

  it('builds the per-season playlist url', () => {
    expect(SEASONAL_CURRENT_URL).toBe('/api/discover/seasonal/current');
    expect(seasonalPlaylistUrl('winter')).toBe('/api/discover/seasonal/winter/playlist');
  });
});

describe('the headers', () => {
  it('puts the icon before the name', () => {
    expect(seasonalHeader({ icon: '❄️', name: 'Winter', description: 'Cold picks' })).toEqual({
      title: '❄️ Winter',
      subtitle: 'Cold picks',
    });
  });

  it('LOWERCASES the season name in the mix subtitle only', () => {
    const t = seasonalMixTitles({ icon: '❄️', name: 'Winter' });
    expect(t.title).toBe('❄️ Winter Mix');
    expect(t.subtitle).toBe('Curated playlist for winter');
  });

  it('survives a season with no icon or description', () => {
    expect(seasonalHeader({ name: 'Winter' })).toEqual({ title: ' Winter', subtitle: '' });
  });
});

describe('the albums half', () => {
  it('runs in no-fetch data mode, reusing the payload already in hand', () => {
    // loadSeasonalContent already fetched it; a second request would ask for
    // data the caller is holding.
    expect(seasonalAlbumsData({ albums: [{ album_name: 'A' }] })).toEqual({
      success: true,
      albums: [{ album_name: 'A' }],
    });
  });

  it('defaults to an empty album list rather than undefined', () => {
    expect(seasonalAlbumsData({})).toEqual({ success: true, albums: [] });
    expect(seasonalAlbumsData(null)).toEqual({ success: true, albums: [] });
  });

  it('falls back to the placeholder cover', () => {
    expect(seasonalAlbumCover({ album_cover_url: '/a.jpg' })).toBe('/a.jpg');
    expect(seasonalAlbumCover({})).toBe(SEASONAL_ALBUM_PLACEHOLDER);
  });

  it('keeps the copy', () => {
    expect(SEASONAL_ALBUMS_EMPTY).toBe('No seasonal albums found');
    expect(SEASONAL_ALBUMS_ERROR).toBe('Failed to load seasonal albums');
    expect(SEASONAL_PLAYLIST_EMPTY).toBe('No tracks available yet');
    expect(SEASONAL_NO_ALBUM_ID).toBe('No album ID available');
    expect(SEASONAL_NO_TRACKS).toBe('No tracks found in album');
  });
});

describe('guessing an album’s source', () => {
  it('treats an ALL-DIGIT spotify_album_id as an iTunes id', () => {
    // Spotify ids are base62; a purely numeric value in that column is an
    // iTunes id stored in the same field.
    expect(seasonalAlbumSource({ spotify_album_id: '1440857781' })).toBe('itunes');
    expect(seasonalAlbumSource({ spotify_album_id: '4aawyAB79vO3zipR' })).toBe('spotify');
  });

  it('routes a MIXED id to spotify', () => {
    expect(seasonalAlbumSource({ spotify_album_id: '123abc' })).toBe('spotify');
  });

  it('falls back to itunes with no id at all', () => {
    expect(seasonalAlbumSource({})).toBe('itunes');
  });

  it('lets an explicit source override the guess entirely', () => {
    expect(seasonalAlbumSource({ source: 'deezer', spotify_album_id: '123' })).toBe('deezer');
    expect(seasonalAlbumSource({ source: 'deezer', spotify_album_id: 'abc' })).toBe('deezer');
  });

  it('prefixes the virtual id', () => {
    expect(seasonalVirtualAlbumId('a1')).toBe('seasonal_album_a1');
  });
});

describe('the playlist controller', () => {
  it('is REBUILT when the season key changes', () => {
    // Its fetchUrl bakes in the season, so a cached one keeps asking the old
    // season's endpoint after a rollover.
    expect(seasonalControllerIsStale('winter', 'spring')).toBe(true);
    expect(seasonalControllerIsStale('winter', 'winter')).toBe(false);
    expect(seasonalControllerIsStale(null, 'winter')).toBe(true);
  });
});

describe('the artist context (behaviour beyond the differential cases)', () => {
  it('spreads unknown source fields through', () => {
    const ctx = buildDiscoverArtistContext('spotify', 'A', { custom: 'keep' });
    expect(ctx.custom).toBe('keep');
  });

  it('overwrites the generic id with the ACTIVE provider’s own', () => {
    // This second pass is what makes the modal open on the right provider.
    const ctx = buildDiscoverArtistContext('spotify', 'A', {
      active_source_id: 'generic',
      spotify_artist_id: 'sp9',
    });
    expect(ctx.id).toBe('sp9');
  });

  it('keeps the generic id when the active source has none', () => {
    const ctx = buildDiscoverArtistContext('spotify', 'A', { active_source_id: 'generic' });
    expect(ctx.id).toBe('generic');
  });

  it('fills only the ACTIVE provider from the album artist', () => {
    const album = { artists: [{ id: 'alb1', name: 'Album Artist' }] };
    expect(buildDiscoverArtistContext('spotify', '', {}, album).spotify_artist_id).toBe('alb1');
    expect(buildDiscoverArtistContext('deezer', '', {}, album).spotify_artist_id).toBe('');
  });

  it('lowercases the source', () => {
    expect(buildDiscoverArtistContext('SPOTIFY', 'A').source).toBe('spotify');
  });

  it('accepts every id alias the endpoints spell differently', () => {
    expect(buildDiscoverArtistContext('deezer', 'A', { deezer_id: 'd' }).deezer_artist_id).toBe(
      'd',
    );
    expect(
      buildDiscoverArtistContext('deezer', 'A', { artist_deezer_id: 'd2' }).deezer_artist_id,
    ).toBe('d2');
    expect(buildDiscoverArtistContext('hydrabase', 'A', { hydrabase_artist_id: 'h' }).soul_id).toBe(
      'h',
    );
  });

  it('never yields undefined for a provider id', () => {
    const ctx = buildDiscoverArtistContext('', '');
    for (const key of [
      'spotify_artist_id',
      'itunes_artist_id',
      'deezer_artist_id',
      'discogs_artist_id',
      'amazon_artist_id',
      'soul_id',
      'id',
      'name',
      'source',
    ]) {
      expect(ctx[key]).toBe('');
    }
  });
});
