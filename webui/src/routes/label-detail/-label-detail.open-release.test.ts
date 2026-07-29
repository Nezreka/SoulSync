import { describe, expect, it } from 'vitest';

import {
  buildAlbumObject,
  buildArtistObject,
  isUsableAlbumImage,
  pickResolvedAlbum,
  releaseModalIdentity,
} from './-label-detail.open-release';

const release = {
  album: 'Drukqs',
  artist: 'Aphex Twin',
  artist_id: 'mb-artist',
  year: '2001',
  primary_type: 'album',
};

describe('pickResolvedAlbum', () => {
  it('prefers an exact artist AND title match', () => {
    const albums = [
      { id: '1', name: 'Drukqs', artist: 'Someone Else' },
      { id: '2', name: 'Drukqs', artist: 'Aphex Twin' },
    ];
    expect(pickResolvedAlbum(albums, release)?.id).toBe('2');
  });

  it('falls back to a title-only match', () => {
    const albums = [
      { id: '1', name: 'Other', artist: 'Aphex Twin' },
      { id: '2', name: 'Drukqs', artist: 'AFX' },
    ];
    expect(pickResolvedAlbum(albums, release)?.id).toBe('2');
  });

  it('takes the first result rather than giving up', () => {
    const albums = [{ id: '1', name: 'Nothing Alike', artist: 'Nobody' }];
    expect(pickResolvedAlbum(albums, release)?.id).toBe('1');
  });

  it('matches across punctuation differences between catalogues', () => {
    // The loose normalisation belongs HERE (and would be wrong for the
    // ownership key): two sources punctuate the same album differently.
    const albums = [{ id: '9', name: 'Drukqs!!', artist: 'Aphex  Twin' }];
    expect(pickResolvedAlbum(albums, release)?.id).toBe('9');
  });

  it('is undefined when there is nothing at all', () => {
    expect(pickResolvedAlbum([], release)).toBeUndefined();
  });
});

describe('isUsableAlbumImage', () => {
  it('rejects Cover Art Archive, which cannot load in the browser', () => {
    // Worse than no image: it renders broken AND gets stored on the wishlist.
    expect(isUsableAlbumImage('https://coverartarchive.org/release/x/front')).toBe(false);
  });

  it('accepts a normal CDN url and rejects nothing at all', () => {
    expect(isUsableAlbumImage('https://cdn.example/x.jpg')).toBe(true);
    expect(isUsableAlbumImage('')).toBe(false);
    expect(isUsableAlbumImage(undefined)).toBe(false);
  });
});

describe('buildAlbumObject', () => {
  it('uses the resolved source images when they are usable', () => {
    const album = buildAlbumObject(
      {
        name: 'Drukqs',
        images: [{ url: 'https://cdn.example/a.jpg' }],
        tracks: [{}, {}],
        release_date: '2001-10-22',
      },
      release,
      'sp-1',
      'spotify',
      'https://fallback/x.jpg',
    );
    expect(album.image_url).toBe('https://cdn.example/a.jpg');
    expect(album.total_tracks).toBe(2);
    expect(album.release_date).toBe('2001-10-22');
  });

  it('swaps in the fallback when the detail image is Cover Art Archive', () => {
    const album = buildAlbumObject(
      { images: [{ url: 'https://coverartarchive.org/x' }], tracks: [] },
      release,
      'mb-1',
      'musicbrainz',
      'https://fallback/x.jpg',
    );
    expect(album.image_url).toBe('https://fallback/x.jpg');
    expect(album.images).toEqual([{ url: 'https://fallback/x.jpg' }]);
  });

  it('widens a bare year into a parseable date', () => {
    // The catalog only has a year; the modal and the wishlist entry both want
    // something a date parser will accept.
    const album = buildAlbumObject({ tracks: [] }, release, 'mb-1', 'musicbrainz', '');
    expect(album.release_date).toBe('2001-01-01');
  });

  it('leaves the date empty when there is not even a year', () => {
    const album = buildAlbumObject({ tracks: [] }, { ...release, year: undefined }, 'i', 's', '');
    expect(album.release_date).toBe('');
  });

  it('carries no images at all rather than an empty-url entry', () => {
    const album = buildAlbumObject({ tracks: [] }, release, 'mb-1', 'musicbrainz', '');
    expect(album.images).toEqual([]);
    expect(album.image_url).toBe('');
  });

  it('falls back to the catalog name and type', () => {
    const album = buildAlbumObject({ tracks: [] }, release, 'mb-1', 'musicbrainz', '');
    expect(album.name).toBe('Drukqs');
    expect(album.album_type).toBe('album');
  });
});

describe('buildArtistObject', () => {
  it('prefers the resolved source identity', () => {
    const artist = buildArtistObject(
      { artists: [{ id: 'sp-a', name: 'Aphex Twin', image_url: 'https://cdn/a.jpg' }] },
      release,
      'spotify',
    );
    expect(artist).toEqual({
      id: 'sp-a',
      name: 'Aphex Twin',
      image_url: 'https://cdn/a.jpg',
      source: 'spotify',
    });
  });

  it('reads the nested images array when there is no image_url', () => {
    const artist = buildArtistObject(
      { artists: [{ name: 'X', images: [{ url: 'https://cdn/b.jpg' }] }] },
      release,
      'deezer',
    );
    expect(artist.image_url).toBe('https://cdn/b.jpg');
  });

  it('falls back to the catalog artist id and name', () => {
    const artist = buildArtistObject({}, release, 'musicbrainz');
    expect(artist.id).toBe('mb-artist');
    expect(artist.name).toBe('Aphex Twin');
  });
});

describe('releaseModalIdentity', () => {
  it('namespaces the id and labels the heading with the artist', () => {
    // The wishlist keys off this id; the lbl_ prefix is what keeps a label
    // grab distinct from the same album opened from an artist page.
    expect(releaseModalIdentity(release, 'sp-1', 'Drukqs')).toEqual({
      id: 'lbl_album_sp-1',
      heading: '[Aphex Twin] Drukqs',
    });
  });
});
