import { describe, expect, it } from 'vitest';

import type { CatalogMovie, CatalogShow, PlayerLibrary } from '../-player.types';
import type { HomeVideo } from './home-videos';

import {
  isUhd,
  libraryContinueCards,
  libraryHref,
  libraryRecentCards,
  localDay,
  memoryCards,
  oneCardPerTitle,
  oneOfEach,
  parseLibRow,
  titleCopies,
} from './home-libraries';
import { movieCard, showCard, type RailCard } from './rails';

const movie = (id: number, patch: Partial<CatalogMovie> = {}): CatalogMovie => ({
  id,
  title: `Film ${id}`,
  year: 2010,
  runtime_seconds: 6000,
  content_rating: 'PG',
  rating: 7,
  genres: ['Drama'],
  added_at: '2026-01-01',
  has_poster: true,
  has_backdrop: true,
  studio: null,
  tmdb_id: null,
  library_id: null,
  ...patch,
});
const show = (id: number, patch: Partial<CatalogShow> = {}): CatalogShow => ({
  id,
  title: `Show ${id}`,
  year: 2015,
  network: null,
  content_rating: null,
  rating: 8,
  genres: [],
  added_at: '2026-01-01',
  has_poster: true,
  has_backdrop: true,
  episode_count: 10,
  episodes: [],
  tmdb_id: null,
  library_id: null,
  ...patch,
});
const LIBS: PlayerLibrary[] = [
  { id: 1, kind: 'movie', name: 'Movies', own_page: false, main: true },
  { id: 2, kind: 'show', name: 'TV Shows', own_page: false, main: true },
  { id: 7, kind: 'movie', name: 'Movies 4K', own_page: true, main: false, uhd: true },
  { id: 8, kind: 'movie', name: 'Kids', own_page: true, main: false, uhd: false },
  { id: 9, kind: 'home', name: 'Home videos', own_page: true, main: true },
];

describe('one card per title', () => {
  // Dune in Movies (id 10) and Movies 4K (id 20); the 4K one was added last
  const dune = movie(10, { title: 'Dune', tmdb_id: 438631, library_id: 1, added_at: '2025-01-01' });
  const dune4k = movie(20, {
    title: 'Dune',
    tmdb_id: 438631,
    library_id: 7,
    added_at: '2026-06-01',
  });
  const other = movie(30, { title: 'Heat', tmdb_id: 949, library_id: 1 });
  const copies = titleCopies([dune, dune4k, other], [], LIBS);

  it('shows Movies and Movies 4K copies of one film once, on the main copy, with a 4K chip', () => {
    const row = oneCardPerTitle([dune4k, other, dune].map(movieCard), copies);
    expect(row.map((c) => c.title)).toEqual(['Dune', 'Heat']);
    expect(row[0].href).toBe('/player/movies/10');
    expect(row[0].preview).toEqual({ kind: 'movie', id: 10 });
    expect(row[0].chip).toBe('4K');
    expect(row[1].chip).toBeUndefined();
  });

  it('keeps a title in one library as it is', () => {
    const solo = movie(40, { tmdb_id: 1, library_id: 7 });
    const row = oneCardPerTitle([movieCard(solo)], titleCopies([solo], [], LIBS));
    expect(row).toEqual([movieCard(solo)]);
  });

  it('matches by title and year without a tmdb id, never by title alone', () => {
    const a = movie(1, { title: 'Home Movie', year: 2001, library_id: null });
    const b = movie(2, { title: 'home movie ', year: 2001, library_id: 7 });
    const c = movie(3, { title: 'Home Movie', year: null, library_id: 8 });
    const d = movie(4, { title: 'Home Movie', year: null, library_id: 1 });
    const row = oneCardPerTitle([a, b, c, d].map(movieCard), titleCopies([a, b, c, d], [], LIBS));
    expect(row.map((x) => x.preview?.id)).toEqual([1, 3, 4]);
    // no library_id is the main library: that copy wins
    expect(row[0].chip).toBe('4K');
  });

  it('picks a copy that is not 4K when neither is in the main library', () => {
    const kids = movie(5, { tmdb_id: 9, library_id: 8 });
    const uhd = movie(6, { tmdb_id: 9, library_id: 7 });
    const row = oneCardPerTitle([movieCard(uhd)], titleCopies([uhd, kids], [], LIBS));
    expect(row[0].href).toBe('/player/movies/5');
    expect(row[0].chip).toBe('4K');
  });

  it('dedupes shows too, and the billboard pool', () => {
    const s1 = show(1, { tmdb_id: 77, library_id: 2 });
    const s2 = show(2, { tmdb_id: 77, library_id: 99 });
    const copiesB = titleCopies(
      [],
      [s1, s2],
      [...LIBS, { ...LIBS[2], id: 99, kind: 'show', name: 'TV 4K', uhd: true }],
    );
    const row = oneCardPerTitle([showCard(s2), showCard(s1)], copiesB);
    expect(row).toHaveLength(1);
    expect(row[0].href).toBe('/player/tv/1');
    expect(row[0].chip).toBe('4K');
    expect(oneOfEach([dune4k, other, dune], 'movie', copies).map((m) => m.id)).toEqual([10, 30]);
  });

  it('leaves cards that are not titles alone', () => {
    const album: RailCard = { key: 'album:1', title: 'An album', shape: 'square' };
    expect(oneCardPerTitle([album, album], copies)).toEqual([album, album]);
  });

  it('knows a 4K library by the server or, from an older one, by its name', () => {
    expect(isUhd({ kind: 'movie', name: 'Films', uhd: true })).toBe(true);
    expect(isUhd({ kind: 'movie', name: 'Movies 4K' })).toBe(true);
    expect(isUhd({ kind: 'movie', name: 'Movies' })).toBe(false);
    expect(isUhd({ kind: 'home', name: '4K camera' })).toBe(false);
  });
});

