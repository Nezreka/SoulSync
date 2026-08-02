import { describe, expect, it } from 'vitest';

import {
  DECADES_AVAILABLE_URL,
  DECADE_DOWNLOAD_KEEPS_ARTIST_OBJECTS,
  DECADE_NO_TRACKS,
  decadeButtonId,
  decadeClassicsName,
  decadeCompletedId,
  decadeDownloadPlaylistId,
  decadeHasTracks,
  decadeMix,
  decadeMixKey,
  decadePollerId,
  decadeShelfHasContent,
  decadeStatusBase,
  decadeStatusId,
  decadeSyncCompleteToast,
  decadeSyncPlaylistId,
  decadeTrackToSpotify,
  decadeTracksUrl,
} from './-discover.decade-shelf';

describe('the shelf', () => {
  it('needs success AND at least one decade', () => {
    expect(decadeShelfHasContent({ success: true, decades: [{ year: 1980 }] })).toBe(true);
    expect(decadeShelfHasContent({ success: true, decades: [] })).toBe(false);
    expect(decadeShelfHasContent({ success: false, decades: [{ year: 1980 }] })).toBe(false);
    expect(decadeShelfHasContent({ success: true })).toBe(false);
    expect(decadeShelfHasContent(null)).toBe(false);
  });

  it('builds its urls', () => {
    expect(DECADES_AVAILABLE_URL).toBe('/api/discover/decades/available');
    expect(decadeTracksUrl(1980)).toBe('/api/discover/decade/1980');
  });

  it('titles the card with the bare decade and subtitles it Classics', () => {
    const mix = decadeMix({ year: 1980, track_count: 42 });
    expect(mix.title).toBe('1980s');
    expect(mix.subtitle).toBe('1980s Classics');
    expect(mix.trackCount).toBe(42);
  });

  it('gives the card a Download and a Sync action', () => {
    const actions = decadeMix({ year: 1980 }).actions ?? [];
    expect(actions.map((a) => a.label)).toEqual(['Download', 'Sync']);
    expect(actions[0].closeFirst).toBe(true);
    expect(actions[1].isSync).toBe(true);
  });

  it('has NO syncKey — it drives the sync through its own actions', () => {
    // A syncKey would make the shared mix modal build a Download/Sync pair
    // pointing at the generic playlist endpoints instead of the decade ones.
    expect(decadeMix({ year: 1980 }).syncKey).toBeUndefined();
  });
});

describe('the three id conventions, which are NOT interchangeable', () => {
  it('keys the mix and the download the same, but the SYNC differently', () => {
    // startPlaylistSync writes into playlistTrackCache under the sync id;
    // unifying these changes which cache the sync fills.
    expect(decadeMixKey(1980)).toBe('decade_1980');
    expect(decadeDownloadPlaylistId(1980)).toBe('decade_1980');
    expect(decadeSyncPlaylistId(1980)).toBe('discover_decade_1980');
    expect(decadeSyncPlaylistId(1980)).not.toBe(decadeDownloadPlaylistId(1980));
  });

  it('keys the poller by the SHORT id, sharing one registry with playlist syncs', () => {
    expect(decadePollerId(1980)).toBe('decade_1980');
  });

  it('hyphenates only the DOM ids', () => {
    expect(decadeStatusBase(1980)).toBe('decade-1980');
    expect(decadeStatusId(1980)).toBe('decade-1980-sync-status');
    expect(decadeButtonId(1980)).toBe('decade-1980-sync-btn');
    expect(decadeCompletedId(1980)).toBe('decade-1980-sync-completed');
  });

  it('names the playlist and the toast consistently', () => {
    expect(decadeClassicsName(1980)).toBe('1980s Classics');
    expect(decadeSyncCompleteToast(1980)).toBe('1980s Classics sync complete!');
  });
});

describe('the decade track conversion', () => {
  const flat = {
    spotify_track_id: 't1',
    track_name: 'Take On Me',
    artist_name: 'a-ha',
    album_name: 'Hunting High and Low',
    album_cover_url: '/c.jpg',
    duration_ms: 225000,
  };

  it('builds from the flat columns when there is no json', () => {
    expect(decadeTrackToSpotify(flat, true)).toEqual({
      id: 't1',
      name: 'Take On Me',
      artists: ['a-ha'],
      album: { name: 'Hunting High and Low', images: [{ url: '/c.jpg' }] },
      duration_ms: 225000,
    });
  });

  it('MERGES field by field rather than taking track_data_json whole', () => {
    // This is the difference from the playlist sync's conversion, which uses
    // track_data_json WHOLE. Here a partial json takes the id and every other
    // field still falls back to the flat row — `name` resolves through
    // trackData.name → trackData.track_name → track.track_name (2736).
    const out = decadeTrackToSpotify({ ...flat, track_data_json: { id: 'json-id' } }, true);
    expect(out.id).toBe('json-id');
    expect(out.name).toBe('Take On Me');
    expect(out.album).toEqual({ name: 'Hunting High and Low', images: [{ url: '/c.jpg' }] });
    expect(out.duration_ms).toBe(225000);
  });

  it('prefers each json field, falling back per field', () => {
    const out = decadeTrackToSpotify(
      { ...flat, track_data_json: { name: 'JSON Name', duration_ms: 1000 } },
      true,
    );
    expect(out.name).toBe('JSON Name');
    expect(out.duration_ms).toBe(1000);
  });

  it('wraps a bare artist name into the spotify shape', () => {
    expect(decadeTrackToSpotify({ artist_name: 'a-ha' }, false).artists).toEqual([
      { name: 'a-ha' },
    ]);
  });

  it('gives an art-less track an EMPTY image array', () => {
    expect(decadeTrackToSpotify({ track_name: 'x' }, true).album?.images).toEqual([]);
  });

  it('defaults a missing duration to zero', () => {
    expect(decadeTrackToSpotify({ track_name: 'x' }, true).duration_ms).toBe(0);
  });

  it('FLATTENS artists for the sync path', () => {
    expect(
      decadeTrackToSpotify({ track_data_json: { artists: [{ name: 'A' }] } }, true).artists,
    ).toEqual(['A']);
  });

  it('KEEPS artist objects for the download path — a real asymmetry', () => {
    // startDecadeSync flattens (2746); openDownloadModalForDecade does not
    // (2897). Recorded rather than unified: I have not verified what the
    // download modal does with an object array, and changing it belongs with
    // that verification.
    expect(DECADE_DOWNLOAD_KEEPS_ARTIST_OBJECTS).toBe(true);
    expect(
      decadeTrackToSpotify({ track_data_json: { artists: [{ name: 'A' }] } }, false).artists,
    ).toEqual([{ name: 'A' }]);
  });

  it('leaves an already-flat artist list alone', () => {
    expect(decadeTrackToSpotify({ track_data_json: { artists: ['A'] } }, true).artists).toEqual([
      'A',
    ]);
  });

  it('takes the json album whole when it has one', () => {
    const out = decadeTrackToSpotify(
      { ...flat, track_data_json: { album: { name: 'JSON Album', images: [{ url: '/j.jpg' }] } } },
      true,
    );
    expect(out.album).toEqual({ name: 'JSON Album', images: [{ url: '/j.jpg' }] });
  });
});

describe('both actions refuse an empty cache', () => {
  it('shares one warning', () => {
    expect(decadeHasTracks([{}])).toBe(true);
    expect(decadeHasTracks([])).toBe(false);
    expect(decadeHasTracks(null)).toBe(false);
    expect(DECADE_NO_TRACKS).toBe('No tracks available for this decade');
  });
});
