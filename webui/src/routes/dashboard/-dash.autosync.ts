/**
 * Pure core for the dashboard's Auto Sync card — the mini, read-and-trigger
 * view of the Auto-Sync schedule board (auto-sync.js). The card reuses the
 * board's own state builder through the window seam
 * (buildAutoSyncScheduleState, auto-sync.js:471) so the schedule semantics
 * live in ONE place; this module only turns that state into rich rows.
 *
 * The formatters replicate the vanilla labels 1:1 where cited:
 * - intervalLabel     = autoSyncIntervalLabel (auto-sync.js:68)
 * - weeklyLabel       = autoSyncWeeklyLabel (auto-sync.js:146)
 * - sourceLabel       = autoSyncSourceLabel's map (auto-sync.js:161)
 * - SOURCE_LOGOS      = _AUTO_SYNC_SOURCE_LOGOS (auto-sync.js:184)
 * - health matching   = the parseInt playlist_id filter (auto-sync.js:2000)
 * - run deltas        = autoSyncDelta over before/after_json (auto-sync.js:1499)
 */

import { relativeTime } from './-dash.library';

// ── The seam state's shape (what the card reads of it) ──────────────────────

export interface AutoSyncSeamPipelineState {
  status?: string;
  progress?: number;
  phase?: string;
  [key: string]: unknown;
}

export interface AutoSyncSeamPlaylist {
  id: number | string;
  name?: string;
  custom_name?: string;
  image_url?: string | null;
  /** The server's single source of truth for what to show (web_server.py
   *  get_mirrored_playlists_endpoint: alias if set, else upstream name). */
  display_name?: string;
  source?: string;
  total_count?: number;
  in_library_count?: number;
  wishlisted_count?: number;
  pipeline_state?: AutoSyncSeamPipelineState | null;
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
  started_at?: string;
  before_json?: Record<string, unknown> | null;
  after_json?: Record<string, unknown> | null;
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

/** _AUTO_SYNC_SOURCE_LOGOS (auto-sync.js:184) — the same brand-chip URLs the
 *  board headers use, so the visual language stays consistent. */
export const AUTOSYNC_SOURCE_LOGOS: Record<string, string> = {
  spotify: '/static/img/brands/spotify.png',
  spotify_public: '/static/img/brands/spotify.png',
  tidal: '/static/img/brands/tidal.svg',
  youtube: '/static/img/brands/youtube.svg',
  deezer: '/static/img/brands/deezer.png',
  qobuz: '/static/img/brands/qobuz.svg',
  itunes_link: '/static/img/brands/itunes.png',
  lastfm: '/static/img/brands/lastfm.png',
  listenbrainz: '/static/img/brands/listenbrainz.png',
  soulsync_discovery: '/static/favicon.png',
};

/** The automations table stamps next_run as UTC 'YYYY-MM-DD HH:MM:SS'
 *  (automation_engine strptime + tzinfo=utc) — but Date.parse reads that
 *  format as LOCAL time, skewing every countdown by the viewer's UTC
 *  offset. Tag bare db stamps as UTC before parsing; ISO strings with
 *  timezone info pass through untouched. */
export function parseDbUtc(stamp: string): number {
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(stamp)) {
    return Date.parse(stamp.replace(' ', 'T') + 'Z');
  }
  return Date.parse(stamp);
}

/** "in 12m" / "in 3h" / "in 2d" / "due now"; null when absent or unparseable. */
export function nextRunText(nextRun: string | null | undefined, nowMs: number): string | null {
  if (!nextRun) return null;
  const t = parseDbUtc(nextRun);
  if (!Number.isFinite(t)) return null;
  const diffMin = Math.floor((t - nowMs) / 60000);
  if (diffMin <= 0) return 'due now';
  if (diffMin < 60) return `in ${diffMin}m`;
  const hrs = Math.floor(diffMin / 60);
  if (hrs < 24) return `in ${hrs}h`;
  return `in ${Math.floor(hrs / 24)}d`;
}

// ── Row assembly ────────────────────────────────────────────────────────────

export interface AutoSyncRowCoverage {
  inLibrary: number;
  total: number;
  /** 0–100, rounded like the syncs card's pct. */
  pct: number;
}

