/**
 * Auto-Sync schedule board — pure core, ported 1:1 from auto-sync.js 12-470
 * (the newest, best-factored code in the sync family; its header even declares
 * its externals). Everything here is executable without the DOM, so the whole
 * module is pinned differentially in -sync.autosync.test.ts.
 */

/* ── Constants (auto-sync.js 12, 106-110) ─────────────────────────────────── */

export const AUTO_SYNC_BUCKETS: readonly number[] = [1, 2, 4, 8, 12, 16, 24, 48, 72, 168];

/**
 * Canonical weekday order Mon-Sun, short-lowercase — the engine's
 * trigger_config payload convention and the backend next_run_at weekday_map.
 */
export const AUTO_SYNC_WEEKDAYS: readonly string[] = [
  'mon',
  'tue',
  'wed',
  'thu',
  'fri',
  'sat',
  'sun',
];

export const AUTO_SYNC_WEEKDAY_LABELS: Readonly<Record<string, string>> = {
  mon: 'Mon',
  tue: 'Tue',
  wed: 'Wed',
  thu: 'Thu',
  fri: 'Fri',
  sat: 'Sat',
  sun: 'Sun',
};

/* ── Row shapes (loose on purpose — backend rows are duck-typed) ──────────── */

export interface MirroredRow {
  id?: number;
  source?: string;
  source_ref?: string;
  source_playlist_id?: string | number;
  description?: string;
  name?: string;
  track_count?: number;
  kind?: string;
  variant?: string;
  kind_label?: string;
  _personalized?: boolean;
}

export interface PersonalizedKind {
  kind?: string;
  name_template?: string;
  requires_variant?: boolean;
  variants?: (string | number)[];
}

export interface AutomationRow {
  action_type?: string;
  action_config?: Record<string, unknown>;
  owned_by?: string;
  group_name?: string;
  name?: string;
}

export interface WeeklyTriggerConfig {
  time: string;
  days: string[];
  tz: string;
}

/* ── Source refs + trigger codecs (auto-sync.js 35-60) ────────────────────── */

/**
 * The external reference a mirrored playlist syncs from: explicit source_ref,
 * else the URL smuggled in the description (spotify_public/youtube store the
 * source URL there), else the source_playlist_id.
 */
export function getMirroredSourceRef(p: MirroredRow | null | undefined): string {
  if (p && p.source_ref) return String(p.source_ref);
  const desc = p && p.description ? String(p.description).trim() : '';
  if ((p?.source === 'spotify_public' || p?.source === 'youtube') && /^https?:\/\//i.test(desc)) {
    return desc;
  }
  return p && p.source_playlist_id ? String(p.source_playlist_id) : '';
}

export function autoSyncTriggerForHours(hours: number | string): {
  interval: number;
  unit: string;
} {
  const h = parseInt(String(hours), 10) || 24;
  if (h >= 24 && h % 24 === 0) {
    return { interval: h / 24, unit: 'days' };
  }
  return { interval: h, unit: 'hours' };
}

export function autoSyncHoursFromTrigger(
  config: { interval?: number | string; unit?: string } | null | undefined,
): number | null {
  const interval = parseInt(String(config?.interval), 10) || 0;
  const unit = config?.unit || 'hours';
  if (!interval) return null;
  if (unit === 'minutes') return Math.max(1, Math.round(interval / 60));
  if (unit === 'days') return interval * 24;
  if (unit === 'weeks') return interval * 168;
  return interval;
}

/* ── Cadence labels (auto-sync.js 62-86) ──────────────────────────────────── */

export function autoSyncBucketLabel(hours: number): string {
  if (hours === 168) return 'Weekly';
  if (hours >= 24) return `${hours / 24}d`;
  return `${hours}h`;
}

export function autoSyncIntervalLabel(hours: number): string {
  if (hours === 168) return 'Every week';
  if (hours >= 24) {
    const days = hours / 24;
    return `Every ${days} day${days === 1 ? '' : 's'}`;
  }
  return `Every ${hours} hour${hours === 1 ? '' : 's'}`;
}

export function autoSyncLaneCadence(hours: number): string {
  if (hours === 1) return 'Hourly';
  if (hours === 12) return 'Twice a day';
  if (hours === 24) return 'Daily';
  if (hours === 168) return 'Weekly';
  if (hours < 24) return `Every ${hours}h`;
  const days = hours / 24;
  return `Every ${days} days`;
}

