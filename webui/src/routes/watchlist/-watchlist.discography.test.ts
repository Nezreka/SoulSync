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
  // A pinned source is safe iff its provider is alive (available_sources)
  // OR its id resolves to a library artist (library_resolvable_sources —
  // the artist page upgrades off the id column with no provider call).
  // Anything else navigates into 'Could not access spotify ... provider is
  // unavailable'. Both lists come from the server; with neither present
  // (older backend) every source stays eligible.

  it('never pins a dead provider whose id is not in the library (Lancor)', () => {
    // Spotify auth disconnected AND no-auth unchecked, running Deezer; the
    // artist was only ever matched on Spotify and is not owned. The old
    // ladder pinned spotify → guaranteed 503. Now the button disables and
    // its tooltip names the fix (re-match via provider links).
    expect(
      pickDiscographySource({
        ...NONE,
        spotify_artist_id: 'sp',
        global_metadata_source: 'deezer',
        available_sources: ['deezer', 'itunes', 'musicbrainz'],
        library_resolvable_sources: [],
      }),
    ).toBeNull();
  });

  it('still links a dead provider when its id resolves in the library', () => {
    // Owned artists render the rich library view with no provider call, so
    // a dead-source id that the server vouches for stays a valid link.
    expect(
      pickDiscographySource({
        ...NONE,
        spotify_artist_id: 'sp',
        global_metadata_source: 'deezer',
        available_sources: ['deezer', 'itunes', 'musicbrainz'],
        library_resolvable_sources: ['spotify'],
      }),
    ).toEqual({ id: 'sp', source: 'spotify' });
  });

  it('falls to the next SAFE provider that has an id', () => {
    expect(
      pickDiscographySource({
        ...NONE,
        spotify_artist_id: 'sp',
        deezer_artist_id: 'dz',
        global_metadata_source: 'deezer',
        available_sources: ['deezer', 'itunes'],
        library_resolvable_sources: [],
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
        library_resolvable_sources: [],
      }),
    ).toEqual({ id: 'sp', source: 'spotify' });
  });

  it('treats absent server knowledge as everything eligible', () => {
    // Older backend: byte-for-byte the previous behaviour, which every
    // test above this block pins.
    expect(pickDiscographySource({ ...NONE, spotify_artist_id: 'sp' })).toEqual({
      id: 'sp',
      source: 'spotify',
    });
  });
});