describe('rows about one library', () => {
  const films = [
    movie(1, { library_id: null, added_at: '2026-03-01' }),
    movie(2, { library_id: 7, added_at: '2026-05-01' }),
    movie(3, { library_id: 7, added_at: '2026-06-01' }),
    movie(4, { library_id: 1, added_at: '2026-04-01' }),
  ];
  it('reads its ids', () => {
    expect(parseLibRow('lib-recent:7')).toEqual({ row: 'recent', library: 7 });
    expect(parseLibRow('lib-continue:12')).toEqual({ row: 'continue', library: 12 });
    expect(parseLibRow('lib-recent:x')).toBeNull();
    expect(parseLibRow('home-memories')).toBeNull();
  });

  it('recently added is that library only, newest first; the main one has the rest', () => {
    expect(libraryRecentCards(LIBS[2], LIBS, films, []).map((c) => c.preview?.id)).toEqual([3, 2]);
    expect(libraryRecentCards(LIBS[0], LIBS, films, []).map((c) => c.preview?.id)).toEqual([4, 1]);
    expect(libraryHref(LIBS[2])).toBe('/player/movies?tab=library&library=7');
    expect(libraryHref(LIBS[0])).toBe('/player/movies?tab=library');
  });

  it('continue keeps what you started in that library (a movie by id, an episode by its show)', () => {
    const shows = [show(50, { library_id: 2 })];
    const watching: RailCard[] = [
      { key: 'movie:3', title: 'Film 3', shape: 'wide', href: '/player/movies/3' },
      { key: 'movie:1', title: 'Film 1', shape: 'wide', href: '/player/movies/1' },
      { key: 'episode:500', title: 'Show 50', shape: 'wide', href: '/player/tv/50?season=1' },
      { key: 'youtube:x', title: 'A clip', shape: 'wide' },
    ];
    expect(libraryContinueCards(watching, LIBS[2], LIBS, films, shows).map((c) => c.key)).toEqual([
      'movie:3',
    ]);
    expect(libraryContinueCards(watching, LIBS[0], LIBS, films, shows).map((c) => c.key)).toEqual([
      'movie:1',
    ]);
    expect(libraryContinueCards(watching, LIBS[1], LIBS, films, shows).map((c) => c.key)).toEqual([
      'episode:500',
    ]);
  });
});

describe('on this day', () => {
  const clip = (id: number, filmed: string, patch: Partial<HomeVideo> = {}): HomeVideo => ({
    id,
    title: `Clip ${id}`,
    filmed_at: filmed,
    duration_s: 75,
    width: 1920,
    height: 1080,
    album: 'Holidays',
    album_key: 'k',
    thumb_url: `/api/player/home/thumb/${id}`,
    progress: { position_s: 0, watched: false },
    missing: false,
    library: 9,
    ...patch,
  });
  it('says how long ago, and leaves out a missing clip', () => {
    const cards = memoryCards(
      [clip(1, '2023-11-26T10:00:00'), clip(2, '2019-11-26T09:00:00', { missing: true })],
      new Date(2026, 10, 26),
    );
    expect(cards).toHaveLength(1);
    expect(cards[0].subtitle).toBe('3 years ago · Nov 26, 2023');
    expect(cards[0].badge).toBe('1:15');
    expect(cards[0].href).toBe('/player/home-videos?library=9');
    expect(cards[0].shape).toBe('wide');
  });
  it('asks for the viewer’s own day', () => {
    expect(localDay(new Date(2026, 0, 5, 23, 30))).toBe('2026-01-05');
  });
});
