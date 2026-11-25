import { describe, expect, it } from 'vitest';

import type { Channel } from './channels';
import type { ServerPremiere } from './premieres';

import {
  premiereItem,
  premiereOptions,
  premieresFor,
  premiereWhen,
  upcomingPremieres,
} from './premieres';
import { programsInWindow, startOfLocalDay } from './schedule';

const chan = (id: string, over: Partial<Channel> = {}): Channel =>
  ({ id, name: id, kind: 'genre', blurb: '', pool: [], ...over }) as Channel;

const day = startOfLocalDay(new Date(2026, 10, 21, 12).getTime());
const at = (h: number) => day + h * 3_600_000;

const premiere = (over: Partial<ServerPremiere> = {}): ServerPremiere => ({
  key: 'episode:7',
  kind: 'episode',
  id: 7,
  show_id: 3,
  title: 'Silo',
  subtitle: 'S2 E1 · The Engineer',
  seconds: 2700,
  at: at(20),
  homes: ['network-apple-tv', 'genre-drama', 'type-tv'],
  has_poster: true,
  ...over,
});

describe('premieres', () => {
  it('a premiere airs once, on the first channel the server named that this guide has', () => {
    const channels = [chan('type-tv'), chan('genre-drama'), chan('genre-comedy')];
    const got = premieresFor([premiere()], channels);
    expect([...got.keys()]).toEqual(['genre-drama']);
    const p = got.get('genre-drama')![0];
    expect(p.at).toBe(at(20));
    expect(p.item).toMatchObject({
      key: 'episode:7',
      title: 'Silo',
      subtitle: 'S2 E1 · The Engineer',
      seconds: 2700,
      href: '/player/tv/3',
      kind: 'episode',
    });
    expect(p.item.art).toContain('/show/3');
  });

  it('a film is its own page and poster', () => {
    const item = premiereItem(
      premiere({ key: 'movie:9', kind: 'movie', id: 9, show_id: null, subtitle: null }),
    );
    expect(item).toMatchObject({ href: '/player/movies/9', kind: 'movie', subtitle: undefined });
    expect(item.art).toContain('/movie/9');
    expect(premiereItem(premiere({ has_poster: false })).art).toBeNull();
  });

  it('never on a music channel or a marathon, and nowhere when no channel can host it', () => {
    const channels = [
      chan('network-apple-tv', { ordered: true }),
      chan('genre-drama', { music: true }),
      chan('genre-comedy'),
    ];
    expect(premieresFor([premiere()], channels).size).toBe(0);
    expect(premieresFor(null, channels).size).toBe(0);
    expect(premieresFor([premiere({ seconds: 0 })], [chan('type-tv')]).size).toBe(0);
  });

  it('two at once keep the order the server sent, and the options name them', () => {
    const list = [premiere(), premiere({ key: 'episode:8', id: 8, subtitle: 'S2 E2' })];
    const mine = premieresFor(list, [chan('type-tv')]).get('type-tv')!;
    expect(mine.map((p) => p.item.key)).toEqual(['episode:7', 'episode:8']);
    expect(premiereOptions(mine).premiereKey).toBe(`episode:7@${at(20)},episode:8@${at(20)}`);
    expect(premiereOptions(undefined)).toEqual({});
  });

  it('the schedule cuts to it at its slot and marks it new, the rest of the day unmoved', () => {
    const pool = Array.from({ length: 12 }, (_, i) => ({
      key: `movie:${i}`,
      title: `film ${i}`,
      seconds: 5400 + i * 60,
      kind: 'movie',
    }));
    const base = { daypart: true, blocks: 1, rotateShows: true, today: day };
    const plain = programsInWindow('type-tv', pool, at(0), at(24), base);
    const mine = premieresFor([premiere()], [chan('type-tv')]).get('type-tv');
    const withIt = programsInWindow('type-tv', pool, at(0), at(24), {
      ...base,
      ...premiereOptions(mine),
    });
    const aired = withIt.find((p) => p.premiere)!;
    expect(aired.start).toBe(at(20));
    expect(aired.end).toBe(at(20) + 2700_000);
    expect(aired.title).toBe('Silo');
    // everything that started before 8pm is what it was (the last one cut at 8)
    const before = (list: typeof plain) =>
      list.filter((p) => p.start < at(20)).map((p) => `${p.key}@${p.start}`);
    expect(before(withIt)).toEqual(before(plain));
    expect(withIt.filter((p) => p.start < at(20)).at(-1)!.end).toBe(at(20));
  });

  it("the home row: what's on now or still to air, soonest first, with its channel", () => {
    const channels = [chan('type-tv'), chan('genre-drama')];
    const list = [
      premiere({ key: 'episode:1', at: at(21) }),
      premiere({ key: 'episode:2', at: at(20) }),
      premiere({ key: 'episode:3', at: at(8) }), // aired this morning
      premiere({ key: 'episode:4', at: at(20) + 3 * 86_400_000 }), // days away
      premiere({ key: 'episode:5', homes: ['network-nowhere'] }),
    ];
    const up = upcomingPremieres(list, channels, at(20) + 60_000);
    expect(up.map((u) => u.premiere.key)).toEqual(['episode:2', 'episode:1']);
    expect(up[0].channel.id).toBe('genre-drama');
    expect(premiereWhen(at(20), at(20) + 60_000)).toBe('On now');
    expect(premiereWhen(at(21), at(12))).toMatch(/^Tonight /);
    expect(premiereWhen(at(11), at(9))).toMatch(/^Today /);
    expect(premiereWhen(at(20) + 86_400_000, at(23))).toMatch(/^Tomorrow /);
  });
});
