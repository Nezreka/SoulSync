import { describe, expect, it } from 'vitest';

import {
  buildYearSlides,
  describeListeningTime,
  formatHour,
  formatStoryDate,
  minutesToDays,
  monthBarHeight,
  peakMonthPlays,
} from './-year.helpers';
import type { YearInListening } from './-year.types';

function makeYear(overrides: Partial<YearInListening> = {}): YearInListening {
  return {
    period: { start: '2025-09-01', end: '2026-08-14', label: 'Sep 2025 — Aug 2026', months: 12 },
    has_data: true,
    totals: { plays: 100, minutes: 4000, artists: 20, albums: 15, tracks: 60, active_days: 40 },
    months: [{ month: '2026-01', label: 'Jan 2026', plays: 100, minutes: 4000, top_artist: 'A' }],
    top_artists: [{ name: 'A', plays: 60, months_on_top: 3 }],
    top_albums: [{ name: 'The Album', artist: 'A', plays: 40 }],
    top_tracks: [{ name: 'T', artist: 'A', album: 'Al', plays: 12, first_played: null, last_played: null }],
    discoveries: [{ name: 'New', first_played: '2026-02-01', plays: 9 }],
    peak_day: { date: '2026-05-20', plays: 14 },
    top_hour: { hour: 22, plays: 30 },
    ...overrides,
  };
}

describe('minutesToDays', () => {
  it('turns minutes into the unit people actually feel', () => {
    expect(minutesToDays(1440)).toBe(1);
    expect(minutesToDays(2880)).toBe(2);
  });

  it('is zero rather than negative or NaN for junk', () => {
    expect(minutesToDays(0)).toBe(0);
    expect(minutesToDays(-50)).toBe(0);
    expect(minutesToDays(Number.NaN)).toBe(0);
  });
});

describe('describeListeningTime', () => {
  it('switches to days once there is more than a day', () => {
    expect(describeListeningTime(4320)).toBe('3 days');
  });

  it('stays in minutes below a day, where "0.3 days" reads worse', () => {
    expect(describeListeningTime(420)).toBe('420 minutes');
  });

  it('says one day in the singular', () => {
    expect(describeListeningTime(1440)).toBe('1 day');
  });

  it('has an honest answer for an empty year', () => {
    expect(describeListeningTime(0)).toBe('0 minutes');
  });
});

describe('monthBarHeight', () => {
  it('draws the peak month full height', () => {
    expect(monthBarHeight(50, 50)).toBe(1);
  });

  it('keeps a quiet month visible instead of invisible', () => {
    const tiny = monthBarHeight(1, 1000);
    expect(tiny).toBeGreaterThan(0.05);
    expect(tiny).toBeLessThan(0.1);
  });

  it('gives a silent month no bar at all — that is the point of the gap', () => {
    expect(monthBarHeight(0, 50)).toBe(0);
  });

  it('does not divide by a zero peak', () => {
    expect(monthBarHeight(0, 0)).toBe(0);
    expect(monthBarHeight(5, 0)).toBe(0);
  });
});

describe('peakMonthPlays', () => {
  it('is the busiest month, not the last one', () => {
    expect(
      peakMonthPlays({
        months: [
          { month: '2026-01', label: 'Jan', plays: 4, minutes: 0, top_artist: null },
          { month: '2026-02', label: 'Feb', plays: 19, minutes: 0, top_artist: null },
          { month: '2026-03', label: 'Mar', plays: 2, minutes: 0, top_artist: null },
        ],
      }),
    ).toBe(19);
  });

  it('is zero for a year with no plays', () => {
    expect(peakMonthPlays({ months: [] })).toBe(0);
  });
});

describe('formatStoryDate', () => {
  it('reads the date as local, not UTC', () => {
    // '2026-05-20' via new Date() would be midnight UTC and render as the 19th
    // anywhere west of Greenwich. played_at is local wall-clock.
    expect(formatStoryDate('2026-05-20')).toContain('20');
  });

  it('tolerates a full timestamp, not just a bare date', () => {
    expect(formatStoryDate('2026-05-20 21:30:00')).toContain('20');
  });

  it('returns null for nothing rather than "Invalid Date"', () => {
    expect(formatStoryDate(null)).toBeNull();
    expect(formatStoryDate('')).toBeNull();
    expect(formatStoryDate('not-a-date')).toBeNull();
  });
});

describe('formatHour', () => {
  it('names the two hours that have names', () => {
    expect(formatHour(0)).toBe('midnight');
    expect(formatHour(12)).toBe('noon');
  });

  it('reads afternoon and evening hours in 12-hour form', () => {
    expect(formatHour(22)).toBe('10pm');
    expect(formatHour(9)).toBe('9am');
  });

  it('refuses an absent or impossible hour instead of printing one', () => {
    expect(formatHour(null)).toBeNull();
    expect(formatHour(undefined)).toBeNull();
    expect(formatHour(24)).toBeNull();
    expect(formatHour(-1)).toBeNull();
  });
});

describe('buildYearSlides', () => {
  it('tells the whole story when the year is full', () => {
    expect(buildYearSlides(makeYear({ top_artists: [
      { name: 'A', plays: 60, months_on_top: 3 },
      { name: 'B', plays: 20, months_on_top: 1 },
    ] }))).toEqual([
      'opening',
      'totals',
      'months',
      'top-artist',
      'artist-countdown',
      'top-albums',
      'top-track',
      'discoveries',
      'when',
      'card',
    ]);
  });

  it('stops at the opening when there is nothing to tell', () => {
    expect(buildYearSlides(makeYear({ has_data: false }))).toEqual(['opening']);
  });

  it('trusts the play count over the has_data flag', () => {
    const empty = makeYear({ totals: { plays: 0, minutes: 0, artists: 0, albums: 0, tracks: 0, active_days: 0 } });

    expect(buildYearSlides(empty)).toEqual(['opening']);
  });

  it('drops the month strip when no month has a single play', () => {
    const year = makeYear({
      months: [{ month: '2026-01', label: 'Jan', plays: 0, minutes: 0, top_artist: null }],
    });

    expect(buildYearSlides(year)).not.toContain('months');
  });

  it('does not run a top-five countdown on a single artist', () => {
    const slides = buildYearSlides(makeYear());

    expect(slides).toContain('top-artist');
    expect(slides).not.toContain('artist-countdown');
  });

  it('drops the discoveries slide when nothing was discovered', () => {
    expect(buildYearSlides(makeYear({ discoveries: [] }))).not.toContain('discoveries');
  });

  it('drops the album wall when no albums resolved', () => {
    // It is an ARTWORK slide. With nothing to show it is four grey squares.
    expect(buildYearSlides(makeYear({ top_albums: [] }))).not.toContain('top-albums');
  });

  it('keeps the when slide on an hour alone', () => {
    const year = makeYear({ peak_day: { date: null, plays: 0 } });

    expect(buildYearSlides(year)).toContain('when');
  });

  it('drops the when slide only when both halves are missing', () => {
    const year = makeYear({
      peak_day: { date: null, plays: 0 },
      top_hour: { hour: null, plays: 0 },
    });

    expect(buildYearSlides(year)).not.toContain('when');
  });

  it('keeps hour zero — midnight is a real answer, not a missing one', () => {
    const year = makeYear({
      peak_day: { date: null, plays: 0 },
      top_hour: { hour: 0, plays: 12 },
    });

    expect(buildYearSlides(year)).toContain('when');
  });

  it('survives a null payload rather than throwing on the way in', () => {
    expect(buildYearSlides(null)).toEqual(['opening']);
    expect(buildYearSlides(undefined)).toEqual(['opening']);
  });
});
