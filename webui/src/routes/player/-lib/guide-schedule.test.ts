import { describe, expect, it } from 'vitest';

import { guideScheduleOptions, sharedCommercials } from './guide-schedule';
import { programsInWindow, startOfLocalDay } from './schedule';

const clip = (id: string) => ({ id, title: id, era: '80s', duration_seconds: 30 });

describe('the shared guide schedule', () => {
  it('commercials off, or none cached, schedules no breaks', () => {
    expect(sharedCommercials(null)).toBeUndefined();
    expect(
      sharedCommercials({ enabled: false, era: 'auto', mode: 'cable', clips: [clip('a')] }),
    ).toBe(undefined);
    expect(sharedCommercials({ enabled: true, era: 'auto', mode: 'cable', clips: [] })).toBe(
      undefined,
    );
  });

  it('the same config always gives the same key', () => {
    const cfg = { enabled: true, era: '80s', mode: 'cable', clips: [clip('a'), clip('b')] };
    expect(sharedCommercials(cfg)?.key).toBe(sharedCommercials({ ...cfg })?.key);
  });

  it('home and guide see the same thing airing', () => {
    const pool = Array.from({ length: 8 }, (_, i) => ({
      key: `movie:m${i}`,
      title: `M${i}`,
      seconds: 1800 + i * 600,
      kind: 'movie' as const,
    }));
    const now = Date.UTC(2026, 10, 24, 19, 7);
    const comm = sharedCommercials({
      enabled: true,
      era: '80s',
      mode: 'cable',
      clips: [clip('a'), clip('b'), clip('c')],
    });
    const opts = guideScheduleOptions(now, comm);
    expect(opts.today).toBe(startOfLocalDay(now));
    const a = programsInWindow('ch', pool, now - 3_600_000, now + 3_600_000, opts);
    const b = programsInWindow(
      'ch',
      pool,
      now - 120_000,
      now + 3_600_000,
      guideScheduleOptions(now, comm),
    );
    const on = (ps: typeof a) => ps.find((p) => p.start <= now && p.end > now)?.key;
    expect(on(b)).toBe(on(a));
  });
});
