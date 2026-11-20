import { describe, expect, it } from 'vitest';

import type { ProgressRow, VideoCatalog } from '../-player.types';

import { decodeCatalog } from './catalog';
import { autoChannels, decadeOf, isKidSafe, playlistChannel, ruleChannel } from './channels';
import { remaining, runtime } from './format';
import { cleanMeta, progressPayload } from './progress';
import { continueRails, fraction, newestFirst } from './rails';
import {
  hashSeed,
  nowAiring,
  programsInWindow,
  scheduleDay,
  seededShuffle,
  startOfLocalDay,
  type GuideItem,
} from './schedule';
import { pickNext, type Box } from './spatial';
import { resolutionLabel, sortTitles } from './video';

const HOUR = 3600;

function pool(n: number, seconds = HOUR): GuideItem[] {
  return Array.from({ length: n }, (_, i) => ({ key: `m:${i}`, title: `Item ${i}`, seconds }));
}

describe('schedule', () => {
  const day = new Date(2026, 10, 20, 15, 0);

  it('is the same for the same channel and day', () => {
    const a = scheduleDay('genre-comedy', pool(10), day);
    const b = scheduleDay('genre-comedy', pool(10), new Date(2026, 10, 20, 2, 0));
    expect(a.map((p) => p.key)).toEqual(b.map((p) => p.key));
  });

  it('differs between channels and between days', () => {
    const a = scheduleDay('genre-comedy', pool(12), day).map((p) => p.key);
    const b = scheduleDay('genre-drama', pool(12), day).map((p) => p.key);
    const c = scheduleDay('genre-comedy', pool(12), new Date(2026, 10, 21)).map((p) => p.key);
    expect(a).not.toEqual(b);
    expect(a).not.toEqual(c);
  });

  it('starts at local midnight, runs back to back, and covers the whole day', () => {
    const programs = scheduleDay('x', pool(5, 5400), day);
    const midnight = startOfLocalDay(day.getTime());
    expect(programs[0].start).toBe(midnight);
    for (let i = 1; i < programs.length; i++) expect(programs[i].start).toBe(programs[i - 1].end);
    expect(programs.at(-1)!.end).toBeGreaterThanOrEqual(midnight + 24 * HOUR * 1000);
  });

  it('uses the real runtimes', () => {
    const items: GuideItem[] = [
      { key: 'a', title: 'A', seconds: 1234 },
      { key: 'b', title: 'B', seconds: 4321 },
      { key: 'c', title: 'C', seconds: 999 },
    ];
    for (const p of scheduleDay('x', items, day)) {
      expect(p.end - p.start).toBe(items.find((i) => i.key === p.key)!.seconds * 1000);
    }
  });

  it('drops items with no usable runtime', () => {
    const items: GuideItem[] = [
      { key: 'a', title: 'A', seconds: 0 },
      { key: 'b', title: 'B', seconds: Number.NaN },
      { key: 'c', title: 'C', seconds: 1800 },
    ];
    expect(new Set(scheduleDay('x', items, day).map((p) => p.key))).toEqual(new Set(['c']));
    expect(scheduleDay('x', [], day)).toEqual([]);
  });

  it('an ordered channel keeps episode order', () => {
    const programs = scheduleDay('show', pool(30, 1800), day, { ordered: true });
    const indexes = programs.slice(0, 10).map((p) => Number(p.key.split(':')[1]));
    for (let i = 1; i < indexes.length; i++) expect(indexes[i]).toBe((indexes[i - 1] + 1) % 30);
  });

  it('a window has no gaps or overlaps, across midnight too', () => {
    const from = new Date(2026, 10, 20, 22, 0).getTime();
    const to = new Date(2026, 10, 21, 3, 0).getTime();
    const programs = programsInWindow('x', pool(7, 5000), from, to);
    expect(programs[0].start).toBeLessThanOrEqual(from);
    expect(programs.at(-1)!.end).toBeGreaterThanOrEqual(to);
    for (let i = 1; i < programs.length; i++) expect(programs[i].start).toBe(programs[i - 1].end);
  });

  it('now airing gives the program and the offset into it', () => {
    const now = new Date(2026, 10, 20, 20, 17, 30).getTime();
    const hit = nowAiring('x', pool(9, 2700), now)!;
    expect(hit.program.start).toBeLessThanOrEqual(now);
    expect(hit.program.end).toBeGreaterThan(now);
    expect(hit.offsetSeconds).toBe(Math.floor((now - hit.program.start) / 1000));
  });

  it('shuffle is a permutation and seeded', () => {
    const items = Array.from({ length: 50 }, (_, i) => i);
    const a = seededShuffle(items, hashSeed('s'));
    expect([...a].sort((x, y) => x - y)).toEqual(items);
    expect(seededShuffle(items, hashSeed('s'))).toEqual(a);
    expect(items[0]).toBe(0);
  });
});

