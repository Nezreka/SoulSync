/**
 * the guide runs on the server's clock.
 *
 * each device used its own midnight and its own date to lay out and seed a
 * channel's day, so a phone in another timezone had a different guide from
 * the tv in the living room. days are counted on the clock the server hands
 * out, whatever zone the device is in.
 */
import { afterEach, describe, expect, it } from 'vitest';

import { dayKey, filmHour, scheduleDay, setGuideClock, startOfLocalDay } from './schedule';

afterEach(() => setGuideClock(null));

const at = (iso: string) => new Date(iso).getTime();

describe('the guide clock', () => {
  it('a day starts at the server’s midnight, not the device’s', () => {
    setGuideClock({ tz: 'America/Los_Angeles' });
    // 3am utc on the 25th is still 7pm on the 24th in Los Angeles
    const now = at('2026-11-25T03:00:00Z');
    expect(startOfLocalDay(now)).toBe(at('2026-11-24T08:00:00Z'));
    expect(dayKey(new Date(now))).toBe('2026-11-24');
    setGuideClock({ tz: 'Asia/Tokyo' });
    expect(startOfLocalDay(now)).toBe(at('2026-11-24T15:00:00Z'));
    expect(dayKey(new Date(now))).toBe('2026-11-25');
  });

  it('a clock change makes a 23 or a 25 hour day, on the server’s calendar', () => {
    setGuideClock({ tz: 'America/Los_Angeles' });
    const spring = startOfLocalDay(at('2026-03-08T20:00:00Z'));
    expect(spring).toBe(at('2026-03-08T08:00:00Z'));
    expect(startOfLocalDay(spring + 36 * 3_600_000) - spring).toBe(23 * 3_600_000);
    const fall = startOfLocalDay(at('2026-11-01T20:00:00Z'));
    expect(startOfLocalDay(fall + 36 * 3_600_000) - fall).toBe(25 * 3_600_000);
  });

  it('with no zone name the server’s offset still decides the day', () => {
    setGuideClock({ tz: null, offset_minutes: -480 });
    const now = at('2026-11-25T03:00:00Z');
    expect(startOfLocalDay(now)).toBe(at('2026-11-24T08:00:00Z'));
    expect(dayKey(new Date(now))).toBe('2026-11-24');
    // a zone name this browser doesn't know falls back the same way
    setGuideClock({ tz: 'Not/AZone', offset_minutes: 540 });
    expect(dayKey(new Date(now))).toBe('2026-11-25');
  });

  it('film time is the server’s evening', () => {
    setGuideClock({ tz: 'America/Los_Angeles' });
    expect(filmHour(at('2026-11-25T03:00:00Z'))).toBe(true); // 7pm there
    expect(filmHour(at('2026-11-24T20:00:00Z'))).toBe(false); // noon there
  });

  it('two devices in different timezones lay out the same channel', () => {
    const pool = Array.from({ length: 12 }, (_, i) => ({
      key: `movie:m${i}`,
      title: `Movie ${i}`,
      seconds: 1320 + i * 60,
      kind: 'movie' as const,
    }));
    const now = at('2026-11-25T03:00:00Z');
    const device = process.env.TZ;
    const layout = (deviceZone: string) => {
      process.env.TZ = deviceZone; // where the phone or the tv happens to be
      setGuideClock({ tz: 'America/Los_Angeles' }); // what the server said
      return scheduleDay('ch-movies', [...pool], new Date(startOfLocalDay(now))).map((p) => [
        p.key,
        p.start,
      ]);
    };
    try {
      const tokyo = layout('Asia/Tokyo');
      const newYork = layout('America/New_York');
      expect(tokyo.length).toBeGreaterThan(0);
      expect(newYork).toEqual(tokyo);
      expect(tokyo[0][1]).toBe(at('2026-11-24T08:00:00Z')); // the server's midnight
      // without the server's clock the two would have differed
      setGuideClock(null);
      process.env.TZ = 'Asia/Tokyo';
      const own = scheduleDay('ch-movies', [...pool], new Date(startOfLocalDay(now)));
      expect(own[0].start).not.toBe(tokyo[0][1]);
    } finally {
      process.env.TZ = device;
    }
  });
});