export interface AutoSyncRowLastRun {
  ok: boolean;
  /** "2h ago" via the shared sync-card clock; '' when the stamp is missing. */
  ago: string;
  /** "+3 to library" — the in_library delta from the run's before/after
   *  snapshots; null when the snapshots are absent or the delta is 0. */
  delta: string | null;
}

export interface AutoSyncRowRunning {
  phase: string;
  /** 0–100. */
  progress: number;
}

export interface AutoSyncCardRow {
  /** The schedule's board key (mirrored playlist id or synthetic row id). */
  key: string;
  automationId: number | string;
  name: string;
  sourceKey: string;
  source: string;
  logo: string | null;
  /** The mirrored playlist's own cover art, when the source supplied one. */
  imageUrl: string | null;
  cadence: string;
  enabled: boolean;
  nextRun: string | null;
  coverage: AutoSyncRowCoverage | null;
  lastRun: AutoSyncRowLastRun | null;
  running: AutoSyncRowRunning | null;
}

function lastRunFor(
  key: string,
  history: AutoSyncSeamHistoryEntry[],
  nowMs: number,
): AutoSyncRowLastRun | null {
  const id = parseInt(key, 10);
  if (!Number.isFinite(id)) return null;
  // History arrives newest-first; the first match is the latest run — the
  // same parseInt equality the board's per-row history filter uses (:2000).
  const entry = (history || []).find((h) => parseInt(String(h.playlist_id), 10) === id);
  if (!entry) return null;
  const status = entry.status || '';
  const ok = status === 'completed' || status === 'finished';
  const before = (entry.before_json || {}) as Record<string, unknown>;
  const after = (entry.after_json || {}) as Record<string, unknown>;
  const libDelta = Number(after.in_library_count ?? NaN) - Number(before.in_library_count ?? NaN);
  const delta = Number.isFinite(libDelta) && libDelta > 0 ? `+${libDelta} to library` : null;
  return {
    ok,
    ago: entry.started_at ? relativeTime(entry.started_at, nowMs) : '',
    delta,
  };
}

function runningFor(playlist: AutoSyncSeamPlaylist | undefined): AutoSyncRowRunning | null {
  const state = playlist?.pipeline_state;
  if (!state || state.status !== 'running') return null;
  return {
    phase: String(state.phase || 'Running pipeline...'),
    progress: Math.max(0, Math.min(100, Number(state.progress) || 0)),
  };
}

function coverageFor(playlist: AutoSyncSeamPlaylist | undefined): AutoSyncRowCoverage | null {
  if (!playlist) return null;
  const total = Number(playlist.total_count) || 0;
  if (total <= 0) return null;
  const inLibrary = Math.max(0, Math.min(total, Number(playlist.in_library_count) || 0));
  return { inLibrary, total, pct: Math.round((inLibrary / total) * 100) };
}

/**
 * Flatten the seam state into the card's rows: one row per schedule entry
 * (a playlist carrying BOTH an hourly and a weekly automation honestly shows
 * twice). Sorted: running first, then enabled by soonest next run (unknown
 * last), disabled at the bottom — "what's happening / what fires next" reads
 * top-down.
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
    const playlist = (state.playlists || []).find((pl) => String(pl.id) === String(key));
    const sourceKey = playlist?.source || '';
    rows.push({
      key,
      automationId,
      name: String(
        playlist?.display_name ||
          playlist?.custom_name ||
          playlist?.name ||
          automationName ||
          `Playlist #${key}`,
      ),
      sourceKey,
      source: playlist ? sourceLabel(sourceKey) : '',
      logo: AUTOSYNC_SOURCE_LOGOS[sourceKey] || null,
      imageUrl: playlist?.image_url || null,
      cadence,
      enabled,
      nextRun: nextRunText(nextRun, nowMs),
      coverage: coverageFor(playlist),
      lastRun: lastRunFor(key, state.runHistory, nowMs),
      running: runningFor(playlist),
    });
  };

  for (const [key, s] of Object.entries(state.playlistSchedules || {})) {
    push(key, s.automation_id, s.automation_name, intervalLabel(s.hours), s.enabled, s.next_run);
  }
  for (const [key, s] of Object.entries(state.weeklySchedules || {})) {
    push(key, s.automation_id, s.automation_name, weeklyLabel(s.time, s.days), s.enabled, s.next_run);
  }

  const sortStamp = (r: AutoSyncCardRow) => {
    if (r.running) return -1;
    // Bucket by the display text — due now < minutes < hours < days < none.
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
