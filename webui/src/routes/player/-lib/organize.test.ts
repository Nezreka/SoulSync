/** the downloads page's filing: sections by kind, cards, seasons, sorting */

import { describe, expect, it } from 'vitest';

import { organize, seasonLabel, stamp, type Entry } from './organize';

type E = Entry & { at: number };
const e = (
  item_type: string,
  item_id: string,
  at: number,
  group?: Entry['group'],
  title?: string,
): E => ({
  item_type,
  item_id,
  at,
  group,
  title: title ?? `${item_type} ${item_id}`,
  size_bytes: 100,
});

describe('organize', () => {
  it('puts the kinds in the page order and leaves empty ones out', () => {
    const out = organize(
      [
        e('home', '1', 1),
        e('podcast_episode', '3:abc', 1, { type: 'podcast', id: '3', title: 'Pod' }),
        e('audiobook', 'b1', 1, { type: 'audiobook', id: 'b1', title: 'Dune' }),
        e('track', '1', 1),
        e('youtube', 'y', 1),
        e('movie', '5', 1, { type: 'movie', id: '5', title: 'Heat' }),
        e('episode', '101', 1, { type: 'season', id: '7:1', title: 'Bluey' }),
      ],
      (x) => x.at,
    );
    expect(out.map((s) => s.label)).toEqual([
      'TV shows',
      'Movies',
      'YouTube',
      'Music',
      'Audiobooks',
      'Podcasts',
      'Home videos',
    ]);
    expect(organize([e('movie', '5', 1)], (x) => x.at).map((s) => s.id)).toEqual(['movies']);
  });

  it('folds seasons into their show, in season order with specials after', () => {
    const season = (n: number) => ({ type: 'season' as const, id: `7:${n}`, title: 'Bluey' });
    const [tv] = organize(
      [
        e('episode', '3', 1, season(2), 'S2 E1 · A'),
        e('episode', '1', 1, season(1), 'S1 E2 · B'),
        e('episode', '2', 1, season(1), 'S1 E1 · C'),
        e('episode', '9', 1, season(0), 'S0 E1 · D'),
      ],
      (x) => x.at,
    );
    expect(tv.cards).toHaveLength(1);
    const show = tv.cards[0];
    expect(show.title).toBe('Bluey');
    expect(show.type).toBe('show');
    expect(show.id).toBe('7');
    expect(show.seasons.map((s) => s.label)).toEqual(['Season 1', 'Season 2', 'Specials']);
    expect(show.seasons[0].items.map((x) => x.item_id)).toEqual(['2', '1']);
    expect(tv.count).toBe(4);
    expect(tv.bytes).toBe(400);
  });

  it('music: albums and playlists are cards, loose tracks are Songs', () => {
    const [music] = organize(
      [
        e('track', '1', 5, { type: 'playlist', id: 'user:4', title: 'Road Trip' }),
        e('track', '2', 9, { type: 'album', id: '9', title: 'Abbey Road' }),
        e('track', '3', 2, { type: 'track', id: '3', title: 'One' }),
        e('track', '4', 7, null),
      ],
      (x) => x.at,
    );
    expect(music.cards.map((c) => [c.title, c.items.length])).toEqual([
      ['Abbey Road', 1],
      ['Songs', 2],
      ['Road Trip', 1],
    ]);
    // songs newest first inside
    expect(music.cards[1].items.map((x) => x.item_id)).toEqual(['4', '3']);
  });

  it('sorts a to z when asked, numbers in order', () => {
    const [movies] = organize(
      [
        e('movie', '1', 9, { type: 'movie', id: '1', title: 'Zodiac' }),
        e('movie', '2', 1, { type: 'movie', id: '2', title: 'alien 10' }),
        e('movie', '3', 5, { type: 'movie', id: '3', title: 'Alien 2' }),
      ],
      (x) => x.at,
      'az',
    );
    expect(movies.cards.map((c) => c.title)).toEqual(['Alien 2', 'alien 10', 'Zodiac']);
  });

  it('reads times and season names', () => {
    expect(stamp('2026-11-20T10:00:00Z')).toBe(Date.parse('2026-11-20T10:00:00Z'));
    expect(stamp(null)).toBe(0);
    expect(stamp('nope')).toBe(0);
    expect(seasonLabel('7:3')).toBe('Season 3');
    expect(seasonLabel('7:0', 'Season 0')).toBe('Specials');
  });
});
