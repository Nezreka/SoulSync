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

  // ── availability (the Discord report: watchlist 503s, Discover works) ──
  // The artist page treats a pinned source as EXCLUSIVE and errors when it
  // can't serve, so pinning a switched-off provider is a guaranteed
  // 'Could not access spotify ... provider is unavailable'. The server now
  // sends which providers are alive; the ladder must respect it.

  it('never pins a provider the user has switched off', () => {
    // Lancor: Spotify auth disconnected AND no-auth unchecked, running
    // Deezer — but this artist was only ever matched on Spotify.
    expect(
      pickDiscographySource({
        ...NONE,
        spotify_artist_id: 'sp',
        global_metadata_source: 'deezer',
        available_sources: ['deezer', 'itunes', 'musicbrainz'],
      }),
    ).toBeNull();   // button disables instead of navigating into a 503
  });

  it('falls to the next AVAILABLE provider that has an id', () => {
    expect(
      pickDiscographySource({
        ...NONE,
        spotify_artist_id: 'sp',
        deezer_artist_id: 'dz',
        global_metadata_source: 'deezer',
        available_sources: ['deezer', 'itunes'],
      }),
    ).toEqual({ id: 'dz', source: 'deezer' });
  });

  it('still honours the active source when it IS available', () => {
    expect(
      pickDiscographySource({
        ...NONE,
        spotify_artist_id: 'sp',
        deezer_artist_id: 'dz',
        global_metadata_source: 'spotify',
        available_sources: ['spotify', 'deezer'],
      }),
    ).toEqual({ id: 'sp', source: 'spotify' });
  });

  it('treats an absent availability list as everything eligible', () => {
    // Older backend / callers that don't fetch it: byte-for-byte the
    // previous behaviour, which every test above this pins.
    expect(pickDiscographySource({ ...NONE, spotify_artist_id: 'sp' })).toEqual({
      id: 'sp',
      source: 'spotify',
    });
  });
});
