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
  /** a new episode or film airing in its premiere slot */
  premiere?: boolean;
}

/** something new, set to air at a fixed time (its premiere) */
export interface Premiere {
  item: GuideItem;
  /** epoch ms */
  at: number;
}

/** an episode premieres at 8pm, a film at 9pm */
const PREMIERE_HOUR = { episode: 20, movie: 21 } as const;

/**
 * when something new premieres: the evening of the day it arrived (or the
 * next one, if it came after its hour). worked out from when it was added,
 * so every screen puts it in the same slot.
 */
export function premiereAt(addedMs: number, kind: 'episode' | 'movie'): number {
  const hour = PREMIERE_HOUR[kind];
  const d = new Date(addedMs);
  const slot = new Date(d.getFullYear(), d.getMonth(), d.getDate(), hour, 0, 0, 0).getTime();
  return addedMs < slot
    ? slot
    : new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1, hour).getTime();
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

/**
 * a seeded random order drawn lazily: forward fisher-yates over a virtual
 * array (only the swapped slots are stored), so taking the 30 movies a day
 * needs costs 30 steps, not a shuffle of all 9,000.
 */
export function* seededOrder<T>(items: readonly T[], seed: number): Generator<T> {
  const n = items.length;
  const rand = seededRandom(seed);
  const swapped = new Map<number, number>();
  for (let i = 0; i < n; i++) {
    const j = i + Math.floor(rand() * (n - i));
    const atJ = swapped.get(j) ?? j;
    swapped.set(j, swapped.get(i) ?? i);
    yield items[atJ];
  }
}

const airableCache = new WeakMap<readonly GuideItem[], GuideItem[]>();

export function airable(items: readonly GuideItem[]): GuideItem[] {
  const hit = airableCache.get(items);
  if (hit) return hit;
  const out = airableUncached(items);
  airableCache.set(items, out);
  return out;
}

function airableUncached(items: readonly GuideItem[]): GuideItem[] {
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
  /**
   * what this profile has seen (item keys, "movie:12"): the unseen air
   * first each time round, and an in-order channel starts today at the
   * first one not seen. seenKey names that set, so a day is redone only
   * when it changes
   */
  seen?: ReadonlySet<string>;
  seenKey?: string;
  /**
   * a channel of movies and tv keeps to the clock: episodes by day, films
   * in the evening (6pm to 2am), the way a channel is programmed
   */
  daypart?: boolean;
  /** today's local midnight: where an in-order channel's "next unseen" starts from */
  today?: number;
  /**
   * episodes of a show air in blocks this long, in order (2: two back to
   * back), the way a channel runs a show, instead of one episode of
   * something, then one of something else
   */
  blocks?: number;
  /** what's new on this channel and when it airs; premiereKey names them */
  premieres?: readonly Premiere[];
  premiereKey?: string;
}

/** evening, film time: 6pm to 2am */
export function filmHour(at: number): boolean {
  const h = new Date(at).getHours();
  return h >= 18 || h < 2;
}

/**
 * one day of a channel, from local midnight until the pool runs past the
 * end of the day. an ordered pool continues from a day-based offset so an
 * episodic channel moves forward through the run day by day.
 */
const dayCache = new WeakMap<readonly GuideItem[], Map<string, GuideProgram[]>>();

/** a channel's day, computed once per pool: browsing or the half hour ticking reuses it */
export function scheduleDay(
  channelId: string,
  pool: readonly GuideItem[],
  day: Date,
  options: ScheduleOptions = {},
): GuideProgram[] {
  const key = `${channelId}|${startOfLocalDay(day.getTime())}|${options.ordered ? 1 : 0}|${
    options.daypart ? 1 : 0
  }|${options.seenKey ?? ''}|${options.today ?? ''}|${options.blocks ?? 1}|${options.premiereKey ?? ''}`;
  let days = dayCache.get(pool);
  if (!days) {
    days = new Map();
    dayCache.set(pool, days);
  }
  const hit = days.get(key);
  if (hit) return hit;
  const out = scheduleDayUncached(channelId, pool, day, options);
  if (days.size > 64) days.clear();
  days.set(key, out);
  return out;
}

