import { describe, expect, it } from 'vitest';

import type { YearInListening } from './-year.types';

import { buildCalendar, buildYearSlides, calendarLevel, sharePercent } from './-year.helpers';

const BASE: YearInListening = {
  period: { start: '2026-01-01', end: '2026-12-03', label: 'Jan 2026 — Dec 2026', months: 12 },
  has_data: true,
  totals: { plays: 40, minutes: 120, artists: 3, albums: 2, tracks: 9, active_days: 4 },
  months: [{ month: '2026-05', label: 'May 2026', plays: 40, minutes: 120, top_artist: 'A' }],
  top_artists: [{ name: 'A', plays: 30, months_on_top: 1 }],
  top_albums: [],
  top_tracks: [],
  discoveries: [],
  peak_day: { date: '2026-05-02', plays: 20, minutes: 60, top_artist: 'A' },
  top_hour: { hour: 21, plays: 10 },
};

describe('the new slides', () => {
  it('adds genre, streak, busiest day and calendar when the year has them', () => {
    const slides = buildYearSlides({
      ...BASE,
      days: [
        { date: '2026-05-01', plays: 5, minutes: 15 },
        { date: '2026-05-02', plays: 20, minutes: 60 },
      ],
      streak: { days: 2, start: '2026-05-01', end: '2026-05-02' },
      top_genres: [{ genre: 'rock', plays: 30, share: 0.75 }],
    });
    expect(slides).toEqual([
      'opening',
      'totals',
      'months',
      'top-artist',
      'top-genre',
      'streak',
      'busiest-day',
      'calendar',
      'when',
      'card',
    ]);
  });

  it('leaves a one-day streak out: that is just a day with music', () => {
    const slides = buildYearSlides({
      ...BASE,
      days: [{ date: '2026-05-02', plays: 20, minutes: 60 }],
      streak: { days: 1, start: '2026-05-02', end: '2026-05-02' },
    });
    expect(slides).not.toContain('streak');
    expect(slides).toContain('calendar');
  });

  it('a year cached before the extras keeps the old combined slide', () => {
    const slides = buildYearSlides(BASE);
    expect(slides).not.toContain('busiest-day');
    expect(slides).not.toContain('calendar');
    expect(slides).toContain('when');
  });

  it('no hour and its own day slide: no empty "when" slide', () => {
    const slides = buildYearSlides({
      ...BASE,
      top_hour: { hour: null, plays: 0 },
      days: [{ date: '2026-05-02', plays: 20, minutes: 60 }],
    });
    expect(slides).toContain('busiest-day');
    expect(slides).not.toContain('when');
  });
});

describe('the calendar', () => {
  it('lays every day out in Monday-first weeks, padding outside the year', () => {
    // 2026-01-01 is a Thursday
    const cal = buildCalendar(
      [{ date: '2026-01-02', plays: 4, minutes: 10 }],
      '2026-01-01',
      '2026-01-11',
    );
    expect(cal.weeks).toHaveLength(2);
    expect(cal.weeks[0][0]).toMatchObject({ date: '2025-12-29', inYear: false });
    expect(cal.weeks[0][3]).toMatchObject({ date: '2026-01-01', inYear: true, plays: 0, level: 0 });
    expect(cal.weeks[0][4]).toMatchObject({ date: '2026-01-02', plays: 4, level: 4 });
    expect(cal.weeks[1][6]).toMatchObject({ date: '2026-01-11', inYear: true });
    expect(cal.months).toEqual([{ week: 0, label: expect.any(String) }]);
  });

  it('shades by quarters of the busiest day and never hides a play', () => {
    expect(calendarLevel(0, 100)).toBe(0);
    expect(calendarLevel(1, 100)).toBe(1);
    expect(calendarLevel(50, 100)).toBe(2);
    expect(calendarLevel(75, 100)).toBe(3);
    expect(calendarLevel(76, 100)).toBe(4);
  });

  it('gives nothing for a broken window', () => {
    expect(buildCalendar([], 'nope', '2026-01-01').weeks).toEqual([]);
  });

  it('words a genre share as a whole percent, at least 1%', () => {
    expect(sharePercent(0.624)).toBe('62%');
    expect(sharePercent(0.001)).toBe('1%');
    expect(sharePercent(0)).toBe('0%');
  });
});
