/**
 * The hero's words: a greeting and the library's size, from data the page
 * already holds (the db stats the library card publishes). No fetch of its
 * own, /api/database/stats is not free on a big library and must stay single.
 */

export function greetingForHour(hour: number): string {
  if (hour >= 5 && hour < 12) return 'good morning';
  if (hour >= 12 && hour < 18) return 'good afternoon';
  if (hour >= 18) return 'good evening';
  // 0-4: the overnight-automation crowd gets acknowledged.
  return 'up late?';
}

/** the greeting with the name worked in. a question keeps its mark at the
 * end: "up late, Boulder?" not "up late?, Boulder". */
export function greetingLine(greeting: string, name: string): string {
  if (!name) return greeting;
  if (greeting.endsWith('?')) return `${greeting.slice(0, -1)}, ${name}?`;
  return `${greeting}, ${name}`;
}

export interface HeroNumber {
  id: 'tracks' | 'albums' | 'artists';
  value: string;
  label: string;
}

/** The hero's three big numbers. Anything unknown is OMITTED, not zeroed —
 *  a fresh boot shows a bare greeting rather than "0 tracks". */
export function heroNumbers(
  stats: { tracks?: number | null; albums?: number | null; artists?: number | null } | null,
): HeroNumber[] {
  const out: HeroNumber[] = [];
  const add = (id: HeroNumber['id'], n: number | null | undefined, label: string) => {
    if (typeof n === 'number' && n > 0) out.push({ id, value: n.toLocaleString(), label });
  };
  add('tracks', stats?.tracks, 'Tracks');
  add('albums', stats?.albums, 'Albums');
  add('artists', stats?.artists, 'Artists');
  return out;
}

/** How many enrichment workers are actually running right now. 'active' is
 *  the one stateClass -dash.core assigns for running-and-not-paused. */
export function countBusyWorkers(pills: Record<string, { stateClass: string | null }>): number {
  return Object.values(pills).filter((pill) => pill.stateClass === 'active').length;
}
