/**
 * The header's live status strip — the words that replaced "Music Dashboard".
 *
 * The old title/subtitle told the user what page they were on; this tells
 * them what their system is doing, from data the page already holds: db
 * stats the library card publishes, the enrichment pills' run state, and the
 * watchlist countdown the quick-nav already receives. No fetch of its own —
 * /api/database/stats is not free on a big library and must stay single.
 */

export function greetingForHour(hour: number): string {
  if (hour >= 5 && hour < 12) return 'good morning';
  if (hour >= 12 && hour < 18) return 'good afternoon';
  if (hour >= 18) return 'good evening';
  // 0-4: the overnight-automation crowd gets acknowledged.
  return 'up late?';
}

export interface HelloStat {
  id: 'tracks' | 'artists' | 'workers' | 'scan';
  label: string;
  /** navigateToPage target; absent = opens the enrichment manager instead. */
  page?: string;
}

/** Assemble the visible stat chips. Anything unknown is OMITTED, not zeroed —
 *  a fresh boot shows a bare greeting rather than "0 tracks". */
export function buildHelloStats(input: {
  tracks?: number | null;
  artists?: number | null;
  busyWorkers: number;
  scanCountdown?: string | null;
}): HelloStat[] {
  const out: HelloStat[] = [];
  if (typeof input.tracks === 'number' && input.tracks > 0) {
    out.push({ id: 'tracks', label: `${input.tracks.toLocaleString()} tracks`, page: 'library' });
  }
  if (typeof input.artists === 'number' && input.artists > 0) {
    out.push({
      id: 'artists',
      label: `${input.artists.toLocaleString()} artists`,
      page: 'library',
    });
  }
  if (input.busyWorkers > 0) {
    out.push({
      id: 'workers',
      label: input.busyWorkers === 1 ? '1 worker busy' : `${input.busyWorkers} workers busy`,
    });
  }
  if (input.scanCountdown) {
    out.push({ id: 'scan', label: `next scan in ${input.scanCountdown}`, page: 'watchlist' });
  }
  return out;
}

/** How many enrichment workers are actually running right now. 'active' is
 *  the one stateClass -dash.core assigns for running-and-not-paused. */
export function countBusyWorkers(
  pills: Record<string, { stateClass: string | null }>,
): number {
  return Object.values(pills).filter((pill) => pill.stateClass === 'active').length;
}