const catalog: VideoCatalog = {
  kinds: { movies: true, shows: true, youtube: true },
  movies: [
    {
      id: 1,
      title: 'A',
      year: 1994,
      runtime_seconds: 6000,
      content_rating: 'PG',
      rating: 7,
      genres: ['Comedy'],
      added_at: '2026-01-01',
      has_poster: true,
      has_backdrop: true,
    },
    {
      id: 2,
      title: 'B',
      year: 1996,
      runtime_seconds: 5400,
      content_rating: 'R',
      rating: 7,
      genres: ['Comedy', 'Drama'],
      added_at: '2026-01-03',
      has_poster: true,
      has_backdrop: true,
    },
    {
      id: 3,
      title: 'C',
      year: 1999,
      runtime_seconds: 6600,
      content_rating: 'G',
      rating: 7,
      genres: ['Comedy'],
      added_at: '2026-01-02',
      has_poster: true,
      has_backdrop: true,
    },
    {
      id: 4,
      title: 'D',
      year: 2004,
      runtime_seconds: null,
      content_rating: 'PG',
      rating: 7,
      genres: ['Comedy'],
      added_at: null,
      has_poster: false,
      has_backdrop: false,
    },
  ],
  shows: [
    {
      id: 10,
      title: 'S1',
      year: 2010,
      network: 'HBO',
      content_rating: 'TV-PG',
      rating: 8,
      genres: ['Drama'],
      added_at: null,
      has_poster: true,
      has_backdrop: true,
      episode_count: 2,
      episodes: [
        [100, 1, 1, 3000, 'a'],
        [101, 1, 2, 3000, 'b'],
      ],
    },
    {
      id: 11,
      title: 'S2',
      year: 2012,
      network: 'HBO',
      content_rating: 'TV-MA',
      rating: 8,
      genres: ['Drama'],
      added_at: null,
      has_poster: true,
      has_backdrop: true,
      episode_count: 1,
      episodes: [[110, 1, 1, 3000, 'c']],
    },
    {
      id: 12,
      title: 'S3',
      year: 2012,
      network: 'NBC',
      content_rating: 'TV-G',
      rating: 8,
      genres: ['Comedy'],
      added_at: null,
      has_poster: true,
      has_backdrop: true,
      episode_count: 1,
      episodes: [[120, 1, 1, 1500, 'd']],
    },
  ],
  recent_episodes: [],
  youtube: [],
};

