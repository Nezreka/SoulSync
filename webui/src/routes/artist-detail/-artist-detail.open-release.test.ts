import { describe, expect, it } from 'vitest';

import {
  albumTracksParams,
  isReleaseClickable,
  lockSectionType,
  openReleaseArtist,
  reconcileAlbumWithTracksResponse,
  releaseSectionType,
  releaseToAlbumData,
  stillCheckingMessage,
} from './-artist-detail.open-release';

describe('isReleaseClickable', () => {
  it('allows opening releases even while ownership is unresolved', () => {
    expect(isReleaseClickable({ owned: null })).toBe(true);
    expect(isReleaseClickable({ owned: true })).toBe(true);
    expect(isReleaseClickable({ owned: false })).toBe(true);
    // A release with no ownership field at all is still clickable.
    expect(isReleaseClickable({})).toBe(true);
  });

  it('names the release in the still-checking toast', () => {
    expect(stillCheckingMessage({ title: 'Kid A' })).toBe('Still checking ownership for Kid A...');
  });
});

describe('openReleaseArtist', () => {
  it('uses the CURRENT artist id, not the one in the response', () => {
    // loadArtistDetailData's library-upgrade branch can rewrite the id after
    // the fetch; the modal must act on the upgraded one.
    const artist = openReleaseArtist(
      { artist: { id: 'source-9', name: 'Aphex Twin' } },
      42,
      'a.jpg',
    );
    expect(artist).toEqual({ id: 42, name: 'Aphex Twin', image_url: 'a.jpg', source: null });
  });

  it('prefers the discography source over the artist source', () => {
    const artist = openReleaseArtist(
      { artist: { name: 'X', source: 'itunes' }, discography: { source: 'musicbrainz' } },
      1,
      '',
    );
    expect(artist?.source).toBe('musicbrainz');
  });

  it('returns null without a name — the vanilla refused to open the modal', () => {
    expect(openReleaseArtist({ artist: { id: 1 } }, 1, '')).toBeNull();
    expect(openReleaseArtist({}, 1, '')).toBeNull();
  });

  it('falls back to an empty image rather than undefined', () => {
    expect(openReleaseArtist({ artist: { name: 'X' } }, 1, '')?.image_url).toBe('');
  });
});

describe('releaseToAlbumData', () => {
  it('takes total_tracks from the object form of track_completion', () => {
    const album = releaseToAlbumData({
      id: 1,
      title: 'Kid A',
      track_completion: { total_tracks: 10, owned_tracks: 3 },
    });
    expect(album.total_tracks).toBe(10);
  });

  it('falls back to track_count, then to 1 — never 0', () => {
    // The modal treats a zero-track album as empty and refuses to open.
    expect(releaseToAlbumData({ id: 1, track_count: 7 }).total_tracks).toBe(7);
    expect(releaseToAlbumData({ id: 1 }).total_tracks).toBe(1);
    expect(releaseToAlbumData({ id: 1, track_count: 0 }).total_tracks).toBe(1);
  });

  it('synthesises a release_date from the year, and empty without one', () => {
    expect(releaseToAlbumData({ id: 1, year: 1994 }).release_date).toBe('1994-01-01');
    expect(releaseToAlbumData({ id: 1 }).release_date).toBe('');
  });

  it('defaults album_type through type, then album', () => {
    expect(releaseToAlbumData({ id: 1, album_type: 'single' }).album_type).toBe('single');
    expect(releaseToAlbumData({ id: 1, type: 'ep' }).album_type).toBe('ep');
    expect(releaseToAlbumData({ id: 1 }).album_type).toBe('album');
  });
});