function scheduleDayUncached(
  channelId: string,
  pool: readonly GuideItem[],
  day: Date,
  options: ScheduleOptions,
): GuideProgram[] {
  const items = airable(pool);
  if (!items.length) return [];
  const dayStart = startOfLocalDay(day.getTime());
  const dayEnd = dayStart + 24 * 60 * 60 * 1000;
  const seed = hashSeed(`${channelId}|${dayKey(new Date(dayStart))}`);

  const out: GuideProgram[] = [];
  let cursor = dayStart;
  // premieres hold their slot: what's on before one is cut at it, and two
  // at the same time air back to back
  const premieres = (options.premieres ?? [])
    .filter((p) => p.at >= dayStart && p.at < dayEnd && p.item.seconds >= MIN_SECONDS)
    .sort((a, b) => a.at - b.at);
  let nextPremiere = 0;
  const premiereNow = () => {
    while (nextPremiere < premieres.length && premieres[nextPremiere].at <= cursor) {
      const p = premieres[nextPremiere++];
      const seconds = Math.min(p.item.seconds, MAX_SECONDS);
      out.push({ ...p.item, seconds, start: cursor, end: cursor + seconds * 1000, premiere: true });
      cursor += seconds * 1000;
    }
  };
  const place = (item: GuideItem) => {
    premiereNow();
    if (cursor >= dayEnd) return;
    let end = cursor + item.seconds * 1000;
    const due = premieres[nextPremiere];
    if (due && due.at < end) end = due.at; // cut at the premiere
    if (end - cursor >= MIN_SECONDS * 1000) out.push({ ...item, start: cursor, end });
    cursor = Math.max(cursor, end);
    premiereNow();
  };
  // episodes run in blocks of this many, in order (the pool keeps each show's sample in airing order)
  const block = Math.max(1, Math.floor(options.blocks ?? 1));
  // blocks: a drawn episode stands for its show; the show's next episodes
  // not yet aired this time round go on, in order (the unseen first), so a
  // show runs in proper back to back blocks. nothing airs twice in a round.
  const aired = new Set<string>();
  const distinct = new Set(items.map((x) => x.key)).size;
  const episodesOf = new Map<string, GuideItem[]>();
  if (block > 1 && !options.ordered)
    for (const item of items)
      if (item.kind === 'episode' && item.href) {
        const list = episodesOf.get(item.href);
        if (list) list.push(item);
        else episodesOf.set(item.href, [item]);
      }
  const nextOf = (href: string): GuideItem | null => {
    const list = episodesOf.get(href) ?? [];
    const open = list.filter((x) => !aired.has(x.key));
    return open.find((x) => !options.seen?.has(x.key)) ?? open[0] ?? null;
  };
  const push = (item: GuideItem) => {
    if (block <= 1) {
      place(item);
      return;
    }
    // every push airs something: the caller loops until the day is full, so
    // a push that placed nothing would spin forever (it froze the tab)
    if (aired.size >= distinct) aired.clear();
    const show = item.kind === 'episode' && item.href ? item.href : null;
    let first: GuideItem =
      (show ? nextOf(show) : aired.has(item.key) ? null : item) ??
      // that show has had its turn this round: whatever hasn't, so nothing repeats early
      items.find((x) => !aired.has(x.key)) ??
      item;
    place(first);
    aired.add(first.key);
    for (let i = 1; i < block && show && cursor < dayEnd; i++) {
      const list = episodesOf.get(show)!;
      const after = list[list.indexOf(first) + 1];
      if (!after || aired.has(after.key)) break;
      place(after);
      aired.add(after.key);
      first = after;
    }
  };

  const seen = options.seen;
  if (options.ordered) {
    // days since the epoch in local time, so tomorrow starts further along
    const dayIndex = Math.round(dayStart / 86400000);
    let index = (hashSeed(channelId) + dayIndex * 7) % items.length;
    // knowing what you've seen: today starts at the first one you haven't,
    // and each day after carries on from where the day before ran out
    const firstUnseen =
      seen && options.today != null ? items.findIndex((x) => !seen.has(x.key)) : -1;
    if (firstUnseen >= 0) {
      const avg = items.reduce((n, x) => n + x.seconds, 0) / items.length;
      const perDay = Math.max(1, Math.round(86400 / avg));
      const todayIndex = Math.round(options.today! / 86400000);
      index =
        (((firstUnseen + (dayIndex - todayIndex) * perDay) % items.length) + items.length) %
        items.length;
    }
    while (cursor < dayEnd) {
      push(items[index]);
      index = (index + 1) % items.length;
    }
    return out;
  }

  // a draw: the unseen first, then what you've seen; each lap a fresh order
  // so repeats don't line up
  const lapOrder = (pool: readonly GuideItem[], lapSeed: number) => {
    if (!seen) return seededOrder(pool, lapSeed);
    const fresh = pool.filter((x) => !seen.has(x.key));
    const old = pool.filter((x) => seen.has(x.key));
    return (function* () {
      yield* seededOrder(fresh, lapSeed);
      yield* seededOrder(old, lapSeed + 7919);
    })();
  };
  const draw = (pool: readonly GuideItem[], salt: number) => {
    let lap = 0;
    let order = lapOrder(pool, seed + salt);
    return () => {
      for (let tries = 0; tries < 2; tries++) {
        const next = order.next();
        if (!next.done) return next.value;
        lap += 1;
        order = lapOrder(pool, seed + salt + lap);
      }
      return null;
    };
  };

  const films = options.daypart ? items.filter((x) => x.kind === 'movie') : [];
  const shows = options.daypart ? items.filter((x) => x.kind !== 'movie') : [];
  if (films.length && shows.length) {
    // a mixed channel keeps to the clock: films in the evening, episodes by day
    const film = draw(films, 1);
    const show = draw(shows, 2);
    while (cursor < dayEnd) {
      const next = filmHour(cursor) ? film() : show();
      if (!next) break;
      push(next);
    }
    return out;
  }

  const next = draw(items, 0);
  while (cursor < dayEnd) {
    const item = next();
    if (!item) break;
    push(item);
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