describe('channels', () => {
  const channels = autoChannels(catalog);
  const byId = new Map(channels.map((c) => [c.id, c]));

  it('makes type, genre, network, decade and kids channels from facets', () => {
    expect(byId.has('type-movies')).toBe(true);
    expect(byId.has('type-tv')).toBe(true);
    expect(byId.get('genre-comedy')!.pool.length).toBe(4); // 3 timed films + 1 episode
    expect(byId.has('network-hbo')).toBe(true);
    // one show is not a network channel
    expect(byId.has('network-nbc')).toBe(false);
    expect(byId.has('decade-1990')).toBe(true);
    expect(
      byId
        .get('kids')!
        .pool.map((i) => i.key)
        .sort(),
    ).toEqual(['episode:100', 'episode:101', 'episode:120', 'movie:1', 'movie:3'].sort());
  });

  it('leaves out thin channels and items without a runtime', () => {
    expect(byId.has('type-youtube')).toBe(false);
    expect(byId.has('decade-2000')).toBe(false);
    expect(byId.get('type-movies')!.pool.some((i) => i.key === 'movie:4')).toBe(false);
  });

  it('builds rule and playlist channels', () => {
    const rule = ruleChannel(
      { id: 'r', name: 'Nineties comedy', kinds: ['movies'], genre: 'comedy', decade: 1990 },
      catalog,
    )!;
    expect(rule.pool.map((i) => i.key).sort()).toEqual(['movie:1', 'movie:2', 'movie:3']);
    expect(
      ruleChannel({ id: 'r', name: 'x', kinds: ['movies'], decade: 2010 }, catalog),
    ).toBeNull();
    const pl = playlistChannel({
      id: 7,
      name: 'Mine',
      tracks: [1, 2, 3].map((i) => ({ id: i, title: `t${i}`, seconds: 200 })),
    })!;
    expect(pl.id).toBe('playlist-7');
  });

  it('helpers', () => {
    expect(decadeOf(1987)).toBe(1980);
    expect(decadeOf(null)).toBeNull();
    expect(isKidSafe('tv-pg')).toBe(true);
    expect(isKidSafe('R')).toBe(false);
    expect(isKidSafe(null)).toBe(false);
  });
});

function row(partial: Partial<ProgressRow>): ProgressRow {
  return {
    item_type: 'movie',
    item_id: '1',
    position_seconds: 100,
    duration_seconds: 1000,
    finished: false,
    meta: { title: 'T' },
    updated_at: null,
    ...partial,
  };
}

describe('rails', () => {
  it('splits continue rows into watching and listening, newest order kept', () => {
    const rails = continueRails([
      row({ item_type: 'episode', item_id: 'e' }),
      row({ item_type: 'audiobook', item_id: 'b' }),
      row({ item_type: 'movie', item_id: 'm' }),
      row({ item_type: 'movie', item_id: 'done', finished: true }),
      row({ item_type: 'movie', item_id: 'zero', position_seconds: 0 }),
    ]);
    expect(rails.map((r) => r.id)).toEqual(['watching', 'listening']);
    expect(rails[0].cards.map((c) => c.key)).toEqual(['episode:e', 'movie:m']);
    expect(rails[0].cards[0].progress).toBeCloseTo(0.1);
    expect(rails[0].cards[0].shape).toBe('wide');
    expect(rails[0].cards[0].source).toEqual({ itemType: 'episode', itemId: 'e' });
  });

  it('an empty store makes no rails', () => {
    expect(continueRails([])).toEqual([]);
  });

  it('fraction and newest first', () => {
    expect(fraction(50, 0)).toBeUndefined();
    expect(fraction(150, 100)).toBe(1);
    expect(
      newestFirst([{ d: '2026-01-01' }, { d: null }, { d: '2026-03-01' }], (x) => x.d).map(
        (x) => x.d,
      ),
    ).toEqual(['2026-03-01', '2026-01-01', null]);
  });
});

describe('progress payload', () => {
  it('shapes a good write', () => {
    expect(progressPayload('Movie', 5, 12.345, 100, { title: ' Heat ' })).toEqual({
      item_type: 'movie',
      item_id: '5',
      position_seconds: 12.3,
      duration_seconds: 100,
      meta: { title: 'Heat' },
    });
  });

  it('refuses times a media element reports before it knows', () => {
    expect(progressPayload('movie', 1, Number.NaN)).toBeNull();
    expect(progressPayload('movie', 1, -2)).toBeNull();
    expect(progressPayload('movie', 1, 10, Number.POSITIVE_INFINITY)).toEqual({
      item_type: 'movie',
      item_id: '1',
      position_seconds: 10,
    });
  });

  it('clamps to the duration and drops expiring artwork', () => {
    expect(progressPayload('track', 'x', 500, 200)!.position_seconds).toBe(200);
    expect(cleanMeta({ artwork: '/api/image-cache/abc', title: 'x' })).toEqual({ title: 'x' });
    expect(progressPayload('bad type!', 1, 5)).toBeNull();
    expect(progressPayload('movie', '', 5)).toBeNull();
  });
});

