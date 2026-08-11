/**
 * Pure core for the dashboard's Auto Sync card — the mini, read-and-trigger
 * view of the Auto-Sync schedule board (auto-sync.js). The card reuses the
 * board's own state builder through the window seam
 * (buildAutoSyncScheduleState, auto-sync.js:471) so the schedule semantics
 * live in ONE place; this module only turns that state into compact rows.
 *
 * The formatters replicate the vanilla labels 1:1 where cited:
 * - intervalLabel     = autoSyncIntervalLabel (auto-sync.js:68)
 * - weeklyLabel       = autoSyncWeeklyLabel (auto-sync.js:146)
 * - sourceLabel       = autoSyncSourceLabel's map (auto-sync.js:161)
 * - health matching   = the parseInt playlist_id filter (auto-sync.js:2000)
 */

// ── The seam state's shape (what the card reads of it) ──────────────────────

export interface AutoSyncSeamPlaylist {
  id: number | string;
  name?: string;
  custom_name?: string;
  source?: string;
  [key: string]: unknown;
}

export interface AutoSyncSeamHourly {
  automation_id: number | string;
  automation_name?: string;
  hours: number;
  enabled: boolean;
  next_run?: string | null;
}

export interface AutoSyncSeamWeekly {
  automation_id: number | string;
  automation_name?: string;
  time: string;
  days: string[];
  enabled: boolean;
  next_run?: string | null;
}

export interface AutoSyncSeamHistoryEntry {
  playlist_id?: number | string;
  status?: string;
  completed_at?: string;
  started_at?: string;
  [key: string]: unknown;
}

export interface AutoSyncSeamState {
  playlists: AutoSyncSeamPlaylist[];
  playlistSchedules: Record<string, AutoSyncSeamHourly>;
  weeklySchedules: Record<string, AutoSyncSeamWeekly>;
  runHistory: AutoSyncSeamHistoryEntry[];
}

// ── Formatters ──────────────────────────────────────────────────────────────

/** autoSyncIntervalLabel (auto-sync.js:68), verbatim semantics. */
export function intervalLabel(hours: number): string {
  if (hours === 168) return 'Every week';
  if (hours >= 24) {
    const days = hours / 24;
    return `Every ${days} day${days === 1 ? '' : 's'}`;
  }
  return `Every ${hours} hour${hours === 1 ? '' : 's'}`;
}

const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const WEEKDAY_LABELS: Record<string, string> = {
  mon: 'Mon',
  tue: 'Tue',
  wed: 'Wed',
  thu: 'Thu',
  fri: 'Fri',
  sat: 'Sat',
  sun: 'Sun',
};

/** autoSyncWeeklyLabel (auto-sync.js:146): full/empty week → Daily, else the
 *  canonical Mon–Sun order regardless of how the days were toggled on. */
export function weeklyLabel(time: string, days: string[]): string {
  if (!Array.isArray(days) || days.length === 0 || days.length === 7) {
    return `Daily @ ${time}`;
  }
  const ordered = WEEKDAYS.filter((d) => days.includes(d));
  return `${ordered.map((d) => WEEKDAY_LABELS[d]).join(', ')} @ ${time}`;
}

/** autoSyncSourceLabel's map (auto-sync.js:161); unknown sources capitalize. */
export const AUTOSYNC_SOURCE_LABELS: Record<string, string> = {
  spotify: 'Spotify',
  spotify_public: 'Spotify Link',
  tidal: 'Tidal',
  youtube: 'YouTube',
  deezer: 'Deezer',
  qobuz: 'Qobuz',
  beatport: 'Beatport',
  file: 'File Imports',
  itunes_link: 'iTunes Link',
  listenbrainz: 'ListenBrainz',
  lastfm: 'Last.fm Radio',
  soulsync_discovery: 'SoulSync Discovery',
};

export function sourceLabel(source: string | undefined): string {
  if (!source) return '';
  if (AUTOSYNC_SOURCE_LABELS[source]) return AUTOSYNC_SOURCE_LABELS[source];
  return source.charAt(0).toUpperCase() + source.slice(1);
}

