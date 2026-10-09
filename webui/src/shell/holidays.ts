/**
 * which holiday it is, if any. windows are short on purpose (about a week,
 * the day itself a little special) so it stays a treat, not wallpaper.
 *
 * dates are the location's local calendar date. the moving holidays are
 * worked out (thanksgiving) or looked up (lunar new year, which follows the
 * lunisolar calendar and has no simple formula).
 */

export type HolidayId = 'halloween' | 'thanksgiving' | 'christmas' | 'new-year' | 'lunar-new-year';

export interface HolidayInfo {
  id: HolidayId;
  label: string;
  /** true on the day itself (or its eve, where the eve is the big night) */
  isDay: boolean;
}

export const HOLIDAY_LABELS: Record<HolidayId, string> = {
  halloween: 'Halloween',
  thanksgiving: 'Thanksgiving',
  christmas: 'Christmas',
  'new-year': "New Year's",
  'lunar-new-year': 'Lunar New Year',
};

/** first day of lunar new year, through 2040 */
const LUNAR_NEW_YEAR: Record<number, [number, number]> = {
  2026: [2, 17],
  2027: [2, 6],
  2028: [1, 26],
  2029: [2, 13],
  2030: [2, 3],
  2031: [1, 23],
  2032: [2, 11],
  2033: [1, 31],
  2034: [2, 19],
  2035: [2, 8],
  2036: [1, 28],
  2037: [2, 15],
  2038: [2, 4],
  2039: [1, 24],
  2040: [2, 12],
};

/** a calendar date as a day number, so windows can cross months */
function dayNumber(y: number, m: number, d: number): number {
  return Math.floor(Date.UTC(y, m - 1, d) / 86_400_000);
}

/** the nth weekday (0 = sunday) of a month, 1-based n */
function nthWeekday(y: number, m: number, weekday: number, n: number): number {
  const first = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
  return 1 + ((weekday - first + 7) % 7) + (n - 1) * 7;
}

export function thanksgivingDate(
  year: number,
  countryCode: string | null,
): [number, number] | null {
  const cc = (countryCode || '').toUpperCase();
  if (cc === 'US') return [11, nthWeekday(year, 11, 4, 4)]; // 4th thursday of november
  if (cc === 'CA') return [10, nthWeekday(year, 10, 1, 2)]; // 2nd monday of october
  return null;
}

/**
 * the holiday on a calendar date, or null. `countryCode` scopes the ones
 * that are national (thanksgiving: us and canada, different days).
 */
export function holidayOn(
  y: number,
  m: number,
  d: number,
  countryCode: string | null = null,
): HolidayInfo | null {
  const today = dayNumber(y, m, d);

  // halloween: the last week of october, the 31st the big night
  const halloween = dayNumber(y, 10, 31);
  if (today >= halloween - 7 && today <= halloween) {
    return { id: 'halloween', label: HOLIDAY_LABELS.halloween, isDay: today === halloween };
  }

  const tg = thanksgivingDate(y, countryCode);
  if (tg) {
    const day = dayNumber(y, tg[0], tg[1]);
    if (today >= day - 3 && today <= day + 1) {
      return { id: 'thanksgiving', label: HOLIDAY_LABELS.thanksgiving, isDay: today === day };
    }
  }

  // christmas: the week before through boxing day; the eve and the day are
  // the big ones (santa flies the eve, the day itself always snows)
  if (m === 12 && d >= 18 && d <= 26) {
    return { id: 'christmas', label: HOLIDAY_LABELS.christmas, isDay: d === 24 || d === 25 };
  }

  // new year's: the eve and the day, the eve the big night
  if ((m === 12 && d === 31) || (m === 1 && d === 1)) {
    return { id: 'new-year', label: HOLIDAY_LABELS['new-year'], isDay: m === 12 };
  }

  // lunar new year: from the eve through the first few days. january dates
  // can belong to this year's festival, so check this year's start only
  const lny = LUNAR_NEW_YEAR[y];
  if (lny) {
    const day = dayNumber(y, lny[0], lny[1]);
    if (today >= day - 1 && today <= day + 4) {
      return {
        id: 'lunar-new-year',
        label: HOLIDAY_LABELS['lunar-new-year'],
        isDay: today === day - 1 || today === day,
      };
    }
  }
  return null;
}

/** the location's local calendar date from the server's utc offset */
export function localDate(
  utcOffsetSeconds: number,
  nowMs: number = Date.now(),
): [number, number, number] {
  const d = new Date(nowMs + utcOffsetSeconds * 1000);
  return [d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate()];
}

/** christmas day: the one day it snows no matter what the sky says */
export function isChristmasDay(m: number, d: number): boolean {
  return m === 12 && d === 25;
}

/**
 * how busy the new year's fireworks are: a fuller show in the half hour
 * either side of midnight on the eve, a gentle scatter the rest of the night
 */
export function fireworksLevel(m: number, d: number, minutes: number): number {
  const nearMidnight =
    (m === 12 && d === 31 && minutes >= 23 * 60 + 30) || (m === 1 && d === 1 && minutes <= 30);
  return nearMidnight ? 1 : 0.35;
}
