import type { YearDay, YearInListening, YearSlideKind } from './-year.types';

/**
 * Pure core for the Year in Listening story.
 *
 * The one idea holding this file together: a Wrapped earns its format from a
 * SEQUENCE OF SINGLE-IDEA MOMENTS. That only works if every moment has
 * something to say — an empty slide is worse than a shorter story, because it
 * breaks the promise that each screen is worth advancing to. So the slide list
 * is DERIVED from the data rather than fixed, and every "is there anything
 * here" decision lives in `buildYearSlides` where it can be tested, not
 * scattered through JSX as `{x && <Slide/>}`.
 */

/** Minutes as the unit people actually feel. 1,440 minutes is a day. */
export function minutesToDays(minutes: number): number {
  if (!Number.isFinite(minutes) || minutes <= 0) return 0;
  return Math.round((minutes / 1440) * 10) / 10;
}

/**
 * "12,480 minutes" / "8.7 days" — whichever is the more legible at this size.
 *
 * Under a day, "0.3 days" is a worse sentence than the minute count it came
 * from, so the crossover is deliberate rather than a formatting preference.
 */
export function describeListeningTime(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes <= 0) return '0 minutes';
  const days = minutesToDays(minutes);
  if (days < 1) return `${Math.round(minutes).toLocaleString()} minutes`;
  return `${days.toLocaleString()} ${days === 1 ? 'day' : 'days'}`;
}

/** Bar height as a fraction of the tallest month, with a visible floor. */
export function monthBarHeight(plays: number, peak: number): number {
  if (!Number.isFinite(plays) || !Number.isFinite(peak)) return 0;
  if (plays <= 0 || peak <= 0) return 0;
  const FLOOR = 0.06;
  return FLOOR + (1 - FLOOR) * (plays / peak);
}

/** The busiest month's play count — the scale every bar is drawn against. */
export function peakMonthPlays(year: Pick<YearInListening, 'months'>): number {
  return (year.months ?? []).reduce((max, m) => (m.plays > max ? m.plays : max), 0);
}

/** '2026-05-20' -> 'Wednesday, 20 May'. Parsed as LOCAL, not UTC. */
export function formatStoryDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  // `new Date('2026-05-20')` is parsed as UTC and renders as the 19th west of
  // Greenwich. played_at is local wall-clock, so the parts are split by hand.
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  if (!match) return null;
  const [, y, m, d] = match;
  const date = new Date(Number(y), Number(m) - 1, Number(d));
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });
}

/** 22 -> '10pm'. Hour 0 is midnight, not "missing". */
export function formatHour(hour: number | null | undefined): string | null {
  if (hour === null || hour === undefined) return null;
  if (!Number.isInteger(hour) || hour < 0 || hour > 23) return null;
  if (hour === 0) return 'midnight';
  if (hour === 12) return 'noon';
  const suffix = hour < 12 ? 'am' : 'pm';
  const twelve = hour % 12;
  return `${twelve}${suffix}`;
}

/**
 * The slides this particular year can actually fill.
 *
 * `opening` and `card` always appear — they are the frame. Everything between
 * has to earn its place:
 *
 * - no plays at all → just the opening, which says so
 * - no month has a single play → no month strip (twelve empty bars is not a chart)
 * - fewer than two artists → no countdown (a "top 5" of one is a list of one)
 * - no albums resolved → no album wall (it is an artwork slide; with nothing
 *   to show it is four grey squares)
 * - nothing first played inside the window → no discoveries slide
 * - no peak day AND no top hour → no 'when' slide
 */
