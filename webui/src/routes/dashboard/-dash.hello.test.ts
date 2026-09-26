import { describe, expect, it } from 'vitest';

import { countBusyWorkers, greetingForHour, greetingLine, heroNumbers } from './-dash.hello';

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

describe('greetingLine', () => {
  it('puts the name after the greeting', () => {
    expect(greetingLine('good evening', 'Boulder')).toBe('good evening, Boulder');
  });

  it('keeps the question mark at the end', () => {
    expect(greetingLine('up late?', 'Boulder')).toBe('up late, Boulder?');
  });

  it('is just the greeting with no name', () => {
    expect(greetingLine('up late?', '')).toBe('up late?');
  });
});

describe('heroNumbers', () => {
  it('omits what it does not know instead of showing zeros', () => {
    // Fresh boot: no db stats yet → a bare greeting, never "0 tracks".
    expect(heroNumbers(null)).toEqual([]);
    expect(heroNumbers({ tracks: 0, albums: 0, artists: 0 })).toEqual([]);
    expect(heroNumbers({ tracks: null, albums: 12 })).toEqual([
      { id: 'albums', value: (12).toLocaleString(), label: 'Albums' },
    ]);
  });

  it('formats the library in tracks, albums, artists order', () => {
    const numbers = heroNumbers({ tracks: 308778, albums: 70061, artists: 5550 });
    expect(numbers.map((n) => n.id)).toEqual(['tracks', 'albums', 'artists']);
    expect(numbers.map((n) => n.value)).toEqual([
      (308778).toLocaleString(),
      (70061).toLocaleString(),
      (5550).toLocaleString(),
    ]);
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