/* ── Weekly codecs (auto-sync.js 91-156) ──────────────────────────────────── */

/** Browser tz for new weekly schedules; UTC where Intl is unavailable. */
export function detectBrowserTimezone(): string {
  try {
    const tz =
      typeof Intl !== 'undefined' &&
      Intl.DateTimeFormat &&
      Intl.DateTimeFormat().resolvedOptions().timeZone;
    return tz || 'UTC';
  } catch {
    return 'UTC';
  }
}

/**
 * Build a weekly_time trigger_config from picker input. Defensive: garbage
 * time → 09:00, unknown days dropped, missing tz → browser tz → UTC, so the
 * payload always passes next_run_at validation.
 */
export function autoSyncWeeklyTrigger({
  time,
  days,
  tz,
}: { time?: unknown; days?: unknown; tz?: unknown } = {}): WeeklyTriggerConfig {
  const safeTime = typeof time === 'string' && /^\d{1,2}:\d{2}$/.test(time) ? time : '09:00';
  const safeDays = Array.isArray(days)
    ? days.filter((d): d is string => AUTO_SYNC_WEEKDAYS.includes(d as string))
    : [];
  const safeTz = typeof tz === 'string' && tz ? tz : detectBrowserTimezone();
  return { time: safeTime, days: safeDays, tz: safeTz };
}

/**
 * Parse a weekly_time trigger_config back out, defensively. Empty/all-invalid
 * days surface as ALL SEVEN days — the next_run_at "every day" convention —
 * so the board renders the schedule under every column instead of treating it
 * as unscheduled. Null when the config isn't recognisably weekly.
 */
export function autoSyncWeeklyFromTrigger(config: unknown): WeeklyTriggerConfig | null {
  if (!config || typeof config !== 'object') return null;
  const c = config as Record<string, unknown>;
  const rawTime = typeof c.time === 'string' && /^\d{1,2}:\d{2}$/.test(c.time) ? c.time : '09:00';
  let days = Array.isArray(c.days)
    ? c.days.map((d) => String(d).toLowerCase()).filter((d) => AUTO_SYNC_WEEKDAYS.includes(d))
    : [];
  if (days.length === 0) days = [...AUTO_SYNC_WEEKDAYS];
  const tz = typeof c.tz === 'string' && c.tz ? c.tz : 'UTC';
  return { time: rawTime, days, tz };
}

/**
 * Card/tooltip label for a weekly schedule. Days render in canonical Mon-Sun
 * order regardless of toggle order; a full week collapses to "Daily @ T".
 */
export function autoSyncWeeklyLabel(parsed: WeeklyTriggerConfig | null | undefined): string {
  if (!parsed) return 'Unscheduled';
  const { time, days } = parsed;
  if (!Array.isArray(days) || days.length === 0) return `Daily @ ${time}`;
  if (days.length === 7) return `Daily @ ${time}`;
  const ordered = AUTO_SYNC_WEEKDAYS.filter((d) => days.includes(d));
  const dayList = ordered.map((d) => AUTO_SYNC_WEEKDAY_LABELS[d]).join(', ');
  return `${dayList} @ ${time}`;
}

/* ── Source labels + scheduling eligibility (auto-sync.js 158-216) ────────── */

