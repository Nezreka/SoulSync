/**
 * the guide's schedules: what a channel is airing at any moment.
 *
 * a channel is a pool of real items with real runtimes. each day the pool is
 * shuffled with a seed made of the channel id and the date, then laid end to
 * end from local midnight. the same channel on the same day always airs the
 * same thing at the same minute, on every screen, with nothing stored.
 *
 * the day's run continues past midnight into the next day's grid only as far
 * as the last program ends; the next day starts its own run at its midnight.
 */

export interface GuideItem {
  /** stable id within the item type, e.g. a movie id or an episode id */
  key: string;
  title: string;
  subtitle?: string;
  seconds: number;
  /** for linking the program to its detail page */
  href?: string;
  /** artwork, if the item has any */
  art?: string | null;
  /** what it is: movie, episode, youtube, track */
  kind?: string;
}

export interface GuideProgram extends GuideItem {
  /** epoch ms */
  start: number;
  end: number;
}

/** keeps a program long enough to see, and drops broken runtimes */
export const MIN_SECONDS = 60;
export const MAX_SECONDS = 6 * 60 * 60;

/** a small, fast, seedable hash (FNV-1a 32 bit) */
export function hashSeed(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** mulberry32: a deterministic 0..1 generator from a 32 bit seed */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** fisher-yates with a seeded generator; never mutates the input */
export function seededShuffle<T>(items: readonly T[], seed: number): T[] {
  const out = items.slice();
  const rand = seededRandom(seed);
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/** yyyy-mm-dd of a local date: the day part of the seed */
export function dayKey(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

export function startOfLocalDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

export function airable(items: readonly GuideItem[]): GuideItem[] {
  return items
    .filter((item) => Number.isFinite(item.seconds) && item.seconds >= MIN_SECONDS)
    .map((item) => (item.seconds > MAX_SECONDS ? { ...item, seconds: MAX_SECONDS } : item));
}

export interface ScheduleOptions {
  /**
   * keep the pool's own order instead of shuffling (a show channel airs its
   * episodes in order, picking up where yesterday left off).
   */
  ordered?: boolean;
}

/**
 * one day of a channel, from local midnight until the pool runs past the
 * end of the day. an ordered pool continues from a day-based offset so an
 * episodic channel moves forward through the run day by day.
 */
export function scheduleDay(
  channelId: string,
  pool: readonly GuideItem[],
  day: Date,
  options: ScheduleOptions = {},
): GuideProgram[] {
  const items = airable(pool);
  if (!items.length) return [];
  const dayStart = startOfLocalDay(day.getTime());
  const dayEnd = dayStart + 24 * 60 * 60 * 1000;
  const seed = hashSeed(`${channelId}|${dayKey(new Date(dayStart))}`);

  let order: GuideItem[];
  if (options.ordered) {
    // days since the epoch in local time, so tomorrow starts further along
    const dayIndex = Math.round(dayStart / 86400000);
    const offset = (hashSeed(channelId) + dayIndex * 7) % items.length;
    order = [...items.slice(offset), ...items.slice(0, offset)];
  } else {
    order = seededShuffle(items, seed);
  }

  const out: GuideProgram[] = [];
  let cursor = dayStart;
  let index = 0;
  // a short pool repeats; a reshuffle per lap keeps repeats from lining up
  let lap = 0;
  while (cursor < dayEnd) {
    if (index >= order.length) {
      index = 0;
      lap += 1;
      if (!options.ordered) order = seededShuffle(items, seed + lap);
    }
    const item = order[index++];
    const end = cursor + item.seconds * 1000;
    out.push({ ...item, start: cursor, end });
    cursor = end;
  }
  return out;
}

/**
 * the programs overlapping [from, to): yesterday's run can still be airing
 * after midnight, so the window looks at the day before too.
 */
export function programsInWindow(
  channelId: string,
  pool: readonly GuideItem[],
  from: number,
  to: number,
  options: ScheduleOptions = {},
): GuideProgram[] {
  if (to <= from) return [];
  const out: GuideProgram[] = [];
  let dayStart = startOfLocalDay(from) - 24 * 60 * 60 * 1000;
  while (dayStart < to) {
    const programs = scheduleDay(channelId, pool, new Date(dayStart), options);
    const nextDay = startOfLocalDay(dayStart + 36 * 60 * 60 * 1000);
    for (const program of programs) {
      // a run that spills past midnight is cut where the next day's run starts
      const end = Math.min(program.end, nextDay);
      if (program.start >= nextDay) break;
      if (end > from && program.start < to) out.push({ ...program, end });
    }
    dayStart = nextDay;
  }
  return out;
}

/** what is on right now, and how far into it */
export function nowAiring(
  channelId: string,
  pool: readonly GuideItem[],
  now: number,
  options: ScheduleOptions = {},
): { program: GuideProgram; offsetSeconds: number } | null {
  const programs = programsInWindow(channelId, pool, now, now + 1, options);
  const program = programs.find((p) => p.start <= now && p.end > now);
  if (!program) return null;
  return { program, offsetSeconds: Math.floor((now - program.start) / 1000) };
}

export interface GuideBlock {
  start: number;
  end: number;
  items: GuideProgram[];
}

/**
 * songs are three minutes; a grid cell that small says nothing. music
 * channels air in blocks: consecutive songs gathered until the block is at
 * least ``minSeconds`` long, so a row reads like radio (a half-hour set) and
 * a block opens into its track list.
 */
export function mergeIntoBlocks(
  programs: readonly GuideProgram[],
  minSeconds = 1800,
): GuideBlock[] {
  const out: GuideBlock[] = [];
  let current: GuideBlock | null = null;
  for (const p of programs) {
    if (!current || current.end !== p.start || current.end - current.start >= minSeconds * 1000) {
      current = { start: p.start, end: p.end, items: [p] };
      out.push(current);
    } else {
      current.items.push(p);
      current.end = p.end;
    }
  }
  return out;
}

/** "Drake, SZA and 9 more" for a block's cell */
export function blockArtists(block: GuideBlock, show = 2): string {
  const names: string[] = [];
  for (const item of block.items) {
    const n = item.subtitle?.trim();
    if (n && !names.includes(n)) names.push(n);
  }
  if (names.length <= show) return names.join(', ');
  return `${names.slice(0, show).join(', ')} and ${names.length - show} more`;
}
