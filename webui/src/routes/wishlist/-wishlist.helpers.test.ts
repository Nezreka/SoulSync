import { describe, expect, it } from 'vitest';

import type { ParsedWishlistTrack } from './-wishlist.types';

import {
  artistHue,
  buildArtistImageMap,
  failingTitle,
  filterWishlistGroups,
  groupWishlistArtists,
  orbImage,
  orbRingCovers,
  orbSizeClass,
  parseWishlistTrack,
  trackCountLabel,
} from './-wishlist.helpers';

const row = (overrides: Record<string, unknown> = {}) => ({
  spotify_track_id: 't1',
  retry_count: 0,
  spotify_data: {
    name: 'Xtal',
    album: { name: 'Selected Ambient Works', images: [{ url: 'cover.jpg' }] },
    artists: [{ name: 'Aphex Twin' }],
  },
  ...overrides,
});

describe('parseWishlistTrack', () => {
  it('unpacks an object payload', () => {
    expect(parseWishlistTrack(row(), 'album')).toMatchObject({
      track: 'Xtal',
      artist: 'Aphex Twin',
      album: 'Selected Ambient Works',
      image: 'cover.jpg',
      type: 'album',
      id: 't1',
      failing: false,
    });
  });

  it('unpacks spotify_data delivered as a JSON string', () => {
    // Some endpoints ship it serialised; both shapes must land identically.
    const parsed = parseWishlistTrack(
      row({ spotify_data: JSON.stringify(row().spotify_data) }),
      'album',
    );
    expect(parsed?.artist).toBe('Aphex Twin');
    expect(parsed?.image).toBe('cover.jpg');
  });

  it('drops a row whose spotify_data is unparseable or missing', () => {
    // Dropping beats rendering an "Unknown / Unknown Artist" ghost orb.
    expect(parseWishlistTrack(row({ spotify_data: '{not json' }), 'album')).toBeNull();
    expect(parseWishlistTrack(row({ spotify_data: null }), 'album')).toBeNull();
    expect(parseWishlistTrack(row({ spotify_data: 42 }), 'album')).toBeNull();
  });

  it('accepts a bare-string album and a bare-string artist', () => {
    const parsed = parseWishlistTrack(
      row({ spotify_data: { name: 'B', album: 'Just A Name', artists: ['Boards of Canada'] } }),
      'single',
    );
    expect(parsed).toMatchObject({ album: 'Just A Name', artist: 'Boards of Canada', image: '' });
  });

  it('falls back when artist info is absent entirely', () => {
    const parsed = parseWishlistTrack(row({ spotify_data: { name: 'B' } }), 'single');
    expect(parsed).toMatchObject({ artist: 'Unknown Artist', album: 'Unknown', track: 'B' });
  });

  it('marks failing at 3 attempts, not before', () => {
    expect(parseWishlistTrack(row({ retry_count: 2 }), 'album')?.failing).toBe(false);
    expect(parseWishlistTrack(row({ retry_count: 3 }), 'album')?.failing).toBe(true);
    expect(parseWishlistTrack(row({ retry_count: '4' }), 'album')?.failing).toBe(true);
  });

  it('falls back to id when there is no spotify_track_id', () => {
    expect(parseWishlistTrack(row({ spotify_track_id: null, id: 77 }), 'album')?.id).toBe('77');
  });
});

const track = (o: Partial<ParsedWishlistTrack> = {}): ParsedWishlistTrack => ({
  track: 'T',
  artist: 'A',
  album: 'Al',
  image: '',
  type: 'album',
  id: 'x',
  retry: 0,
  failing: false,
  lastTried: '',
  failReason: '',
  ...o,
});

describe('groupWishlistArtists', () => {
  it('nests album tracks under their album and hangs singles off the artist', () => {
    const groups = groupWishlistArtists(
      [
        track({ artist: 'A', album: 'One' }),
        track({ artist: 'A', album: 'One' }),
        track({ artist: 'A', album: 'Two' }),
      ],
      [track({ artist: 'A', type: 'single' })],
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].albums.map((a) => [a.name, a.tracks.length])).toEqual([
      ['One', 2],
      ['Two', 1],
    ]);
    expect(groups[0].singles).toHaveLength(1);
    expect(groups[0].total).toBe(4);
  });

  it('sorts busiest artist first', () => {
    const groups = groupWishlistArtists(
      [
        track({ artist: 'Quiet' }),
        track({ artist: 'Busy' }),
        track({ artist: 'Busy' }),
        track({ artist: 'Busy' }),
      ],
      [],
    );
    expect(groups.map((g) => g.name)).toEqual(['Busy', 'Quiet']);
  });

  it('rolls failing counts up across albums and singles', () => {
    const groups = groupWishlistArtists(
      [track({ artist: 'A', failing: true }), track({ artist: 'A' })],
      [track({ artist: 'A', type: 'single', failing: true })],
    );
    expect(groups[0].failingCount).toBe(2);
    expect(groups[0].total).toBe(3);
  });

  it('is empty for no tracks', () => {
    expect(groupWishlistArtists([], [])).toEqual([]);
  });
});

