import { describe, expect, it } from 'vitest';

import {
  RECENT_ALBUM_PLACEHOLDER,
  RECENT_NO_TRACKS,
  RECENT_RELEASES_EMPTY,
  RECENT_RELEASES_ERROR,
  RECENT_RELEASES_LOADING,
  RECENT_RELEASES_URL,
  recentAlbumCover,
  recentAlbumFetchUrl,
  recentAlbumId,
  recentAlbumSource,
  recentNoIdMessage,
  recentTrackAlbum,
  recentTrackArtists,
  recentVirtualAlbumId,
} from './-discover.recent-releases';

describe('the card', () => {
  it('falls back to the placeholder cover', () => {
    expect(recentAlbumCover({ album_cover_url: '/a.jpg' })).toBe('/a.jpg');
    expect(recentAlbumCover({})).toBe(RECENT_ALBUM_PLACEHOLDER);
  });

  it('keeps the section copy', () => {
    expect(RECENT_RELEASES_URL).toBe('/api/discover/recent-releases');
    expect(RECENT_RELEASES_LOADING).toBe('Loading recent releases...');
    expect(RECENT_RELEASES_EMPTY).toBe('No recent releases found');
    expect(RECENT_RELEASES_ERROR).toBe('Failed to load recent releases');
    expect(RECENT_NO_TRACKS).toBe('No tracks found in album');
  });
});

describe('picking the source and id', () => {
  it('prefers the first populated id — spotify, deezer, itunes', () => {
    expect(recentAlbumSource({ album_spotify_id: 's' })).toBe('spotify');
    expect(recentAlbumSource({ album_deezer_id: 'd' })).toBe('deezer');
    expect(recentAlbumSource({ album_itunes_id: 'i' })).toBe('itunes');
    expect(recentAlbumSource({ album_deezer_id: 'd', album_spotify_id: 's' })).toBe('spotify');
  });

  it('falls back to itunes with no ids at all', () => {
    expect(recentAlbumSource({})).toBe('itunes');
  });

  it('lets an explicit source override the pick', () => {
    expect(recentAlbumSource({ source: 'deezer', album_spotify_id: 's' })).toBe('deezer');
  });

  it('reads the id field that matches the SOURCE, which can disagree', () => {
    // An explicit source: 'deezer' on a row with only a spotify id yields no id
    // at all — the vanilla then throws "No deezer album ID available".
    // Transcribed as-is rather than made lenient.
    const album = { source: 'deezer', album_spotify_id: 's' };
    expect(recentAlbumId(album, recentAlbumSource(album))).toBeUndefined();
  });

  it('reads the right id for each source', () => {
    const album = { album_spotify_id: 's', album_deezer_id: 'd', album_itunes_id: 'i' };
    expect(recentAlbumId(album, 'spotify')).toBe('s');
    expect(recentAlbumId(album, 'deezer')).toBe('d');
    expect(recentAlbumId(album, 'itunes')).toBe('i');
    expect(recentAlbumId(album, 'discogs')).toBe('i'); //  anything else reads itunes
  });

  it('names the failing source in the error', () => {
    expect(recentNoIdMessage('deezer')).toBe('No deezer album ID available');
  });

  it('uses a DIFFERENT heuristic from seasonal, deliberately', () => {
    // Seasonal inspects whether spotify_album_id looks numeric because it has
    // only that one column; Recent Releases has three and picks the first
    // populated one.
    expect(recentAlbumSource({ album_spotify_id: '1440857781' })).toBe('spotify');
  });
});

describe('the request', () => {
  it('passes name and artist for Hydrabase resolution', () => {
    expect(
      recentAlbumFetchUrl('spotify', 'a1', { album_name: 'SAW', artist_name: 'Aphex Twin' }),
    ).toBe('/api/discover/album/spotify/a1?name=SAW&artist=Aphex+Twin');
  });

  it('sends empty strings rather than "undefined"', () => {
    expect(recentAlbumFetchUrl('itunes', 'a1', {})).toBe(
      '/api/discover/album/itunes/a1?name=&artist=',
    );
  });

  it('prefixes the virtual id the download bar keys on', () => {
    expect(recentVirtualAlbumId('a1')).toBe('discover_album_a1');
  });
});

describe('rebuilding the album envelope', () => {
  it('uses the FRESH response, not the card’s cached row', () => {
    // The cached row carries stale art and no album_type, and the download
    // modal classifies on album_type — this is what makes an album download
    // behave like an album rather than a loose track set.
    expect(
      recentTrackAlbum({
        id: 'a1',
        name: 'Fresh Name',
        album_type: 'single',
        total_tracks: 3,
        release_date: '2026-01-01',
        images: [{ url: '/fresh.jpg' }],
      }),
    ).toEqual({
      id: 'a1',
      name: 'Fresh Name',
      album_type: 'single',
      total_tracks: 3,
      release_date: '2026-01-01',
      images: [{ url: '/fresh.jpg' }],
    });
  });

  it('defaults album_type to "album", not to empty', () => {
    expect(recentTrackAlbum({}).album_type).toBe('album');
  });

  it('defaults the rest without leaving undefined', () => {
    const a = recentTrackAlbum({});
    expect([a.total_tracks, a.release_date, a.images]).toEqual([0, '', []]);
  });
});

describe('track artists', () => {
  it('prefers the track’s own, then the album’s, then the card row', () => {
    const album = { artist_name: 'Row Artist' };
    expect(
      recentTrackArtists({ artists: [{ name: 'Track' }] }, { artists: [{ name: 'Album' }] }, album),
    ).toEqual(['Track']);
    expect(recentTrackArtists({}, { artists: [{ name: 'Album' }] }, album)).toEqual(['Album']);
    expect(recentTrackArtists({}, {}, album)).toEqual(['Row Artist']);
  });

  it('passes plain strings through rather than blanking them', () => {
    expect(recentTrackArtists({ artists: ['Plain'] }, {}, {})).toEqual(['Plain']);
  });

  it('leaves a non-array artists value alone', () => {
    expect(recentTrackArtists({ artists: 'nope' }, {}, {})).toBe('nope');
  });
});
