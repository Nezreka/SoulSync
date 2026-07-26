import { describe, expect, it } from 'vitest';

import { pickDiscographySource } from './-ui/watchlist-artist-detail';

const NONE = {
  spotify_artist_id: null,
  itunes_artist_id: null,
  deezer_artist_id: null,
  discogs_artist_id: null,
  musicbrainz_artist_id: null,
  global_metadata_source: '',
};

describe('pickDiscographySource', () => {
  it('prefers the id belonging to the active source', () => {
    const all = {
      spotify_artist_id: 'sp',
      itunes_artist_id: 'it',
      deezer_artist_id: 'dz',
      discogs_artist_id: 'dc',
      musicbrainz_artist_id: 'mb',
    };
    expect(pickDiscographySource({ ...all, global_metadata_source: 'spotify' })).toEqual({
      id: 'sp',
      source: 'spotify',
    });
    expect(pickDiscographySource({ ...all, global_metadata_source: 'deezer' })).toEqual({
      id: 'dz',
      source: 'deezer',
    });
    expect(pickDiscographySource({ ...all, global_metadata_source: 'discogs' })).toEqual({
      id: 'dc',
      source: 'discogs',
    });
    expect(pickDiscographySource({ ...all, global_metadata_source: 'musicbrainz' })).toEqual({
      id: 'mb',
      source: 'musicbrainz',
    });
  });

  it('falls through when the active source has no match for this artist', () => {
    // Active source is Spotify but the artist was never matched there, so the
    // link must still go somewhere useful rather than nowhere.
    expect(
      pickDiscographySource({
        ...NONE,
        deezer_artist_id: 'dz',
        global_metadata_source: 'spotify',
      }),
    ).toEqual({ id: 'dz', source: 'deezer' });
  });

  it('prefers iTunes over the generic fallback chain', () => {
    // The vanilla ladder checks iTunes before the catch-all, so an artist with
    // both iTunes and Discogs ids but a non-matching active source goes to
    // iTunes.
    expect(
      pickDiscographySource({
        ...NONE,
        itunes_artist_id: 'it',
        discogs_artist_id: 'dc',
        global_metadata_source: 'spotify',
      }),
    ).toEqual({ id: 'it', source: 'itunes' });
  });

  it('falls back through spotify, discogs, deezer, musicbrainz in order', () => {
    expect(
      pickDiscographySource({ ...NONE, spotify_artist_id: 'sp', discogs_artist_id: 'dc' }),
    ).toEqual({ id: 'sp', source: 'spotify' });
    expect(
      pickDiscographySource({ ...NONE, discogs_artist_id: 'dc', deezer_artist_id: 'dz' }),
    ).toEqual({ id: 'dc', source: 'discogs' });
    expect(
      pickDiscographySource({ ...NONE, deezer_artist_id: 'dz', musicbrainz_artist_id: 'mb' }),
    ).toEqual({ id: 'dz', source: 'deezer' });
    expect(pickDiscographySource({ ...NONE, musicbrainz_artist_id: 'mb' })).toEqual({
      id: 'mb',
      source: 'musicbrainz',
    });
  });

  it('is null for an artist with no provider ids at all', () => {
    // The button is disabled in that case rather than linking to '#'.
    expect(pickDiscographySource(NONE)).toBeNull();
  });

  it('tolerates a missing or unknown active source', () => {
    expect(pickDiscographySource({ ...NONE, spotify_artist_id: 'sp' })).toEqual({
      id: 'sp',
      source: 'spotify',
    });
    expect(
      pickDiscographySource({
        ...NONE,
        spotify_artist_id: 'sp',
        global_metadata_source: 'bandcamp',
      }),
    ).toEqual({ id: 'sp', source: 'spotify' });
  });
});