/** "in 12m" / "in 3h" / "in 2d" / "due now"; null when absent or unparseable. */
export function nextRunText(nextRun: string | null | undefined, nowMs: number): string | null {
  if (!nextRun) return null;
  const t = Date.parse(nextRun);
  if (!Number.isFinite(t)) return null;
  const diffMin = Math.floor((t - nowMs) / 60000);
  if (diffMin <= 0) return 'due now';
  if (diffMin < 60) return `in ${diffMin}m`;
  const hrs = Math.floor(diffMin / 60);
  if (hrs < 24) return `in ${hrs}h`;
  return `in ${Math.floor(hrs / 24)}d`;
}

// ── Row assembly ────────────────────────────────────────────────────────────

export type AutoSyncHealth = 'good' | 'bad' | 'none';

export interface AutoSyncCardRow {
  /** The schedule's board key (mirrored playlist id or synthetic row id). */
  key: string;
  automationId: number | string;
  name: string;
  source: string;
  cadence: string;
  enabled: boolean;
  nextRun: string | null;
  /** Outcome of the newest pipeline run recorded for this playlist. */
  health: AutoSyncHealth;
}

function healthFor(key: string, history: AutoSyncSeamHistoryEntry[]): AutoSyncHealth {
  const id = parseInt(key, 10);
  if (!Number.isFinite(id)) return 'none';
  // History arrives newest-first; the first match is the latest run — the
  // same parseInt equality the board's per-row history filter uses (:2000).
  const entry = (history || []).find((h) => parseInt(String(h.playlist_id), 10) === id);
  if (!entry) return 'none';
  const status = entry.status || '';
  if (status === 'completed' || status === 'finished') return 'good';
  if (status === 'error' || status === 'skipped') return 'bad';
  return 'none';
}

function rowName(
  key: string,
  playlists: AutoSyncSeamPlaylist[],
  automationName: string | undefined,
): { name: string; source: string } {
  const p = (playlists || []).find((pl) => String(pl.id) === String(key));
  if (p) {
    return {
      name: String(p.custom_name || p.name || automationName || `Playlist #${key}`),
      source: sourceLabel(p.source),
    };
  }
  return { name: String(automationName || `Playlist #${key}`), source: '' };
}

/**
 * Flatten the seam state into the card's rows: one row per schedule entry
 * (a playlist carrying BOTH an hourly and a weekly automation honestly shows
 * twice). Sorted: enabled first, then soonest next run (unknown last), then
 * name — "what fires next" reads top-down.
 */
export function autoSyncCardRows(state: AutoSyncSeamState, nowMs: number): AutoSyncCardRow[] {
  const rows: AutoSyncCardRow[] = [];
  const push = (
    key: string,
    automationId: number | string,
    automationName: string | undefined,
    cadence: string,
    enabled: boolean,
    nextRun: string | null | undefined,
  ) => {
    const { name, source } = rowName(key, state.playlists, automationName);
    rows.push({
      key,
      automationId,
      name,
      source,
      cadence,
      enabled,
      nextRun: nextRunText(nextRun, nowMs),
      health: healthFor(key, state.runHistory),
    });
  };

  for (const [key, s] of Object.entries(state.playlistSchedules || {})) {
    push(key, s.automation_id, s.automation_name, intervalLabel(s.hours), s.enabled, s.next_run);
  }
  for (const [key, s] of Object.entries(state.weeklySchedules || {})) {
    push(key, s.automation_id, s.automation_name, weeklyLabel(s.time, s.days), s.enabled, s.next_run);
  }

  const sortStamp = (r: AutoSyncCardRow) => {
    // Re-derive a comparable stamp from the display text's source order:
    // due now < minutes < hours < days < none. The raw next_run string is
    // gone by now, so rank the buckets — enough for a stable card order.
    if (r.nextRun === 'due now') return 0;
    if (r.nextRun && r.nextRun.endsWith('m')) return 1;
    if (r.nextRun && r.nextRun.endsWith('h')) return 2;
    if (r.nextRun && r.nextRun.endsWith('d')) return 3;
    return 4;
  };
  rows.sort((a, b) => {
    if (a.enabled !== b.enabled) return a.enabled ? -1 : 1;
    const sa = sortStamp(a);
    const sb = sortStamp(b);
    if (sa !== sb) return sa - sb;
    return a.name.localeCompare(b.name);
  });
  return rows;
}