describe('reconcileAlbumWithTracksResponse', () => {
  // Collision Course (Deezer): the artist-albums endpoint omits nb_tracks,
  // so the release card fabricates total_tracks=1 while correctly carrying
  // album_type='ep'. The /api/album/<id>/tracks fetch returns the real
  // release (total_tracks=6). Without reconciliation the modal POSTs the
  // fabricated count and get_album_type_display('ep', 1) files it as Single.
  const cardAlbum = releaseToAlbumData({ id: 81827, title: 'Collision Course', album_type: 'ep' });
  const sixTracks = Array.from({ length: 6 }, (_, i) => ({ id: i + 1 }));

  it('takes the real type/count from the tracks response', () => {
    expect(cardAlbum.total_tracks).toBe(1); // the fabricated card count
    const album = reconcileAlbumWithTracksResponse(cardAlbum, {
      album: { album_type: 'ep', total_tracks: 6 },
      tracks: sixTracks,
    });
    expect(album.album_type).toBe('ep');
    expect(album.total_tracks).toBe(6);
    expect(album.id).toBe(81827);
    expect(album.name).toBe('Collision Course');
  });

  it('falls back to the tracklist length when the album payload has no count', () => {
    const album = reconcileAlbumWithTracksResponse(cardAlbum, {
      album: { album_type: 'ep', total_tracks: 0 },
      tracks: sixTracks,
    });
    expect(album.total_tracks).toBe(6);
  });

  it('falls back to the tracklist length when the album payload is absent', () => {
    const album = reconcileAlbumWithTracksResponse(cardAlbum, { tracks: sixTracks });
    expect(album.total_tracks).toBe(6);
    expect(album.album_type).toBe('ep');
  });

  it('keeps the card values when the response carries nothing usable', () => {
    const album = reconcileAlbumWithTracksResponse(cardAlbum, {});
    expect(album.total_tracks).toBe(1);
    expect(album.album_type).toBe('ep');
  });

  it('prefers the response album_type over the card', () => {
    const album = reconcileAlbumWithTracksResponse(
      { ...cardAlbum, album_type: 'album' },
      { album: { album_type: 'single', total_tracks: 2 }, tracks: [{}, {}] },
    );
    expect(album.album_type).toBe('single');
    expect(album.total_tracks).toBe(2);
  });
});

describe('albumTracksParams', () => {
  const artist = { id: 1, name: 'Aphex Twin', image_url: '', source: 'spotify' };

  it('sends the album name and artist for Hydrabase lookups', () => {
    expect(albumTracksParams({ title: 'Kid A' }, artist)).toEqual({
      name: 'Kid A',
      artist: 'Aphex Twin',
      source: 'spotify',
    });
  });

  it("uses a gap-fill card's OWN source, overriding the artist's", () => {
    // #1067: the card belongs to another source; querying the artist's source
    // returns no tracks at all.
    const params = albumTracksParams({ title: 'X', _gap_source: 'deezer' }, artist);
    expect(params.source).toBe('deezer');
  });

  it('omits source entirely when the artist has none', () => {
    const params = albumTracksParams({ title: 'X' }, { ...artist, source: null });
    expect('source' in params).toBe(false);
  });

  it('still sets a gap source when the artist has none', () => {
    const params = albumTracksParams(
      { title: 'X', _gap_source: 'qobuz' },
      { ...artist, source: null },
    );
    expect(params.source).toBe('qobuz');
  });
});

describe('releaseSectionType', () => {
  it('is the section the page shows the release in', () => {
    expect(releaseSectionType({ album_type: 'EP' })).toBe('ep');
    expect(releaseSectionType({ album_type: 'single' })).toBe('single');
    expect(releaseSectionType({ album_type: 'compile' })).toBe('compilation');
    expect(releaseSectionType({ album_type: 'album' })).toBe('album');
    // anything else sits under Albums, like the backend buckets it
    expect(releaseSectionType({ album_type: 'appears_on' })).toBe('album');
    expect(releaseSectionType({ type: 'single' })).toBe('single');
    expect(releaseSectionType({})).toBe('album');
  });
});

describe('lockSectionType', () => {
  it('pins the type to the section and marks it locked, whatever the fetch said', () => {
    // discord: Deezer's 3-track album Flow State Sampler, shown under Albums
    const fetched = { id: 7, name: 'Flow State Sampler', album_type: 'single', total_tracks: 3 };
    expect(lockSectionType(fetched, { album_type: 'album' })).toEqual({
      ...fetched,
      album_type: 'album',
      album_type_locked: true,
    });
  });
});
