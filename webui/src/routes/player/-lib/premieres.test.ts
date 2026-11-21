import { describe, expect, it } from 'vitest';

import type { Channel } from './channels';
import type { GuideItem } from './schedule';

import { premieresFor } from './premieres';

const ep = (show: number, id: number): GuideItem => ({
  key: `episode:${id}`,
  title: `show ${show}`,
  seconds: 1800,
  href: `/player/tv/${show}`,
  kind: 'episode',
});
const chan = (id: string, kind: Channel['kind'], pool: GuideItem[]): Channel =>
  ({ id, name: id, kind, blurb: '', pool }) as Channel;

const now = new Date(2026, 10, 21, 12).getTime();
const iso = (h: number) => new Date(2026, 10, 21, h).toISOString();

const recent = (id: number, n: number, at: string) => ({
  id,
  show_id: 1,
  show_title: 'show 1',
  season_number: 2,
  episode_number: n,
  title: null,
  has_still: false,
  added_at: at,
  runtime_seconds: 2700,
});

describe('premieres', () => {
  const channels = [
    chan('type-tv', 'type', [ep(1, 1), ep(2, 9)]),
    chan('genre-drama', 'genre', [ep(1, 1)]),
    chan('network-hbo', 'network', [ep(1, 1)]),
  ];

  it('a new episode premieres once, on its network, at 8pm that evening', () => {
    const got = premieresFor(
      { movies: [], recent_episodes: [recent(50, 1, iso(9))] },
      channels,
      now,
    );
    expect([...got.keys()]).toEqual(['network-hbo']);
    const [p] = got.get('network-hbo')!;
    expect(p.at).toBe(new Date(2026, 10, 21, 20).getTime());
    expect(p.item).toMatchObject({ key: 'episode:50', seconds: 2700, subtitle: 'S2 E1' });
  });

  it('a season drop airs three a night, in order', () => {
    const drop = [5, 3, 1, 2, 4].map((n) => recent(60 + n, n, iso(9)));
    const got = premieresFor({ movies: [], recent_episodes: drop }, channels, now).get(
      'network-hbo',
    )!;
    const eight = new Date(2026, 10, 21, 20).getTime();
    expect(got.map((p) => p.item.subtitle)).toEqual(['S2 E1', 'S2 E2', 'S2 E3', 'S2 E4', 'S2 E5']);
    expect(got.map((p) => p.at)).toEqual([
      eight,
      eight,
      eight,
      eight + 86_400_000,
      eight + 86_400_000,
    ]);
  });

  it('old arrivals are not premieres', () => {
    const old = new Date(2026, 10, 10, 9).toISOString();
    expect(
      premieresFor({ movies: [], recent_episodes: [recent(70, 1, old)] }, channels, now).size,
    ).toBe(0);
  });
});
