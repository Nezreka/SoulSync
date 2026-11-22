import { describe, expect, it } from 'vitest';

import type { GuideItem } from './schedule';

import { filmHour, premiereAt, programsInWindow, scheduleDay, startOfLocalDay } from './schedule';

const ep = (n: number): GuideItem => ({
  key: `episode:${n}`,
  title: `E${n}`,
  seconds: 1800,
  kind: 'episode',
});
const film = (n: number): GuideItem => ({
  key: `movie:${n}`,
  title: `M${n}`,
  seconds: 6000,
  kind: 'movie',
});

describe('smarter scheduling', () => {
  const day = new Date(2026, 10, 21, 12);
  const today = startOfLocalDay(day.getTime());

  it("airs what you haven't seen first, every time round", () => {
    const pool = Array.from({ length: 10 }, (_, i) => ep(i));
    const seen = new Set(pool.slice(0, 7).map((x) => x.key));
    const programs = scheduleDay('ch', pool, day, { seen, seenKey: 'a' });
    expect(programs.slice(0, 3).every((p) => !seen.has(p.key))).toBe(true);
    expect(programs.slice(3, 10).every((p) => seen.has(p.key))).toBe(true);
  });

  it('keeps films for the evening on a mixed channel', () => {
    const pool = [
      ...Array.from({ length: 12 }, (_, i) => ep(i)),
      ...Array.from({ length: 6 }, (_, i) => film(i)),
    ];
    const programs = scheduleDay('mix', pool, day, { daypart: true });
    for (const p of programs) expect(p.key.startsWith('movie:')).toBe(filmHour(p.start));
  });

  it('a marathon starts today at your next unseen episode', () => {
    const pool = Array.from({ length: 30 }, (_, i) => ep(i));
    const seen = new Set(pool.slice(0, 12).map((x) => x.key));
    const first = programsInWindow('m', pool, today, today + 1, {
      ordered: true,
      seen,
      seenKey: 'b',
      today,
    })[0];
    expect(first.key).toBe('episode:12');
    // tomorrow carries on from where today ran out
    const tomorrow = programsInWindow('m', pool, today + 86_400_000, today + 86_400_001, {
      ordered: true,
      seen,
      seenKey: 'b',
      today,
    })[0];
    expect(tomorrow.key).toBe(`episode:${(12 + 48) % 30}`);
  });
});

describe('shows in blocks', () => {
  it("an episode is followed by the show's next one", () => {
    const show = (h: string, n: number): GuideItem => ({
      key: `episode:${h}${n}`,
      title: h,
      seconds: 1800,
      kind: 'episode',
      href: `/player/tv/${h}`,
    });
    const pool = [
      ...[1, 2, 3, 4].map((n) => show('a', n)),
      ...[1, 2, 3, 4].map((n) => show('b', n)),
    ];
    const programs = scheduleDay('blk', pool, new Date(2026, 10, 21, 12), { blocks: 2 });
    const firstLap = programs.slice(0, 8).map((p) => p.key);
    // a lap airs each episode once
    expect(new Set(firstLap).size).toBe(firstLap.length);
    // and every episode with a next one has it straight after, unless it was itself a second
    const pairs = firstLap.filter((k, i) => {
      const n = firstLap[i + 1];
      return (
        n && n.slice(0, -1) === k.slice(0, -1) && Number(n.slice(-1)) === Number(k.slice(-1)) + 1
      );
    });
    expect(pairs.length).toBeGreaterThanOrEqual(3);
  });
});

describe('premieres', () => {
  it('a new episode airs at 8pm the evening it came, a film at 9', () => {
    const at = (h: number, d = 21) => new Date(2026, 10, d, h).getTime();
    expect(premiereAt(at(9), 'episode')).toBe(at(20));
    expect(premiereAt(at(21), 'episode')).toBe(at(20, 22));
    expect(premiereAt(at(9), 'movie')).toBe(at(21));
  });

  it('holds its slot on the channel, back to back with another, marked', () => {
    const pool = Array.from({ length: 10 }, (_, i) => ep(i));
    const day = new Date(2026, 10, 21, 12);
    const eight = new Date(2026, 10, 21, 20).getTime();
    const premieres = [
      { item: ep(100), at: eight },
      { item: ep(101), at: eight },
      { item: film(7), at: new Date(2026, 10, 21, 21).getTime() },
    ];
    const programs = scheduleDay('pre', pool, day, { premieres, premiereKey: 'p' });
    const first = programs.findIndex((p) => p.premiere);
    expect(programs[first].start).toBe(eight);
    expect(programs[first].key).toBe('episode:100');
    expect(programs[first + 1]).toMatchObject({ key: 'episode:101', premiere: true });
    expect(programs.filter((p) => p.premiere).map((p) => p.key)).toEqual([
      'episode:100',
      'episode:101',
      'movie:7',
    ]);
    // nothing runs over a premiere
    expect(programs[first - 1].end).toBeLessThanOrEqual(eight);
  });
});

describe('blocks never stall', () => {
  it('fills the day with repeated keys and a show that has run out', () => {
    const e = (h: string, n: number): GuideItem => ({
      key: `episode:${h}${n}`,
      title: h,
      seconds: 1800,
      kind: 'episode',
      href: `/player/tv/${h}`,
    });
    // the same episode twice in the pool, and one film
    const pool = [e('a', 1), e('a', 1), e('a', 2), film(1)];
    const programs = scheduleDay('stall', pool, new Date(2026, 10, 21, 12), { blocks: 2 });
    const last = programs[programs.length - 1];
    expect(last.end).toBeGreaterThanOrEqual(new Date(2026, 10, 22, 0).getTime());
  });
});
