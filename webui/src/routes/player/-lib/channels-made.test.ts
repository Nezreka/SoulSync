import { describe, expect, it } from 'vitest';

import type { CatalogMovie, CatalogShow, VideoCatalog } from '../-player.types';

import {
  applyLineup,
  EMPTY_SPEC,
  marathonItems,
  numberChannels,
  specBlurb,
  specChannel,
  specMatches,
  specPool,
  type Channel,
} from './channels';

const now = Date.UTC(2026, 10, 21);
const movie = (id: number, over: Partial<CatalogMovie> = {}): CatalogMovie => ({
  id,
  title: `M${id}`,
  year: 1985,
  runtime_seconds: 5400,
  content_rating: 'R',
  rating: 7.5,
  genres: ['Horror'],
  added_at: '2026-11-01 00:00:00',
  has_poster: false,
  has_backdrop: false,
  studio: 'Carolco',
  tmdb_id: null,
  ...over,
});
const show = (id: number, over: Partial<CatalogShow> = {}): CatalogShow => ({
  id,
  title: `S${id}`,
  year: 1995,
  network: 'HBO',
  content_rating: 'TV-MA',
  rating: 8,
  genres: ['Drama'],
  added_at: '2020-01-01',
  has_poster: false,
  has_backdrop: false,
  episode_count: 3,
  episodes: [
    [id * 10 + 1, 1, 1, 1500, 'a'],
    [id * 10 + 2, 1, 2, 1500, 'b'],
    [id * 10 + 3, 1, 3, 1500, 'c'],
  ],
  tmdb_id: null,
  ...over,
});
const catalog = {
  movies: [
    movie(1),
    movie(2, { year: 1999, genres: ['Comedy'] }),
    movie(3, { rating: 5 }),
    movie(4),
  ],
  shows: [show(9), show(8, { network: 'NBC' })],
  youtube: [],
  recent_episodes: [],
  kinds: { movies: true, shows: true, youtube: false },
} as unknown as VideoCatalog;

describe('channels people make', () => {
  it('a spec picks what fits: genre, decade, score, studio, newness', () => {
    const spec = {
      ...EMPTY_SPEC,
      name: 'x',
      kinds: ['movies' as const],
      genres: ['horror'],
      decades: [1980],
      min_score: 7,
    };
    expect(specPool(spec, catalog, now).map((p) => p.key)).toEqual(['movie:1', 'movie:4']);
    expect(specMatches({ ...spec, added_days: 7 }, movie(1), 'movies', now)).toBe(false);
    expect(specMatches({ ...spec, added_days: 30 }, movie(1), 'movies', now)).toBe(true);
    expect(specMatches({ ...spec, studios: ['A24'] }, movie(1), 'movies', now)).toBe(false);
  });

  it('a network picks shows, and leaves movies out', () => {
    const spec = { ...EMPTY_SPEC, name: 'x', networks: ['hbo'] };
    const keys = specPool(spec, catalog, now).map((p) => p.key);
    expect(keys.every((k) => k.startsWith('episode:9'))).toBe(true);
  });

  it('a made channel files under everyone or yours, and says what it is', () => {
    const spec = { ...EMPTY_SPEC, name: 'Shocks', kinds: ['movies' as const], genres: ['Horror'] };
    expect(specChannel({ id: 'c1', scope: 'server', spec }, catalog, now)?.kind).toBe('server');
    expect(specChannel({ id: 'c2', scope: 'mine', spec }, catalog, now)?.id).toBe('made-c2');
    expect(specBlurb(spec)).toBe('movies · Horror');
  });

  it('a marathon airs every owned episode in order, specials last', () => {
    const items = marathonItems({
      id: 5,
      title: 'Show',
      seasons: [
        { season_number: 2, episodes: [{ id: 21, episode_number: 1, runtime_minutes: 22 }] },
        { season_number: 0, episodes: [{ id: 1, episode_number: 1, runtime_minutes: 22 }] },
        {
          season_number: 1,
          episodes: [
            { id: 12, episode_number: 2, runtime_minutes: 22 },
            { id: 11, episode_number: 1, runtime_minutes: 22 },
            { id: 13, episode_number: 3, runtime_minutes: 22, owned: false },
          ],
        },
      ],
    });
    expect(items.map((i) => i.key)).toEqual([
      'episode:11',
      'episode:12',
      'episode:21',
      'episode:1',
    ]);
    const ch = specChannel(
      {
        id: 'm',
        scope: 'mine',
        spec: { ...EMPTY_SPEC, name: 'All of it', show: 5, order: 'in_order' },
      },
      catalog,
      now,
      { title: 'Show', episodes: items },
    );
    expect(ch?.ordered).toBe(true);
    expect(ch?.blurb).toBe('Show, start to finish');
  });

  it("a lineup orders, renames and hides; what it doesn't name keeps the guide's order", () => {
    const c = (id: string, kind: Channel['kind']): Channel => ({
      id,
      name: id,
      kind,
      blurb: '',
      pool: [],
    });
    const base = [c('a', 'genre'), c('b', 'genre'), c('c', 'genre'), c('d', 'kids')];
    const lined = applyLineup(base, [
      { channel_id: 'c', hidden: false, name: 'See' },
      { channel_id: 'a', hidden: true, name: null },
    ]);
    expect(lined.map((x) => [x.id, x.name, !!x.hidden])).toEqual([
      ['c', 'See', false],
      ['a', 'a', true],
      ['b', 'b', false],
      ['d', 'd', false],
    ]);
    const numbered = numberChannels(lined.filter((x) => !x.hidden));
    // kids sit before genres in the guide's groups; within genres, the lineup's order
    expect(numbered.map((x) => [x.id, x.number])).toEqual([
      ['d', 101],
      ['c', 102],
      ['b', 103],
    ]);
  });
});