export function buildYearSlides(year: YearInListening | null | undefined): YearSlideKind[] {
  if (!year) return ['opening'];
  const slides: YearSlideKind[] = ['opening'];
  if (!year.has_data || (year.totals?.plays ?? 0) <= 0) return slides;

  slides.push('totals');

  if (peakMonthPlays(year) > 0) slides.push('months');

  const artists = year.top_artists ?? [];
  if (artists.length >= 1) slides.push('top-artist');
  if (artists.length >= 2) slides.push('artist-countdown');

  if ((year.top_albums ?? []).length >= 1) slides.push('top-albums');
  if ((year.top_tracks ?? []).length >= 1) slides.push('top-track');
  if ((year.top_genres ?? []).length >= 1) slides.push('top-genre');
  if ((year.discoveries ?? []).length >= 1) slides.push('discoveries');
  // a streak of one day is just a day with music
  if ((year.streak?.days ?? 0) >= 2) slides.push('streak');

  // the busiest day has its own slide once the payload says who led it;
  // a year cached before that keeps the old combined 'when' slide
  const hasPeakDay = Boolean(year.peak_day?.date);
  const hasHour = year.top_hour?.hour !== null && year.top_hour?.hour !== undefined;
  const ownDaySlide = hasPeakDay && Array.isArray(year.days);
  if (ownDaySlide) slides.push('busiest-day');
  if ((year.days ?? []).length >= 1) slides.push('calendar');
  if (hasHour || (hasPeakDay && !ownDaySlide)) slides.push('when');

  slides.push('card');
  return slides;
}

// ── the calendar ─────────────────────────────────────────────────────────────

export interface CalendarCell {
  date: string;
  plays: number;
  /** 0 = nothing played, 1..4 = how much, against the year's busiest day */
  level: 0 | 1 | 2 | 3 | 4;
  /** false for the padding days before the window opens or after today */
  inYear: boolean;
}

export interface YearCalendar {
  /** columns of seven days, Monday first */
  weeks: CalendarCell[][];
  /** the week a month starts in, for the labels along the top */
  months: { week: number; label: string }[];
  max: number;
}

function isoDay(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function parseDay(iso: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '');
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

/** How full a day's square is: quarters of the busiest day, never 0 for a play. */
export function calendarLevel(plays: number, max: number): CalendarCell['level'] {
  if (!Number.isFinite(plays) || plays <= 0 || max <= 0) return 0;
  const ratio = plays / max;
  if (ratio <= 0.25) return 1;
  if (ratio <= 0.5) return 2;
  if (ratio <= 0.75) return 3;
  return 4;
}

/**
 * Every day from `start` to `end` laid out as weeks, the way a contribution
 * calendar is. Days are local calendar days (parsed by hand, never as UTC).
 */
export function buildCalendar(days: YearDay[], start: string, end: string): YearCalendar {
  const from = parseDay(start);
  const to = parseDay(end);
  if (!from || !to || to < from) return { weeks: [], months: [], max: 0 };
  const plays = new Map((days ?? []).map((d) => [d.date, d.plays]));
  const max = (days ?? []).reduce((m, d) => (d.plays > m ? d.plays : m), 0);
  const cursor = new Date(from);
  // back to the Monday of the first week
  cursor.setDate(cursor.getDate() - ((cursor.getDay() + 6) % 7));
  const weeks: CalendarCell[][] = [];
  const months: YearCalendar['months'] = [];
  let lastMonth = -1;
  while (cursor <= to) {
    const week: CalendarCell[] = [];
    for (let i = 0; i < 7; i++) {
      const date = isoDay(cursor);
      const inYear = cursor >= from && cursor <= to;
      const n = inYear ? (plays.get(date) ?? 0) : 0;
      if (inYear && cursor.getDate() === 1 && cursor.getMonth() !== lastMonth) {
        months.push({
          week: weeks.length,
          label: cursor.toLocaleDateString(undefined, { month: 'short' }),
        });
        lastMonth = cursor.getMonth();
      }
      week.push({ date, plays: n, level: calendarLevel(n, max), inYear });
      cursor.setDate(cursor.getDate() + 1);
    }
    weeks.push(week);
  }
  return { weeks, months, max };
}

/** '62%' of plays, rounded, for the genre slide. */
export function sharePercent(share: number): string {
  if (!Number.isFinite(share) || share <= 0) return '0%';
  return `${Math.max(1, Math.round(share * 100))}%`;
}
