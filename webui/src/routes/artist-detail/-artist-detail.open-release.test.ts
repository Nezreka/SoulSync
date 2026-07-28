import { describe, expect, it } from 'vitest';

import {
  albumTracksParams,
  isReleaseClickable,
  openReleaseArtist,
  releaseToAlbumData,
  stillCheckingMessage,
} from './-artist-detail.open-release';

describe('isReleaseClickable', () => {
  it('is inert only while ownership is unresolved', () => {
    expect(isReleaseClickable({ owned: null })).toBe(false);
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
