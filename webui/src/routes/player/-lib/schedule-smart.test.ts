import { describe, expect, it } from 'vitest';

import type { GuideItem } from './schedule';

import { filmHour, programsInWindow, scheduleDay, startOfLocalDay } from './schedule';

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