describe('buildArtistImageMap', () => {
  it('lets a curated watchlist photo override the library photo', () => {
    const map = buildArtistImageMap(
      [{ artist_images: { 'Aphex Twin': 'library.jpg' } }],
      [{ artist_name: 'Aphex Twin', image_url: 'curated.jpg' }],
    );
    expect(map.get('aphex twin')).toBe('curated.jpg');
  });

  it('keeps the library photo when the watchlist has none', () => {
    const map = buildArtistImageMap(
      [{ artist_images: { 'Aphex Twin': 'library.jpg' } }],
      [{ artist_name: 'Aphex Twin', image_url: null }],
    );
    expect(map.get('aphex twin')).toBe('library.jpg');
  });

  it('keys case-insensitively and skips blanks', () => {
    const map = buildArtistImageMap([{ artist_images: { 'MiXeD Case': 'a.jpg', Blank: '' } }], []);
    expect(map.get('mixed case')).toBe('a.jpg');
    expect(map.has('blank')).toBe(false);
  });
});

describe('orb presentation', () => {
  it('bands orb size at 4 and 10', () => {
    expect(orbSizeClass(3)).toBe('orb-sm');
    expect(orbSizeClass(4)).toBe('orb-md');
    expect(orbSizeClass(9)).toBe('orb-md');
    expect(orbSizeClass(10)).toBe('orb-lg');
  });

  it('gives an artist a stable hue in range', () => {
    expect(artistHue('Aphex Twin')).toBe(artistHue('Aphex Twin'));
    for (const n of ['a', 'Boards of Canada', '', '311']) {
      const h = artistHue(n);
      expect(h).toBeGreaterThanOrEqual(0);
      expect(h).toBeLessThan(360);
    }
  });

  it('prefers curated art, then album cover, then a single cover', () => {
    const base = { name: 'A', albums: [], singles: [], total: 0, failingCount: 0 };
    const images = new Map([['a', 'curated.jpg']]);
    expect(orbImage(base, images)).toBe('curated.jpg');
    expect(
      orbImage({ ...base, albums: [{ name: 'x', image: 'album.jpg', tracks: [] }] }, new Map()),
    ).toBe('album.jpg');
    expect(orbImage({ ...base, singles: [track({ image: 'single.jpg' })] }, new Map())).toBe(
      'single.jpg',
    );
    expect(orbImage(base, new Map())).toBe('');
  });

  it('shows the art ring only at 3+ covers, capped at 6', () => {
    const mk = (n: number) => ({
      name: 'A',
      singles: [],
      total: n,
      failingCount: 0,
      albums: Array.from({ length: n }, (_, i) => ({
        name: `a${i}`,
        image: `${i}.jpg`,
        tracks: [],
      })),
    });
    expect(orbRingCovers(mk(2))).toEqual([]);
    expect(orbRingCovers(mk(3))).toHaveLength(3);
    expect(orbRingCovers(mk(9))).toHaveLength(6);
  });
});

describe('failingTitle', () => {
  it('builds the tooltip from whatever the API supplied', () => {
    expect(failingTitle(track({ retry: 1 }))).toBe('1 failed attempt');
    expect(failingTitle(track({ retry: 4, lastTried: 'yesterday' }))).toBe(
      '4 failed attempts · last tried yesterday',
    );
    expect(failingTitle(track({ retry: 3, failReason: 'no sources' }))).toBe(
      '3 failed attempts\nno sources',
    );
  });
});

describe('filterWishlistGroups', () => {
  const groups = [
    { name: 'Aphex Twin', albums: [], singles: [], total: 3, failingCount: 0 },
    { name: 'Boards of Canada', albums: [], singles: [], total: 5, failingCount: 2 },
  ];

  it('matches album names, not just the artist', () => {
    // Regression guard: f59c56438 renamed .wl-satellite -> .wl-album-tile and
    // left the filter querying the old class, so this silently matched nothing
    // from that commit until the port restored it.
    const withAlbum = [
      {
        name: 'Boards of Canada',
        albums: [{ name: 'Geogaddi', image: '', tracks: [] }],
        singles: [],
        total: 4,
        failingCount: 0,
      },
      { name: 'Aphex Twin', albums: [], singles: [], total: 2, failingCount: 0 },
    ];
    expect(filterWishlistGroups(withAlbum, 'geogaddi', false).map((g) => g.name)).toEqual([
      'Boards of Canada',
    ]);
  });

  it('matches the artist name case-insensitively', () => {
    expect(filterWishlistGroups(groups, 'APHEX', false).map((g) => g.name)).toEqual(['Aphex Twin']);
    expect(filterWishlistGroups(groups, '', false)).toHaveLength(2);
  });

  it('narrows to artists with failing tracks', () => {
    expect(filterWishlistGroups(groups, '', true).map((g) => g.name)).toEqual(['Boards of Canada']);
  });

  it('applies both together', () => {
    expect(filterWishlistGroups(groups, 'aphex', true)).toEqual([]);
  });
});

describe('trackCountLabel', () => {
  it('pluralises', () => {
    expect(trackCountLabel(0)).toBe('0 tracks');
    expect(trackCountLabel(1)).toBe('1 track');
    expect(trackCountLabel(2)).toBe('2 tracks');
  });
});