describe('format', () => {
  it('runtimes and remaining time', () => {
    expect(runtime(6720)).toBe('1h 52m');
    expect(runtime(205)).toBe('3:25');
    expect(runtime(2820)).toBe('47m');
    expect(runtime(null)).toBe('');
    expect(remaining(100, 3700)).toBe('1h left');
    expect(remaining(95, 100)).toBe('almost done');
  });
});

describe('spatial focus', () => {
  const box = (left: number, top: number, w = 100, h = 100): Box => ({
    left,
    top,
    right: left + w,
    bottom: top + h,
  });
  //  [a][b][c]
  //  [d]   [e]
  const a = box(0, 0);
  const b = box(110, 0);
  const c = box(220, 0);
  const d = box(0, 110);
  const e = box(220, 110);
  const all = [a, b, c, d, e];

  it('moves to the nearest neighbour in the direction pressed', () => {
    expect(pickNext(a, all, 'right')).toBe(1);
    expect(pickNext(b, all, 'right')).toBe(2);
    expect(pickNext(a, all, 'down')).toBe(3);
    expect(pickNext(e, all, 'up')).toBe(2);
    expect(pickNext(d, all, 'right')).toBe(4);
  });

  it('prefers the one in line over a diagonal at the same distance', () => {
    const below = box(0, 330);
    const diagonal = box(110, 330);
    expect(pickNext(a, [a, diagonal, below], 'down')).toBe(2);
    expect(pickNext(c, all, 'down')).toBe(4);
  });

  it('nothing that way is -1', () => {
    expect(pickNext(a, all, 'left')).toBe(-1);
    expect(pickNext(a, all, 'up')).toBe(-1);
  });
});

describe('catalog decoding', () => {
  it('reads rows by the field names the server sends', () => {
    const out = decodeCatalog({
      fields: {
        movie: ['title', 'id', 'genre_ids', 'runtime_seconds', 'has_poster'],
        show: ['id', 'title', 'episode_count', 'episodes'],
      },
      genres: { '3': 'Drama' },
      movies: [['Heat', 7, [3, 99], 6000, 1]],
      shows: [[5, 'S', 40, [[1, 1, 1, 1800, 'Pilot']]]],
    });
    expect(out.movies[0]).toMatchObject({
      id: 7,
      title: 'Heat',
      genres: ['Drama'],
      runtime_seconds: 6000,
      has_poster: true,
      year: null,
    });
    expect(out.shows[0]).toMatchObject({
      id: 5,
      episode_count: 40,
      episodes: [[1, 1, 1, 1800, 'Pilot']],
    });
    expect(out.kinds).toEqual({ movies: false, shows: false, youtube: false });
  });
});

describe('video labels and sorting', () => {
  it('names resolutions the way people say them', () => {
    expect(resolutionLabel('720')).toBe('720p');
    expect(resolutionLabel('1080p')).toBe('1080p');
    expect(resolutionLabel('2160p')).toBe('4K');
    expect(resolutionLabel(null)).toBe('');
  });

  it('sorts titles like a shelf, ignoring a leading the', () => {
    const items = ['The Zoo', 'Alpha', 'a Beta'].map((title) => ({
      title,
      year: null,
      rating: null,
      added_at: null,
    }));
    expect(sortTitles(items, 'title').map((x) => x.title)).toEqual(['Alpha', 'a Beta', 'The Zoo']);
  });
});
