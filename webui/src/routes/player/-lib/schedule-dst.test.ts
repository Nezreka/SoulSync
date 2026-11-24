/**
 * the guide across a clock change. a day ran from local midnight for a flat
 * 24 hours, so the fall-back day (25 hours) stopped an hour short of
 * midnight: nothing aired 23:00-00:00 and tuning in then found nothing.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { programsInWindow, scheduleDay, startOfLocalDay } from './schedule';

const pool = Array.from({ length: 12 }, (_, i) => ({
  key: `movie:m${i}`,
  title: `Movie ${i}`,
  seconds: 1320 + i * 60,
  kind: 'movie' as const,
}));

let tz: string | undefined;
beforeAll(() => {
  tz = process.env.TZ;
  process.env.TZ = 'America/Los_Angeles';
});
afterAll(() => {
  process.env.TZ = tz;
});

describe('the guide across a clock change', () => {
  for (const [label, y, m, d] of [
    ['fall back (25h)', 2026, 10, 1],
    ['spring forward (23h)', 2026, 2, 8],
  ] as const) {
    it(`${label}: the day runs to the next midnight, no gap`, () => {
      const dayStart = new Date(y, m, d).getTime();
      const nextMidnight = new Date(y, m, d + 1).getTime();
      // the zone really is in effect: these days aren't 24 hours
      expect(nextMidnight - dayStart).not.toBe(24 * 3_600_000);
      expect(startOfLocalDay(dayStart)).toBe(dayStart);
      const day = scheduleDay('dst', pool, new Date(dayStart));
      expect(day.at(-1)!.end).toBeGreaterThanOrEqual(nextMidnight);
      // something airs at every minute of the day, the last hour included
      const programs = programsInWindow('dst', pool, dayStart, nextMidnight + 3_600_000);
      for (let t = dayStart; t < nextMidnight; t += 10 * 60_000)
        expect(
          programs.some((p) => p.start <= t && p.end > t),
          new Date(t).toString(),
        ).toBe(true);
    });
  }
});