export function autoSyncSourceLabel(source: string | null | undefined): string {
  const labels: Record<string, string> = {
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
  return labels[source as string] || source || 'Other';
}

/**
 * file + beatport have no external refresh hook; lastfm radios are
 * seed-specific snapshots that never change upstream — re-syncing would just
 * re-discover the same tracks, so they are excluded from the board.
 */
export function autoSyncCanSchedulePlaylist(playlist: MirroredRow | null | undefined): boolean {
  if (!playlist) return false;
  const src = playlist.source || '';
  return !['file', 'beatport', 'lastfm'].includes(src);
}

/* ── Automation linkage (auto-sync.js 218-241) ────────────────────────────── */

export function autoSyncIsPipelineAutomation(auto: AutomationRow | null | undefined): boolean {
  return Boolean(auto && auto.action_type === 'playlist_pipeline');
}

export function autoSyncPlaylistIdFromAutomation(
  auto: AutomationRow | null | undefined,
): number | null {
  if (!autoSyncIsPipelineAutomation(auto)) return null;
  const cfg = auto?.action_config || {};
  if (cfg.all === true || cfg.all === 'true') return null;
  const raw = cfg.playlist_id;
  if (raw === undefined || raw === null || raw === '') return null;
  const id = parseInt(String(raw as string | number), 10);
  return Number.isFinite(id) ? id : null;
}

/**
 * Schedule ownership: the explicit owned_by flag the board stamps, with the
 * legacy group/name conventions as backfill for pre-column rows.
 */
export function autoSyncIsScheduleOwned(auto: AutomationRow | null | undefined): boolean {
  if (auto?.owned_by === 'auto_sync') return true;
  const group = auto?.group_name || '';
  const name = auto?.name || '';
  return group === 'Playlist Auto-Sync' || name.startsWith('Auto-Sync:');
}

/* ── Personalized (SoulSync Discovery) rows (auto-sync.js 243-430) ────────── */

/**
 * Collapsible-group heading for a variant kind: name_template up to the
 * "{variant}" placeholder, trailing separators stripped; kind id fallback.
 */
export function autoSyncKindLabel(k: PersonalizedKind | null | undefined): string {
  return (
    String((k && k.name_template) || (k && k.kind) || '')
      .split('{variant}')[0]
      .replace(/[\s—–:>·.-]+$/, '')
      .trim() ||
    (k && k.kind) ||
    ''
  );
}

/**
 * Tag generated soulsync_discovery rows with kind/variant parsed from their
 * ssd_<kind>_<variant> source id; DROP rows whose kind is unregistered (an
 * orphaned mirror can't be regenerated). Fails open with no kinds metadata.
 */
export function autoSyncEnrichDiscoveryRows(
  playlists: MirroredRow[],
  kinds: PersonalizedKind[] | null | undefined,
): MirroredRow[] {
  if (!Array.isArray(playlists)) return [];
  if (!Array.isArray(kinds) || !kinds.length) return playlists;
  const singletons = new Map<string, boolean>();
  const variantKinds: { prefix: string; k: PersonalizedKind }[] = [];
  for (const k of kinds) {
    if (!k || !k.kind) continue;
    if (k.requires_variant) variantKinds.push({ prefix: `ssd_${k.kind}_`, k });
    else singletons.set(`ssd_${k.kind}`, true);
  }
  // Longest prefix first so a shorter kind can't shadow a longer one.
  variantKinds.sort((a, b) => b.prefix.length - a.prefix.length);
  const out: MirroredRow[] = [];
  for (const p of playlists) {
    if (!p || p.source !== 'soulsync_discovery' || !p.source_playlist_id) {
      out.push(p);
      continue;
    }
    const sid = String(p.source_playlist_id);
    if (singletons.has(sid)) {
      out.push(p);
      continue;
    }
    const vk = variantKinds.find((v) => sid.startsWith(v.prefix));
    if (vk) {
      out.push({
        ...p,
        kind: vk.k.kind,
        variant: sid.slice(vk.prefix.length),
        kind_label: autoSyncKindLabel(vk.k),
      });
      continue;
    }
    // Unregistered kind (orphaned mirror row) → drop.
  }
  return out;
}

/** (kind, variant) → generated track count, from /api/personalized/playlists. */
export function autoSyncGeneratedCountMap(playlistsData: unknown): Map<string, number> {
  const map = new Map<string, number>();
  const data = playlistsData as
    | {
        success?: boolean;
        playlists?: { kind?: string; variant?: string; track_count?: unknown }[];
      }
    | null
    | undefined;
  const list = data && data.success && Array.isArray(data.playlists) ? data.playlists : [];
  for (const p of list) {
    if (!p || !p.kind) continue;
    map.set(`${p.kind} ${p.variant || ''}`, parseInt(String(p.track_count), 10) || 0);
  }
  return map;
}

/**
 * Synthesize schedulable rows for not-yet-generated personalized kinds, with
 * NEGATIVE ids: negatives never collide with real mirrored ids and survive
 * every parseInt in the board, so drag/drop/bulk/weekly work unchanged.
 */
export function autoSyncExpandPersonalizedRows(
  kinds: PersonalizedKind[] | null | undefined,
  existingPlaylists: MirroredRow[] | null | undefined,
  generatedCounts?: Map<string, number> | null,
): MirroredRow[] {
  const rows: MirroredRow[] = [];
  if (!Array.isArray(kinds)) return rows;
  const existingSsd = new Set(
    (existingPlaylists || [])
      .filter((p) => p && p.source === 'soulsync_discovery' && p.source_playlist_id)
      .map((p) => String(p.source_playlist_id)),
  );
  let nextId = -1;
  for (const k of kinds) {
    if (!k || !k.kind) continue;
    const variants = k.requires_variant ? (Array.isArray(k.variants) ? k.variants : []) : [''];
    for (const raw of variants) {
      const variant = k.requires_variant ? String(raw) : '';
      const ssdId = `ssd_${k.kind}${variant ? `_${variant}` : ''}`;
      if (existingSsd.has(ssdId)) continue;
      const name = k.requires_variant
        ? String(k.name_template || `${k.kind} ${variant}`).replace('{variant}', variant)
        : String(k.name_template || k.kind);
      const kindLabel = k.requires_variant ? autoSyncKindLabel(k) : '';
      const generated = generatedCounts ? generatedCounts.get(`${k.kind} ${variant}`) || 0 : 0;
      rows.push({
        id: nextId--,
        source: 'soulsync_discovery',
        name,
        track_count: generated,
        kind: k.kind,
        variant,
        kind_label: kindLabel,
        source_playlist_id: ssdId,
        _personalized: true,
      });
    }
  }
  return rows;
}

/** The automation payload scheduling a board row creates (auto-sync.js 355-371). */
export function autoSyncActionForPlaylist(
  playlist: MirroredRow | null | undefined,
  playlistId: number | string,
): { action_type: string; action_config: Record<string, unknown> } {
  if (playlist && playlist._personalized) {
    const entry: Record<string, unknown> = { kind: playlist.kind };
    if (playlist.variant) entry.variant = playlist.variant;
    return {
      action_type: 'personalized_pipeline',
      action_config: { kinds: [entry], refresh_first: true },
    };
  }
  return {
    action_type: 'playlist_pipeline',
    action_config: { playlist_id: String(playlistId), all: false },
  };
}

export function autoSyncIsPersonalizedAutomation(auto: AutomationRow | null | undefined): boolean {
  return Boolean(auto && auto.action_type === 'personalized_pipeline');
}

/**
 * The single {kind, variant} a board-created personalized schedule targets.
 * Multi-kind pipelines (built on the Automations page) return null so they
 * are never mistaken for a per-row board schedule.
 */
export function autoSyncPersonalizedEntry(
  auto: AutomationRow | null | undefined,
): { kind: string; variant: string } | null {
  if (!autoSyncIsPersonalizedAutomation(auto)) return null;
  const kinds = (auto?.action_config || {}).kinds;
  if (!Array.isArray(kinds) || kinds.length !== 1) return null;
  const k = (kinds[0] || {}) as { kind?: string; variant?: string };
  if (!k.kind) return null;
  return { kind: k.kind, variant: k.variant || '' };
}

/** Map a personalized {kind, variant} to its board row id — real row first, then synthetic. */
export function autoSyncRowIdForPersonalized(
  entry: { kind: string; variant: string } | null,
  playlists: MirroredRow[] | null | undefined,
): number | null {
  if (!entry) return null;
  const ssdId = `ssd_${entry.kind}${entry.variant ? `_${entry.variant}` : ''}`;
  const real = (playlists || []).find(
    (p) => p && p.source === 'soulsync_discovery' && String(p.source_playlist_id) === ssdId,
  );
  if (real) return real.id ?? null;
  const synth = (playlists || []).find(
    (p) => p && p._personalized && p.kind === entry.kind && (p.variant || '') === entry.variant,
  );
  return synth ? (synth.id ?? null) : null;
}

/**
 * Split a source-group's rows into flat rows and collapsible variant-kind
 * groups, preserving encounter order; each group takes its first row's
 * kind_label as heading.
 */
export function autoSyncGroupSidebarRows(rows: MirroredRow[] | null | undefined): {
  flat: MirroredRow[];
  groups: { kind: string; label: string; rows: MirroredRow[] }[];
} {
  const flat: MirroredRow[] = [];
  const groups: { kind: string; label: string; rows: MirroredRow[] }[] = [];
  const byKind = new Map<string, { kind: string; label: string; rows: MirroredRow[] }>();
  (rows || []).forEach((p) => {
    if (p && p.variant && p.kind) {
      let g = byKind.get(p.kind);
      if (!g) {
        g = { kind: p.kind, label: p.kind_label || p.kind, rows: [] };
        byKind.set(p.kind, g);
        groups.push(g);
      }
      g.rows.push(p);
    } else {
      flat.push(p);
    }
  });
  return { flat, groups };
}
