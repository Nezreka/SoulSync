/**
 * The calendar's pure logic: dates and windows, air times and time-of-day
 * bands, same-day drop grouping, the state vocabulary, and the "next up" pick.
 * No React, no fetch, so every rule here is unit tested on its own.
 */

import type { AcqState, CalendarEpisode, CalendarView } from './-calendar.types';

export const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
export const WD_FULL = [
  'Sunday',
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
];
export const MO = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];
export const MO_FULL = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];

// ── dates ────────────────────────────────────────────────────────────────────

/** 'YYYY-MM-DD' as a local midnight. */
export function parseISO(s: string): Date {
  const p = (s || '').split('-');
  return new Date(+p[0], (+p[1] || 1) - 1, +p[2] || 1);
}

export function isoOf(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function addDays(iso: string, n: number): string {
  const d = parseISO(iso);
  d.setDate(d.getDate() + n);
  return isoOf(d);
}

/** The Sunday on or before a day: weeks run Sun→Sat, like Sonarr's. */
export function weekStart(iso: string): string {
  const d = parseISO(iso);
  d.setDate(d.getDate() - d.getDay());
  return isoOf(d);
}

export function monthFirst(iso: string): string {
  const d = parseISO(iso);
  return isoOf(new Date(d.getFullYear(), d.getMonth(), 1));
}

/** What a view fetches: a week, or the six-week grid around a month. */
export function windowFor(view: CalendarView, anchor: string): { start: string; days: number } {
  if (view === 'month') return { start: weekStart(monthFirst(anchor)), days: 42 };
  return { start: weekStart(anchor), days: 7 };
}

/** The anchor one step back or forward: a week, or a month. */
export function stepAnchor(view: CalendarView, anchor: string, dir: -1 | 1): string {
  if (view === 'month') {
    const d = parseISO(monthFirst(anchor));
    return isoOf(new Date(d.getFullYear(), d.getMonth() + dir, 1));
  }
  return addDays(weekStart(anchor), dir * 7);
}

export function daysOf(start: string, days: number): string[] {
  return Array.from({ length: days }, (_, i) => addDays(start, i));
}

/** "This Week", "Next Week", "3 weeks ago", or for a month its name. */
export function windowTitle(view: CalendarView, anchor: string, today: string): string {
  if (view === 'month') return MO_FULL[parseISO(anchor).getMonth()];
  const weeks = Math.round(
    (parseISO(weekStart(anchor)).getTime() - parseISO(weekStart(today)).getTime()) / (7 * 86400000),
  );
  if (weeks === 0) return 'This Week';
  if (weeks === 1) return 'Next Week';
  if (weeks === -1) return 'Last Week';
  return weeks > 0 ? `In ${weeks} weeks` : `${-weeks} weeks ago`;
}

export function windowRange(view: CalendarView, anchor: string): string {
  if (view === 'month') return String(parseISO(anchor).getFullYear());
  const a = parseISO(weekStart(anchor));
  const b = parseISO(addDays(weekStart(anchor), 6));
  const left = `${MO[a.getMonth()]} ${a.getDate()}`;
  const right =
    a.getMonth() === b.getMonth() ? `${b.getDate()}` : `${MO[b.getMonth()]} ${b.getDate()}`;
  return `${left} – ${right}, ${b.getFullYear()}`;
}

// ── air times ────────────────────────────────────────────────────────────────

/**
 * TVDB air time → minutes from midnight, null if absent. Handles "21:00",
 * "21:00:00", "9:00 PM". 00:00 is TVDB's streaming placeholder, not a slot.
 */
export function airMins(s: string | null | undefined): number | null {
  if (!s) return null;
  const str = String(s).trim();
  const m = str.match(/^(\d{1,2}):(\d{2})/);
  if (!m) return null;
  let h = +m[1];
  const mi = +m[2];
  if (/pm/i.test(str) && h < 12) h += 12;
  if (/am/i.test(str) && h === 12) h = 0;
  if (h > 23 || mi > 59) return null;
  if (h === 0 && mi === 0) return null;
  return h * 60 + mi;
}

export function fmtMins(mins: number | null): string {
  if (mins == null) return '';
  const h = Math.floor(mins / 60);
  const mi = mins % 60;
  return `${h % 12 || 12}${mi ? `:${String(mi).padStart(2, '0')}` : ''} ${h >= 12 ? 'PM' : 'AM'}`;
}

/** Early-morning drops (a 3 AM streaming release) belong to the night before. */
export function shiftedMins(mins: number | null): number | null {
  return mins == null ? null : mins < 300 ? mins + 1440 : mins;
}

export function byAirTime(a: CalendarEpisode, b: CalendarEpisode): number {
  const x = shiftedMins(airMins(a.airs_time));
  const y = shiftedMins(airMins(b.airs_time));
  if (x !== y) {
    if (x == null) return 1;
    if (y == null) return -1;
    return x - y;
  }
  return (
    (a.show_title || '').localeCompare(b.show_title || '') ||
    a.season_number - b.season_number ||
    a.episode_number - b.episode_number
  );
}

export const BANDS = [
  { key: 'morning', label: 'Morning', lo: 300, hi: 720 },
  { key: 'afternoon', label: 'Afternoon', lo: 720, hi: 1020 },
  { key: 'prime', label: 'Prime time', lo: 1020, hi: 1260 },
  { key: 'late', label: 'Late night', lo: 1260, hi: 1740 },
  { key: 'anytime', label: 'Anytime', lo: null, hi: null },
] as const;

export type BandKey = (typeof BANDS)[number]['key'];

export function bandOf(mins: number | null): BandKey {
  const m = shiftedMins(mins);
  if (m == null) return 'anytime';
  for (const b of BANDS) {
    if (b.lo != null && b.hi != null && m >= b.lo && m < b.hi) return b.key;
  }
  return 'anytime';
}

// ── states ───────────────────────────────────────────────────────────────────

/** Reading order: what you have, what's moving, what wants you, what's ahead. */
export const STATE_ORDER: AcqState[] = [
  'owned',
  'downloading',
  'queued',
  'wanted',
  'failed',
  'missing',
  'unaired',
  'ignored',
  'unmatched',
];

export interface StateContext {
  /** the airing auto-wishlist automation is on */
  autoOn: boolean;
}

export function stateLabel(state: AcqState): string {
  switch (state) {
    case 'owned':
      return 'In library';
    case 'downloading':
      return 'Downloading';
    case 'queued':
      return 'Queued';
    case 'wanted':
      return 'Wanted';
    case 'failed':
      return 'Failed';
    case 'missing':
      return 'Missing';
    case 'unaired':
      return 'Upcoming';
    case 'ignored':
      return 'Not monitored';
    case 'unmatched':
      return 'Unmatched';
  }
}

/** The word a small card has room for. */
export function stateShort(
  ep: Pick<CalendarEpisode, 'acq' | 'progress'>,
  ctx: StateContext,
): string {
  if (ep.acq === 'downloading') return ep.progress != null ? `${ep.progress}%` : 'Downloading';
  if (ep.acq === 'unaired') return ctx.autoOn ? 'Auto' : 'Upcoming';
  if (ep.acq === 'ignored') return 'Skipped';
  return stateLabel(ep.acq);
}

function fmtEta(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '';
  if (seconds < 90) return 'under 2 min left';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min left`;
  return `${Math.round(seconds / 360) / 10} h left`;
}

/** One line saying why the state is what it is, for the agenda and the modal. */
export function whyLine(ep: CalendarEpisode, ctx: StateContext): string {
  switch (ep.acq) {
    case 'owned':
      return 'On your server';
    case 'downloading':
      return fmtEta(ep.eta_seconds) || 'Fetching now';
    case 'queued':
      return 'Waiting for a download slot';
    case 'wanted':
      return 'On the wishlist, being searched';
    case 'failed':
      return 'Searched and nothing passed your quality profile';
    case 'missing':
      return 'Aired, and nothing is looking for it';
    case 'unaired':
      return ctx.autoOn
        ? 'Grabs automatically after it airs'
        : 'Auto-wishlist is off, so nothing will grab it';
    case 'ignored':
      return 'You stopped monitoring this episode';
    case 'unmatched':
      return 'The show isn\u2019t matched, so nothing can look for it. Fix its match on the show page';
  }
}

export function countStates(eps: { acq: AcqState }[]): Map<AcqState, number> {
  const c = new Map<AcqState, number>();
  for (const e of eps) c.set(e.acq, (c.get(e.acq) ?? 0) + 1);
  return c;
}

/** [state, count] in reading order, zeroes dropped. */
export function orderedCounts(eps: { acq: AcqState }[]): [AcqState, number][] {
  const c = countStates(eps);
  return STATE_ORDER.filter((s) => c.get(s)).map((s) => [s, c.get(s) as number]);
}

// ── same-day drops ───────────────────────────────────────────────────────────

export interface EpisodeGroup {
  key: string;
  eps: CalendarEpisode[];
  /** the first episode, which a card titles itself after */
  lead: CalendarEpisode;
  /** the state the group shows: the first in reading order that any member has */
  acq: AcqState;
  /** members not all in that state */
  mixed: boolean;
  ownedCount: number;
}

/** A whole season dropped on one day (ZOMBIES S2 E1–19) is one card, not
 * nineteen. Same show, season and air time, three or more episodes. */
export const DROP_MIN = 3;

export function groupDrops(eps: CalendarEpisode[]): EpisodeGroup[] {
  const buckets = new Map<string, CalendarEpisode[]>();
  for (const e of eps) {
    const k = `${e.show_tmdb_id ?? e.show_title}|${e.season_number}|${e.air_date}|${e.airs_time ?? ''}`;
    const b = buckets.get(k);
    if (b) b.push(e);
    else buckets.set(k, [e]);
  }
  const out: EpisodeGroup[] = [];
  const done = new Set<string>();
  for (const e of eps) {
    const k = `${e.show_tmdb_id ?? e.show_title}|${e.season_number}|${e.air_date}|${e.airs_time ?? ''}`;
    if (done.has(k)) continue;
    const members = buckets.get(k) as CalendarEpisode[];
    if (members.length < DROP_MIN) {
      out.push(single(e));
      continue;
    }
    done.add(k);
    const sorted = [...members].sort((a, b) => a.episode_number - b.episode_number);
    const c = countStates(sorted);
    const acq = STATE_ORDER.find((s) => c.get(s)) as AcqState;
    out.push({
      key: `g:${k}`,
      eps: sorted,
      lead: sorted[0],
      acq,
      mixed: (c.get(acq) ?? 0) !== sorted.length,
      ownedCount: c.get('owned') ?? 0,
    });
  }
  return out;
}

function single(e: CalendarEpisode): EpisodeGroup {
  return {
    key: `e:${e.id}`,
    eps: [e],
    lead: e,
    acq: e.acq,
    mixed: false,
    ownedCount: e.acq === 'owned' ? 1 : 0,
  };
}

export function episodeCode(g: EpisodeGroup): string {
  const first = g.eps[0];
  if (g.eps.length === 1) return `S${first.season_number} · E${first.episode_number}`;
  return `S${first.season_number} · E${first.episode_number}–${g.eps[g.eps.length - 1].episode_number}`;
}

// ── next up ─────────────────────────────────────────────────────────────────

/** When it airs, in ms. No time means "sometime today": end of day, so a
 * streaming drop stays up next all day instead of expiring at midnight. */
export function airDateTime(ep: CalendarEpisode): number {
  const mins = airMins(ep.airs_time);
  return parseISO(ep.air_date).getTime() + (mins != null ? mins : 23 * 60 + 59) * 60000;
}

/** A show still on air counts as next up for this long after it starts. */
export const ON_AIR_GRACE_MS = 90 * 60000;

/**
 * Up to three episodes for the billboard, one per show. On the current week:
 * the next ones that haven't finished airing, moving through the day; once the
 * week has all aired, the latest. Any other week: its first few.
 */
export function featured(
  eps: CalendarEpisode[],
  now: number,
  currentWeek: boolean,
): CalendarEpisode[] {
  const sorted = [...eps].sort((a, b) => airDateTime(a) - airDateTime(b));
  let pool = sorted;
  if (currentWeek) {
    const upcoming = sorted.filter((e) => airDateTime(e) + ON_AIR_GRACE_MS >= now);
    pool = upcoming.length ? upcoming : sorted.reverse();
  }
  const seen = new Set<string>();
  const out: CalendarEpisode[] = [];
  for (const e of pool) {
    const k = String(e.show_tmdb_id ?? e.show_title);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(e);
    if (out.length === 3) break;
  }
  return out;
}

/** "Tonight, 8 PM", "Tomorrow, 9 PM", "On now", "Thursday". */
export function whenLabel(ep: CalendarEpisode, today: string, now: number): string {
  const mins = airMins(ep.airs_time);
  const diff = Math.round((parseISO(ep.air_date).getTime() - parseISO(today).getTime()) / 86400000);
  if (diff === 0 && mins != null) {
    const start = airDateTime(ep);
    if (now >= start && now < start + ON_AIR_GRACE_MS) return `On now · ${fmtMins(mins)}`;
  }
  const day =
    diff === 0
      ? mins != null && mins >= 17 * 60
        ? 'Tonight'
        : 'Today'
      : diff === 1
        ? 'Tomorrow'
        : diff === -1
          ? 'Yesterday'
          : WD_FULL[parseISO(ep.air_date).getDay()];
  return mins != null ? `${day}, ${fmtMins(mins)}` : day;
}

// ── misc ────────────────────────────────────────────────────────────────────

/** A stable per-show hue for the glow behind a card. */
export function showHue(title: string): number {
  let h = 0;
  for (let i = 0; i < (title || '').length; i++) h = (h * 31 + title.charCodeAt(i)) >>> 0;
  return h % 360;
}

/** "just now", "22 min ago", "5 h ago", "3 days ago" for a utc db timestamp. */
export function timeAgo(utc: string | null | undefined, now: number): string | null {
  if (!utc) return null;
  const t = Date.parse(String(utc).replace(' ', 'T') + (String(utc).endsWith('Z') ? '' : 'Z'));
  if (Number.isNaN(t)) return null;
  const s = Math.max(0, (now - t) / 1000);
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400 * 1.5) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export function isSpecial(ep: CalendarEpisode): boolean {
  return Number(ep.season_number) === 0;
}
