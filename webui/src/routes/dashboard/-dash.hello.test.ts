import { describe, expect, it } from 'vitest';

import { buildHelloStats, countBusyWorkers, greetingForHour } from './-dash.hello';

describe('greetingForHour', () => {
  it('covers the whole clock with no gaps', () => {
    for (let hour = 0; hour < 24; hour++) {
      expect(greetingForHour(hour)).toBeTruthy();
    }
  });

  it('picks the expected bucket at the boundaries', () => {
    expect(greetingForHour(4)).toBe('up late?');
    expect(greetingForHour(5)).toBe('good morning');
    expect(greetingForHour(11)).toBe('good morning');
    expect(greetingForHour(12)).toBe('good afternoon');
    expect(greetingForHour(17)).toBe('good afternoon');
    expect(greetingForHour(18)).toBe('good evening');
    expect(greetingForHour(23)).toBe('good evening');
    expect(greetingForHour(0)).toBe('up late?');
  });
});

describe('buildHelloStats', () => {
  it('omits what it does not know instead of showing zeros', () => {
    // Fresh boot: no db stats yet, nothing running, no countdown → a bare
    // greeting, never "0 tracks".
    expect(buildHelloStats({ tracks: null, artists: null, busyWorkers: 0 })).toEqual([]);
    expect(buildHelloStats({ tracks: 0, artists: 0, busyWorkers: 0 })).toEqual([]);
  });

  it('formats counts and routes each chip somewhere useful', () => {
    const chips = buildHelloStats({
      tracks: 48212,
      artists: 2881,
      busyWorkers: 6,
      scanCountdown: '2h 13m',
    });
    expect(chips.map((chip) => chip.label)).toEqual([
      `${(48212).toLocaleString()} tracks`,
      `${(2881).toLocaleString()} artists`,
      '6 workers busy',
      'next scan in 2h 13m',
    ]);
    expect(chips.find((chip) => chip.id === 'tracks')?.page).toBe('library');
    expect(chips.find((chip) => chip.id === 'scan')?.page).toBe('watchlist');
    // Workers has no page — it opens the enrichment manager instead.
    expect(chips.find((chip) => chip.id === 'workers')?.page).toBeUndefined();
  });

  it('says "1 worker busy", not "1 workers busy"', () => {
    const chips = buildHelloStats({ busyWorkers: 1 });
    expect(chips).toEqual([{ id: 'workers', label: '1 worker busy' }]);
  });
});

describe('countBusyWorkers', () => {
  it("counts only the 'active' stateClass — running-and-not-paused", () => {
    expect(
      countBusyWorkers({
        musicbrainz: { stateClass: 'active' },
        deezer: { stateClass: 'active' },
        lastfm: { stateClass: 'paused' },
        genius: { stateClass: 'complete' },
        repair: { stateClass: null },
      }),
    ).toBe(2);
    expect(countBusyWorkers({})).toBe(0);
  });
});
