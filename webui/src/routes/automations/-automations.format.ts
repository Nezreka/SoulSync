import { type Automation, type AutomationBlocks, BLOCK_CATEGORIES } from './-automations.types';

/**
 * Parse a server timestamp as UTC.
 *
 * The API hands back SQLite datetimes with no zone ("2026-07-27 02:10:37").
 * `new Date(naive)` reads those as LOCAL time, so every relative label is wrong
 * by the viewer's offset — "2h ago" for something that just ran. Timestamps
 * that already carry a zone are parsed as-is.
 */
export function parseServerTime(ts: string): number {
  if (/[Zz]$/.test(ts) || /[+-]\d{2}:\d{2}$/.test(ts)) return new Date(ts).getTime();
  return new Date(`${ts}Z`).getTime();
}

/** "just now" / "5m ago" / "3h ago" / "2d ago"; "Never" when unset. */
export function timeAgo(ts: string | null | undefined, now: number = Date.now()): string {
  if (!ts) return 'Never';
  const d = (now - parseServerTime(ts)) / 1000;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

/**
 * "in 30s" / "in 5m" / "in 2h" / "in 3d"; "soon" once due, "" when unset.
 *
 * Seconds and minutes round UP and hours/days round to NEAREST — that asymmetry
 * is deliberate in the original: a countdown that reads "in 0m" while still
 * waiting looks broken, but "in 2h" for 1h50m does not.
 */
export function timeUntil(ts: string | null | undefined, now: number = Date.now()): string {
  if (!ts) return '';
  const d = (parseServerTime(ts) - now) / 1000;
  if (d <= 0) return 'soon';
  if (d < 60) return `in ${Math.ceil(d)}s`;
  if (d < 3600) return `in ${Math.ceil(d / 60)}m`;
  if (d < 86400) return `in ${Math.round(d / 3600)}h`;
  return `in ${Math.round(d / 86400)}d`;
}

/** Last-resort label: 'video_deep_scan_tv' -> 'Deep Scan Tv'. Never show raw snake_case. */
export function humanizeType(type: string | null | undefined): string {
  return (
    String(type || '')
      .replace(/^(video|music)_/, '')
      .split('_')
      .filter(Boolean)
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ') || 'Unknown'
  );
}

/**
 * The compact "last run" summary on a card.
 *
 * Up to three scalar facts from last_result; full detail lives in the history
 * modal. `status` is skipped because the status dot already says it, and `_`
 * keys are internal. Zero numbers and the strings '' / '0' are dropped as
 * noise, and strings over 24 chars are too long to sit on one line.
 *
 * Truncation is done HERE at a fixed length rather than in CSS: the original
 * tried two CSS approaches and both failed differently, so the cut is
 * deterministic and layout-independent. Callers put `full` in the tooltip.
 */
export function lastResultFacts(lastResult: unknown): { shown: string; full: string } | null {
  // The vanilla check was `typeof === 'object'`, which lets an ARRAY through
  // and renders index-keyed nonsense ("0: foo · 1: bar"). Results are always
  // dicts, so this cannot trigger in practice; rejecting arrays is a
  // deliberate, strictly-safer deviation rather than an oversight.
  if (!lastResult || typeof lastResult !== 'object' || Array.isArray(lastResult)) return null;

  const facts = Object.entries(lastResult as Record<string, unknown>)
    .filter(
      ([k, v]) =>
        k !== 'status' &&
        !k.startsWith('_') &&
        ((typeof v === 'number' && v !== 0) ||
          (typeof v === 'string' && v.length <= 24 && v !== '' && v !== '0')),
    )
    .slice(0, 3)
    .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${String(v).replace(/_/g, ' ')}`);

  if (!facts.length) return null;
  const full = facts.join(' · ');
  const shown = full.length > 64 ? `${full.slice(0, 61).trimEnd()}…` : full;
  return { shown, full };
}

/**
 * What the last run actually accomplished, in a sentence.
 *
 * `lastResultFacts` prints the handler's own dict keys — "tracks added: 4 ·
 * playlists: 2". Honest, but it is the internals leaking: those keys were
 * named for the code that sets them, not for the person reading the card.
 *
 * Only these action types get a sentence, and each one is built ONLY from
 * keys its handler verifiably returns (see the key list beside each entry;
 * `tests/test_automation_result_keys.py` fails if a handler stops returning
 * one). Everything else falls through to the generic facts — inventing a
 * sentence for a handler whose shape I had not read would be worse than the
 * raw keys, because it would read as authoritative.
 */
const num = (v: unknown): number => {
  // Several handlers stringify their counters ('refreshed': str(n)).
  const n = typeof v === 'number' ? v : Number.parseFloat(String(v ?? ''));
  return Number.isFinite(n) ? n : 0;
};

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

type Sentence = (r: Record<string, unknown>) => string | null;

const ACTION_SENTENCES: Record<string, Sentence> = {
  // artists_scanned / new_tracks_found / tracks_added_to_wishlist
  scan_watchlist: (r) => {
    const artists = num(r.artists_scanned);
    const wishlisted = num(r.tracks_added_to_wishlist);
    const found = num(r.new_tracks_found);
    if (!artists && !wishlisted && !found) return null;
    const parts = [`Checked ${plural(artists, 'artist')}`];
    if (wishlisted) parts.push(`wishlisted ${plural(wishlisted, 'track')}`);
    else if (found) parts.push(`found ${plural(found, 'new track')}`);
    else parts.push('nothing new');
    return parts.join(', ');
  },
  // files_scanned / duplicates_found / files_deleted / space_freed_mb
  run_duplicate_cleaner: (r) => {
    const scanned = num(r.files_scanned);
    const removed = num(r.files_deleted);
    const found = num(r.duplicates_found);
    if (!scanned && !found) return null;
    if (!found) return `Checked ${plural(scanned, 'file')}, no duplicates`;
    const freed = num(r.space_freed_mb);
    const tail = removed ? `removed ${plural(removed, 'file')}` : `${found} to review`;
    return freed ? `Found ${found} duplicates, ${tail} (${freed} MB)` : `Found ${found} duplicates, ${tail}`;
  },
  // refreshed / errors
  refresh_mirrored: (r) => {
    const refreshed = num(r.refreshed);
    const errors = num(r.errors);
    if (!refreshed && !errors) return null;
    const base = `Refreshed ${plural(refreshed, 'playlist')}`;
    return errors ? `${base}, ${plural(errors, 'error')}` : base;
  },
  // artists / albums / tracks
  start_database_update: (r) => {
    const tracks = num(r.tracks);
    const artists = num(r.artists);
    if (!tracks && !artists) return null;
    return `Library now ${plural(artists, 'artist')}, ${plural(tracks, 'track')}`;
  },
};

export interface AutomationOutcome {
  text: string;
  /** Drives the tone: a skip is neither success nor failure. */
  kind: 'ok' | 'skipped';
}

/**
 * The last-run line for a card.
 *
 * A non-completed status is handled first and explicitly. It used to fall into
 * the generic facts, where a skip rendered as "reason: Duplicate cleaner is
 * already running" — the word "reason" is a dict key, not something a person
 * would say.
 */
export function automationOutcome(
  actionType: string | null | undefined,
  lastResult: unknown,
): AutomationOutcome | null {
  if (!lastResult || typeof lastResult !== 'object' || Array.isArray(lastResult)) return null;
  const result = lastResult as Record<string, unknown>;
  const status = String(result.status ?? '');

  if (status === 'skipped') {
    const why = String(result.reason ?? '').trim();
    return { text: why ? `Skipped — ${why}` : 'Skipped', kind: 'skipped' };
  }
  // 'started'/'running' mean the handler handed off to a worker and the real
  // outcome lands later; claiming a result would be a guess.
  if (status === 'started' || status === 'running') {
    return { text: 'Handed off — still working', kind: 'skipped' };
  }

  const sentence = ACTION_SENTENCES[actionType ?? '']?.(result);
  if (sentence) return { text: sentence, kind: 'ok' };

  const facts = lastResultFacts(lastResult);
  return facts ? { text: facts.shown, kind: 'ok' } : null;
}

/** The action types that get a hand-written sentence. Exported for the
 *  cross-language test that checks their handlers still return those keys. */
export const SENTENCE_ACTION_TYPES = Object.keys(ACTION_SENTENCES);

/** Triggers driven by a timer — these are the only ones that show "Next:". */
export const TIMER_TRIGGERS = ['schedule', 'daily_time', 'weekly_time', 'monthly_time'] as const;

export function isTimerTrigger(type: string | null | undefined): boolean {
  return (TIMER_TRIGGERS as readonly string[]).includes(type ?? '');
}

const TRIGGER_LABELS: Record<string, string> = {
  app_started: 'App Started',
  track_downloaded: 'Track Downloaded',
  batch_complete: 'Batch Complete',
  watchlist_new_release: 'New Release Found',
  playlist_synced: 'Playlist Synced',
  playlist_changed: 'Playlist Changed',
  discovery_completed: 'Discovery Complete',
  wishlist_processing_completed: 'Wishlist Processed',
  watchlist_scan_completed: 'Watchlist Scan Done',
  database_update_completed: 'Database Updated',
  download_failed: 'Download Failed',
  download_quarantined: 'File Quarantined',
  wishlist_item_added: 'Wishlist Item Added',
  watchlist_artist_added: 'Artist Watched',
  watchlist_artist_removed: 'Artist Unwatched',
  import_completed: 'Import Complete',
  mirrored_playlist_created: 'Playlist Mirrored',
  quality_scan_completed: 'Quality Scan Done',
  duplicate_scan_completed: 'Duplicate Scan Done',
  library_scan_completed: 'Library Scan Done',
  signal_received: 'Signal Received',
};

/**
 * Human label for a trigger.
 *
 * `blockLabel` supplies the label for anything not in the map — video triggers,
 * monthly_time, webhook_received — from the fetched block definitions. Mapped
 * labels always win, matching the vanilla precedence.
 */
export function formatTrigger(
  type: string | null | undefined,
  config: Record<string, unknown> | null | undefined,
  blockLabel?: (type: string) => string | undefined,
): string {
  const cfg = (config ?? {}) as Record<string, unknown>;

  if (type === 'schedule' && config) {
    return `Every ${cfg.interval ?? 1} ${cfg.unit ?? 'hours'}`;
  }
  if (type === 'daily_time' && config) {
    return `Daily at ${cfg.time ?? '00:00'}`;
  }
  if (type === 'weekly_time' && config) {
    const days = (Array.isArray(cfg.days) ? (cfg.days as string[]) : [])
      .map((d) => d.charAt(0).toUpperCase() + d.slice(1))
      .join(', ');
    return `${days || 'Every day'} at ${cfg.time ?? '00:00'}`;
  }
  if (type === 'signal_received' && config) {
    return `Signal: ${cfg.signal_name ?? 'unknown'}`;
  }
  return TRIGGER_LABELS[type ?? ''] ?? blockLabel?.(type ?? '') ?? humanizeType(type);
}

const ACTION_LABELS: Record<string, string> = {
  process_wishlist: 'Process Wishlist',
  scan_watchlist: 'Scan Watchlist',
  scan_library: 'Scan Library',
  refresh_mirrored: 'Refresh Mirrored',
  sync_playlist: 'Sync Playlist',
  discover_playlist: 'Discover Playlist',
  notify_only: 'Notify Only',
  start_database_update: 'Update Database',
  run_duplicate_cleaner: 'Run Duplicate Cleaner',
  clear_quarantine: 'Clear Quarantine',
  cleanup_wishlist: 'Clean Up Wishlist',
  update_discovery_pool: 'Update Discovery',
  start_quality_scan: 'Run Quality Scan',
  backup_database: 'Backup Database',
  refresh_beatport_cache: 'Refresh Beatport Cache',
  clean_search_history: 'Clean Search History',
  clean_completed_downloads: 'Clean Completed Downloads',
  full_cleanup: 'Full Cleanup',
  playlist_pipeline: 'Playlist Pipeline',
  video_scan_library: 'Scan Video Library',
  video_scan_server: 'Scan Video Server',
  video_update_database: 'Update Video Database',
  video_update_database_hourly: 'Update Video Database (Hourly)',
  video_add_airing_episodes: 'Wishlist Airing Episodes',
  video_deep_scan_movies: 'Deep Scan Movie Library',
  video_deep_scan_tv: 'Deep Scan TV Library',
  video_scan_watchlist_people: 'Scan Watchlist People',
  video_scan_watchlist_studios: 'Scan Watchlist Studios',
  video_scan_watchlist_channels: 'Scan Watchlist Channels',
  video_scan_watchlist_playlists: 'Scan Watchlist Playlists',
  video_process_movie_wishlist: 'Process Movie Wishlist',
  video_process_episode_wishlist: 'Process Episode Wishlist',
  video_process_youtube_wishlist: 'Process YouTube Wishlist',
  video_refresh_airing_schedules: 'Refresh Airing Schedules',
  video_reenrich_stale: 'Refresh Stale Metadata',
  video_clean_youtube_episodes: 'Clean Old YouTube Episodes',
};

export function formatAction(
  type: string | null | undefined,
  blockLabel?: (type: string) => string | undefined,
): string {
  return ACTION_LABELS[type ?? ''] ?? blockLabel?.(type ?? '') ?? humanizeType(type);
}

/**
 * The facts under a card name.
 *
 * `paused` is the side's master switch. Without it this function computed
 * "Next: in 3h" and "Listening" from `enabled` and the trigger type alone —
 * so with automations paused every card advertised a countdown that would
 * never fire and claimed to be listening for events it would ignore. The
 * engine skips the slot (`run_automation`, the master check) but keeps the
 * schedule alive, so the stored `next_run` is real; what is false is the
 * implication that it will run. A paused card says paused, and says nothing
 * about a next run.
 *
 * Manual Run still works while paused (it passes `skip_delay`, which bypasses
 * the master check), so the Run button is NOT part of this lie and stays.
 */
export function automationMeta(
  a: Automation,
  now: number = Date.now(),
  paused = false,
): {
  lastRun?: string;
  nextRun?: string;
  listening: boolean;
  paused: boolean;
  runs?: number;
  error?: string;
  result?: AutomationOutcome;
} {
  const enabled = a.enabled === true || a.enabled === 1;
  const timer = isTimerTrigger(a.trigger_type);
  // A disabled automation is already off on its own account; "paused" is only
  // worth saying about one that WOULD otherwise be doing something.
  const heldByMaster = paused && enabled;
  return {
    lastRun: a.last_run ? timeAgo(a.last_run, now) : undefined,
    nextRun: a.next_run && enabled && timer && !paused ? timeUntil(a.next_run, now) : undefined,
    // Event-driven automations advertise that they are armed; timer ones show
    // a countdown instead, so the two are mutually exclusive.
    listening: !timer && enabled && !paused,
    paused: heldByMaster,
    runs: a.run_count || undefined,
    error: a.last_error || undefined,
    // An error replaces the result summary rather than joining it.
    // `?? undefined` because lastResultFacts returns null for "nothing worth
    // showing", while every other field here uses undefined for absent.
    // Prefer a sentence over the handler's raw dict keys; falls back to them.
    result: a.last_error ? undefined : (automationOutcome(a.action_type, a.last_result) ?? undefined),
  };
}

/**
 * The schedule line on a card's face.
 *
 * `automationMeta` answers "what should the meta line say"; this answers the
 * one question the card is asked most — *when does this next happen* — as a
 * single state word plus a label, so the same fact can be coloured, filtered
 * and read at a glance instead of being inferred from which of three optional
 * meta fragments happens to be present.
 *
 * The order is a precedence, not a list: a run in flight outranks everything
 * (it IS happening), then the two ways an automation can be held — its own
 * switch, then the side's — and only after that does a stored `next_run` mean
 * anything. That is the same order the engine itself applies.
 *
 * `due` rather than `overdue`: the scheduler arms `next_run` and picks the row
 * up on its next pass, so a timestamp a moment in the past is normal operation,
 * not a fault. Nothing here claims to know how late is late.
 */
export type AutomationScheduleState =
  | 'running'
  | 'off'
  | 'paused'
  | 'due'
  | 'waiting'
  | 'unscheduled'
  | 'listening';

export interface AutomationSchedule {
  state: AutomationScheduleState;
  label: string;
  /** The label is a live countdown, so the card must re-render each second. */
  ticking: boolean;
}

export function automationSchedule(
  a: Automation,
  now: number = Date.now(),
  paused = false,
  running = false,
): AutomationSchedule {
  if (running) return { state: 'running', label: 'Running now', ticking: false };
  const enabled = a.enabled === true || a.enabled === 1;
  if (!enabled) return { state: 'off', label: 'Switched off', ticking: false };
  if (paused) return { state: 'paused', label: 'Paused', ticking: false };
  if (!isTimerTrigger(a.trigger_type))
    return { state: 'listening', label: 'Listening', ticking: false };
  // A timer automation with no armed next_run has not been picked up by the
  // scheduler yet — usually the seconds after it was created or re-triggered.
  if (!a.next_run) return { state: 'unscheduled', label: 'Not scheduled yet', ticking: false };
  if (parseServerTime(a.next_run) <= now) return { state: 'due', label: 'Due now', ticking: false };
  return { state: 'waiting', label: `Next ${timeUntil(a.next_run, now)}`, ticking: true };
}

/**
 * Label for a type that the static maps do not cover — video triggers,
 * monthly_time, webhook_received and anything shipped later.
 *
 * Mirrors _findBlockDef: searches triggers, then actions, then notifications,
 * and returns the first match. Returns undefined so callers fall through to
 * humanizeType, which is the vanilla precedence (map, then block def, then
 * humanized).
 */
export function blockLabelLookup(
  blocks: AutomationBlocks | undefined,
): (type: string) => string | undefined {
  return (type: string) => {
    if (!blocks || !type) return undefined;
    for (const category of BLOCK_CATEGORIES) {
      const found = blocks[category]?.find((b) => b.type === type);
      if (found?.label) return found.label;
    }
    return undefined;
  };
}
