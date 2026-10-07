import { describe, expect, it } from 'vitest';

import { fireworksLevel, holidayOn, isChristmasDay, localDate, thanksgivingDate } from './holidays';

/** the holiday calendar: short windows, the right days, the moving ones right */
describe('holidayOn', () => {
  const id = (y: number, m: number, d: number, cc: string | null = 'US') =>
    holidayOn(y, m, d, cc)?.id ?? null;

  it('halloween is the last week of october, the 31st the big night', () => {
    expect(id(2026, 10, 23)).toBeNull();
    expect(id(2026, 10, 24)).toBe('halloween');
    expect(holidayOn(2026, 10, 31)?.isDay).toBe(true);
    expect(holidayOn(2026, 10, 30)?.isDay).toBe(false);
    expect(id(2026, 11, 1)).toBeNull();
  });

  it('thanksgiving: the fourth thursday in the us, the second monday in canada, nowhere else', () => {
    expect(thanksgivingDate(2026, 'US')).toEqual([11, 26]);
    expect(thanksgivingDate(2027, 'US')).toEqual([11, 25]);
    expect(thanksgivingDate(2026, 'CA')).toEqual([10, 12]);
    expect(thanksgivingDate(2026, 'DE')).toBeNull();
    expect(id(2026, 11, 23)).toBe('thanksgiving'); // the monday before
    expect(id(2026, 11, 27)).toBe('thanksgiving'); // black friday
    expect(id(2026, 11, 28)).toBeNull();
    expect(id(2026, 10, 12, 'CA')).toBe('thanksgiving');
    expect(id(2026, 11, 26, 'GB')).toBeNull();
  });

  it('lunar new year from the eve through the first days, from the table', () => {
    expect(id(2026, 2, 15)).toBeNull();
    expect(id(2026, 2, 16)).toBe('lunar-new-year'); // the eve
    expect(holidayOn(2026, 2, 17)?.isDay).toBe(true);
    expect(id(2026, 2, 21)).toBe('lunar-new-year');
    expect(id(2026, 2, 22)).toBeNull();
    expect(id(2028, 1, 26)).toBe('lunar-new-year'); // a january one
    expect(id(2041, 2, 1)).toBeNull(); // past the table: nothing, never a wrong guess
  });

  it('an ordinary day is no holiday', () => {
    expect(id(2026, 7, 14)).toBeNull();
  });
});

describe('localDate', () => {
  it("is the location's date, not the viewer's", () => {
    // 03:00 utc on nov 1 is still oct 31 evening in los angeles
    const now = Date.UTC(2026, 10, 1, 3, 0);
    expect(localDate(-25200, now)).toEqual([2026, 10, 31]);
    expect(localDate(0, now)).toEqual([2026, 11, 1]);
  });
});

describe('christmas and new year', () => {
  const id = (y: number, m: number, d: number) => holidayOn(y, m, d, 'US')?.id ?? null;

  it('christmas runs the week before through boxing day; the eve and the day are the big ones', () => {
    expect(id(2026, 12, 17)).toBeNull();
    expect(id(2026, 12, 18)).toBe('christmas');
    expect(holidayOn(2026, 12, 24)?.isDay).toBe(true);
    expect(holidayOn(2026, 12, 25)?.isDay).toBe(true);
    expect(holidayOn(2026, 12, 26)?.isDay).toBe(false);
    expect(id(2026, 12, 27)).toBeNull();
  });

  it('only christmas day itself snows', () => {
    expect(isChristmasDay(12, 25)).toBe(true);
    expect(isChristmasDay(12, 24)).toBe(false);
  });

  it("new year's is the eve and the day", () => {
    expect(id(2026, 12, 30)).toBeNull();
    expect(holidayOn(2026, 12, 31)?.id).toBe('new-year');
    expect(holidayOn(2026, 12, 31)?.isDay).toBe(true);
    expect(id(2027, 1, 1)).toBe('new-year');
    expect(id(2027, 1, 2)).toBeNull();
  });

  it('the fireworks build to a show either side of midnight', () => {
    expect(fireworksLevel(12, 31, 21 * 60)).toBe(0.35);
    expect(fireworksLevel(12, 31, 23 * 60 + 45)).toBe(1);
    expect(fireworksLevel(1, 1, 15)).toBe(1);
    expect(fireworksLevel(1, 1, 45)).toBe(0.35);
  });
});
