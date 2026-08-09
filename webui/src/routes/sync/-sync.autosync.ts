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
  /** Feeds the shared-helpers quality-profile select's hydration (740). */
  quality_profile_id?: string | number | null;
  description?: string;
  name?: string;
  /** String or number on the wire, which is why every read parseInts it. */
  track_count?: number | string;
  kind?: string;
  variant?: string;
  kind_label?: string;
  _personalized?: boolean;
  /** The board's organize-by-playlist preference (1921). */
  organize_by_playlist?: boolean;
  /**
   * Live pipeline status — used to disable 'Run now' (1954) and to drive the
   * whole monitor panel (1104-1114). Declared as the full PipelineState (see
   * below) rather than just its status, because the monitor reads phase,
   * progress, timestamps and logs off the same object.
   */
  pipeline_state?: PipelineState | null;
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
  /**
   * The schedule fields the board reads (471-569). Added with the state
   * builder; the codec helpers above predate them and never needed them.
   */
  id?: number | string;
  trigger_type?: string;
  trigger_config?: unknown;
  next_run?: string | null;
  enabled?: unknown;
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

/* ── The schedule-state builder (auto-sync.js 471-569) ────────────────────── */

export interface AutoSyncScheduleEntry {
  automation_id: number | string;
  automation_name: string;
  enabled: boolean;
  owned: true;
  next_run?: string | null;
  trigger_config: unknown;
}

export interface AutoSyncHourlyEntry extends AutoSyncScheduleEntry {
  hours: number;
}

export interface AutoSyncWeeklyEntry extends AutoSyncScheduleEntry, WeeklyTriggerConfig {}

export interface AutoSyncScheduleState {
  playlists: MirroredRow[];
  automations: AutomationRow[];
  playlistSchedules: Record<string, AutoSyncHourlyEntry>;
  weeklySchedules: Record<string, AutoSyncWeeklyEntry>;
  /** Pipeline automations this board does NOT own — the read-only panel. */
  automationPipelines: AutomationRow[];
  /**
   * Typed as `unknown[]` when slice A wrote this, because nothing consumed it
   * yet. Slice D-ii gave the rows a shape and both the health dot and the
   * history panel read them, so it is narrowed. Rows still arrive unvalidated
   * from the API — every consumer treats a malformed one as such.
   */
  runHistory: AutoSyncHistoryEntry[];
  runHistoryTotal: number;
}

/**
 * 487. Tri-state, NOT truthiness: the row is enabled unless it is explicitly
 * `false` or `0`, so an absent flag counts as enabled.
 */
function autoSyncEnabledFlag(auto: AutomationRow): boolean {
  const enabled = (auto as { enabled?: unknown }).enabled;
  return enabled !== false && enabled !== 0;
}

/**
 * Fold the automations list into the board's two schedule maps plus the
 * read-only pipeline panel (471-569).
 *
 * TWO ASYMMETRIES, both deliberate in the vanilla and both easy to "tidy" into
 * bugs:
 *
 * 1. **`trigger_config || {}` on the schedule arm, RAW on the weekly arm**
 *    (481 vs 502, with a comment at 496-501). A null or non-object config on a
 *    weekly row must fall through to `automationPipelines` as a broken row —
 *    coercing it to `{}` would hand it to autoSyncWeeklyFromTrigger, whose
 *    defensive defaults would silently turn garbage into an every-day schedule.
 *
 * 2. **The playlist_pipeline pass pushes unbucketable rows onto
 *    `automationPipelines`; the personalized_pipeline pass DROPS them** (518 vs
 *    526-528). A personalized automation that cannot be bucketed simply
 *    disappears from the board rather than appearing in the read-only panel.
 *
 * TWO TYPING COERCIONS, declared rather than smuggled. The vanilla stores
 * `auto.id` and `auto.name` raw; this stores `?? ''` so the entry type is not
 * optional. Both fields always arrive populated from /api/automations, and an
 * undefined id would produce a broken DELETE url either way — but it is a
 * difference, so it is written down. Keys are `String(playlistId)` for the same
 * reason: JS object keys are strings regardless, so this only makes explicit
 * what the vanilla's numeric index already did.
 */
export function buildAutoSyncScheduleState(
  playlists: MirroredRow[],
  automations: AutomationRow[],
  historyData: { history?: unknown[]; total?: number } = {},
): AutoSyncScheduleState {
  const playlistSchedules: Record<string, AutoSyncHourlyEntry> = {};
  const weeklySchedules: Record<string, AutoSyncWeeklyEntry> = {};
  const automationPipelines: AutomationRow[] = [];

  (automations || []).filter(autoSyncIsPipelineAutomation).forEach((auto) => {
    const playlistId = autoSyncPlaylistIdFromAutomation(auto);
    const isOwned = autoSyncIsScheduleOwned(auto);

    if (playlistId && isOwned && auto.trigger_type === 'schedule') {
      const hours = autoSyncHoursFromTrigger(
        (auto.trigger_config || {}) as Parameters<typeof autoSyncHoursFromTrigger>[0],
      );
      if (hours) {
        playlistSchedules[String(playlistId)] = {
          automation_id: auto.id ?? '',
          automation_name: auto.name ?? '',
          hours,
          enabled: autoSyncEnabledFlag(auto),
          owned: true,
          next_run: auto.next_run,
          trigger_config: auto.trigger_config || {},
        };
        return;
      }
    }
    if (playlistId && isOwned && auto.trigger_type === 'weekly_time') {
      // RAW on purpose — see asymmetry 1 above.
      const parsed = autoSyncWeeklyFromTrigger(auto.trigger_config);
      if (parsed) {
        weeklySchedules[String(playlistId)] = {
          automation_id: auto.id ?? '',
          automation_name: auto.name ?? '',
          time: parsed.time,
          days: parsed.days,
          tz: parsed.tz,
          enabled: autoSyncEnabledFlag(auto),
          owned: true,
          next_run: auto.next_run,
          trigger_config: auto.trigger_config || {},
        };
        return;
      }
    }
    automationPipelines.push(auto);
  });

  // 524-558. Board-owned single-kind personalized schedules bucket onto their
  // row — the real mirrored row when one was generated, else the synthetic one.
  (automations || []).filter(autoSyncIsPersonalizedAutomation).forEach((auto) => {
    const entry = autoSyncPersonalizedEntry(auto);
    if (!entry || !autoSyncIsScheduleOwned(auto)) return;
    const key = autoSyncRowIdForPersonalized(entry, playlists);
    if (key == null) return;
    if (auto.trigger_type === 'schedule') {
      const hours = autoSyncHoursFromTrigger(
        (auto.trigger_config || {}) as Parameters<typeof autoSyncHoursFromTrigger>[0],
      );
      if (hours) {
        playlistSchedules[String(key)] = {
          automation_id: auto.id ?? '',
          automation_name: auto.name ?? '',
          hours,
          enabled: autoSyncEnabledFlag(auto),
          owned: true,
          next_run: auto.next_run,
          trigger_config: auto.trigger_config || {},
        };
      }
    } else if (auto.trigger_type === 'weekly_time') {
      const parsed = autoSyncWeeklyFromTrigger(auto.trigger_config);
      if (parsed) {
        weeklySchedules[String(key)] = {
          automation_id: auto.id ?? '',
          automation_name: auto.name ?? '',
          time: parsed.time,
          days: parsed.days,
          tz: parsed.tz,
          enabled: autoSyncEnabledFlag(auto),
          owned: true,
          next_run: auto.next_run,
          trigger_config: auto.trigger_config || {},
        };
      }
    }
    // No else — see asymmetry 2 above.
  });

  return {
    playlists,
    automations,
    playlistSchedules,
    weeklySchedules,
    automationPipelines,
    // Rows arrive unvalidated; every consumer treats a malformed one as such.
    runHistory: (historyData.history || []) as AutoSyncHistoryEntry[],
    runHistoryTotal: historyData.total || 0,
  };
}

/* ── The hourly board's lane model (auto-sync.js 741-823) ─────────────────── */

/**
 * 742-746. The sidebar filter matches the playlist NAME or its SOURCE LABEL —
 * so typing "tidal" finds every Tidal playlist even though no name contains it.
 * Empty filter matches everything.
 */
export function autoSyncMatchesFilter(
  playlist: MirroredRow,
  filter: string,
  sourceLabel: (source: string) => string = autoSyncSourceLabel,
): boolean {
  const needle = (filter || '').trim().toLowerCase();
  if (!needle) return true;
  const name = (playlist?.name || '').toLowerCase();
  const label = sourceLabel(playlist?.source || '').toLowerCase();
  return name.includes(needle) || label.includes(needle);
}

/**
 * 747-753. Group the schedulable rows by source, ordering the groups by their
 * DISPLAY LABEL rather than the raw key.
 *
 * Worth knowing before anyone simplifies this: with TODAY'S twelve labels the
 * two orderings happen to coincide, because every label is essentially its key
 * capitalised. Sorting by the key would therefore pass every test that used
 * real sources — which is why the labeller is injectable and the test below
 * supplies one that genuinely reorders. The moment a label stops matching its
 * key (an 'amazon_music' → 'Prime Music', say) the difference becomes visible
 * to users, and this stays correct.
 */
export function autoSyncGroupBySource(
  playlists: MirroredRow[],
  sourceLabel: (source: string) => string = autoSyncSourceLabel,
): {
  source: string;
  rows: MirroredRow[];
}[] {
  const grouped = new Map<string, MirroredRow[]>();
  (playlists || []).forEach((p) => {
    const key = p?.source || 'other';
    const rows = grouped.get(key);
    if (rows) rows.push(p);
    else grouped.set(key, [p]);
  });
  return [...grouped.keys()]
    .sort((a, b) => sourceLabel(a).localeCompare(sourceLabel(b)))
    .map((source) => ({ source, rows: grouped.get(source) as MirroredRow[] }));
}

export interface AutoSyncLane {
  hours: number;
  /** True when this interval is not one of the ten standard buckets. */
  isCustom: boolean;
  playlists: MirroredRow[];
}

/**
 * 795-823. The lanes to render, and the reason this is not just
 * AUTO_SYNC_BUCKETS.
 *
 * A schedule can carry ANY hour count — the Automations page and the board's
 * own custom-interval prompt both allow it. Those hours are merged into the
 * bucket list so a 6h or 36h schedule renders in its own lane instead of
 * vanishing from the board entirely. Filtered to finite positives so a
 * corrupt row cannot inject a NaN or negative lane, de-duplicated, and sorted
 * ascending so a custom lane lands between its neighbours rather than at the
 * end.
 */
export function autoSyncBuildLanes(
  schedulable: MirroredRow[],
  playlistSchedules: Record<string, { hours?: number } | undefined>,
): AutoSyncLane[] {
  const customHours = Object.values(playlistSchedules || {})
    .map((s) => parseInt(String(s?.hours), 10))
    .filter((h) => Number.isFinite(h) && h > 0 && !AUTO_SYNC_BUCKETS.includes(h));

  const allHours = [...new Set([...AUTO_SYNC_BUCKETS, ...customHours])].sort((a, b) => a - b);

  return allHours.map((hours) => ({
    hours,
    isCustom: !AUTO_SYNC_BUCKETS.includes(hours),
    playlists: (schedulable || []).filter(
      (p) => playlistSchedules?.[String(p?.id)]?.hours === hours,
    ),
  }));
}

/**
 * stats-automations.js 4260-4264, reached from auto-sync.js 1863/2002 as a
 * cross-file global. A bare timestamp is UTC, so it gets a 'Z' appended; one
 * that already carries an offset is parsed as-is.
 */
export function autoSyncParseUTC(ts: string): number {
  if (/[Zz]$/.test(ts) || /[+-]\d{2}:\d{2}$/.test(ts)) return new Date(ts).getTime();
  return new Date(`${ts}Z`).getTime();
}

/**
 * 1999-2011. 'next in 12m' / '3h' / '2d', or 'due now' once the moment has
 * passed. `now` is a parameter rather than a `Date.now()` call so the label is
 * testable without faking the clock.
 */
export function autoSyncNextRunLabel(nextRun: string | null | undefined, now: number): string {
  if (!nextRun) return '';
  const ts = autoSyncParseUTC(nextRun);
  if (!Number.isFinite(ts)) return '';
  const diff = ts - now;
  if (diff <= 0) return 'due now';
  const mins = Math.ceil(diff / 60000);
  if (mins < 60) return `next in ${mins}m`;
  const hours = Math.ceil(mins / 60);
  if (hours < 24) return `next in ${hours}h`;
  return `next in ${Math.ceil(hours / 24)}d`;
}

export interface AutoSyncHealth {
  level: 'ok' | 'warning' | 'failing';
  tooltip: string;
}

/**
 * 1978-1996. The health dot on a scheduled card, from the last three runs of
 * that playlist in the loaded history. Three errors in a row is failing (red);
 * any error at all is a warning. 'skipped' counts as an error, which is
 * deliberate — a skipped run means the pipeline did not do its job.
 *
 * ORDERING, now verified: `slice(0, 3)` calls the FIRST three rows "the last 3
 * runs", which is only correct if the endpoint returns newest-first. It does —
 * music_database.py 17820 orders the pipeline-history query `ORDER BY id DESC`
 * (the `ORDER BY started_at DESC` I first found during the P0 read belongs to
 * `sync_history`, a different table). So the window is genuinely the most
 * recent three runs and needs no defensive sort.
 */
export function autoSyncPlaylistHealth(
  history: AutoSyncHistoryEntry[] | null | undefined,
  playlistId: number | string,
): AutoSyncHealth {
  const id = parseInt(String(playlistId), 10);
  const recent = (history || [])
    .filter((h) => parseInt(String(h?.playlist_id), 10) === id)
    .slice(0, 3);
  if (!recent.length) return { level: 'ok', tooltip: '' };
  const errored = recent.filter((h) => h?.status === 'error' || h?.status === 'skipped');
  if (errored.length >= 3) {
    return {
      level: 'failing',
      tooltip: `Last ${recent.length} runs failed — check Run History tab`,
    };
  }
  if (errored.length) {
    return { level: 'warning', tooltip: `${errored.length} of last ${recent.length} runs failed` };
  }
  return { level: 'ok', tooltip: '' };
}

/* ── The live pipeline monitor (1104-1129) ──────────────────────────────── */

export interface PipelineState {
  status?: string;
  phase?: string;
  progress?: number | string;
  started_at?: number;
  finished_at?: number;
  log?: { message?: string }[];
}

export interface AutoSyncPipelineItem {
  playlist: MirroredRow;
  state: PipelineState;
}

/**
 * 1104-1114. Every playlist with a non-idle pipeline state, running ones
 * first, then most-recently-touched. `finished_at || started_at || 0` is kept
 * verbatim: a running row has no finish time, so it sorts on its start.
 */
export function getAutoSyncPipelinePlaylists(playlists: MirroredRow[]): AutoSyncPipelineItem[] {
  return playlists
    .map((p) => ({ playlist: p, state: (p.pipeline_state || null) as PipelineState | null }))
    .filter(
      (item): item is AutoSyncPipelineItem =>
        !!item.state && !!item.state.status && item.state.status !== 'idle',
    )
    .sort((a, b) => {
      const aRunning = a.state.status === 'running' ? 1 : 0;
      const bRunning = b.state.status === 'running' ? 1 : 0;
      if (aRunning !== bRunning) return bRunning - aRunning;
      return (
        (b.state.finished_at || b.state.started_at || 0) -
        (a.state.finished_at || a.state.started_at || 0)
      );
    });
}

/** 1116-1122. */
export function autoSyncPipelineStatusLabel(status: string | undefined): string {
  if (status === 'running') return 'Running';
  if (status === 'finished') return 'Completed';
  if (status === 'skipped') return 'Skipped';
  if (status === 'error') return 'Needs attention';
  return 'Idle';
}

/** 1124-1129. Note that 'skipped' shares the error class but not the label. */
export function autoSyncPipelineStatusClass(status: string | undefined): string {
  if (status === 'running') return 'running';
  if (status === 'finished') return 'finished';
  if (status === 'error' || status === 'skipped') return 'error';
  return 'idle';
}

export interface AutoSyncMonitorSummary {
  visible: AutoSyncPipelineItem[];
  runningCount: number;
  title: string;
  detail: string;
}

/**
 * 1131-1145. ALL running rows, then at most 2 finished ones, then the whole
 * lot capped at 4. The two caps compose: with five pipelines running you see
 * four running rows and no recent ones, which is the intent — live work
 * outranks history.
 */
export function autoSyncMonitorSummary(playlists: MirroredRow[]): AutoSyncMonitorSummary {
  const items = getAutoSyncPipelinePlaylists(playlists);
  const running = items.filter((i) => i.state.status === 'running');
  const recent = items.filter((i) => i.state.status !== 'running').slice(0, 2);
  return {
    visible: [...running, ...recent].slice(0, 4),
    runningCount: running.length,
    title: running.length
      ? `${running.length} pipeline${running.length === 1 ? '' : 's'} running`
      : 'No pipelines running',
    detail: running.length
      ? 'Live status refreshes while this modal is open.'
      : 'Use Run now on a scheduled playlist when you want the pipeline immediately.',
  };
}

/** 1163-1164. Clamped to 0-100 because the backend has been known to overshoot. */
export function autoSyncPipelineProgress(progress: unknown): number {
  return Math.max(0, Math.min(100, parseInt(String(progress), 10) || 0));
}

/** 1165. The LAST log line, not the first — the monitor shows the newest. */
export function autoSyncPipelineLatestLog(state: PipelineState | null | undefined): string {
  const log = state?.log;
  return Array.isArray(log) && log.length ? log[log.length - 1]?.message || '' : '';
}

/* ── The read-only Automations panel (1185-1198, 1883-1918) ─────────────── */

/**
 * stats-automations.js 4144-4152. 'deep_scan_library' → 'Deep Scan Library'.
 * The video/music prefix is stripped first.
 */
export function autoSyncHumanizeType(type: string | null | undefined): string {
  return (
    String(type || '')
      .replace(/^(video|music)_/, '')
      .split('_')
      .filter(Boolean)
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ') || 'Unknown'
  );
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
 * stats-automations.js 4154-4186, reached from auto-sync.js 1890 as an
 * UNGUARDED cross-file global. That call is safe today only because
 * index.html 8398-8399 loads stats-automations.js immediately before
 * auto-sync.js — a load-order dependency, not a contract.
 *
 * This port does not inherit that dependency. It reimplements the formatter
 * and delegates to the global ONLY for the one branch it cannot reproduce:
 * `_findBlockDef(type)?.label`, which reads block definitions
 * stats-automations.js fetches at runtime. So an exotic trigger type still
 * gets its configured label while that file is loaded, and degrades to the
 * humanized identifier rather than throwing if it ever is not.
 */
export function autoSyncFormatTrigger(
  type: string | undefined,
  config: Record<string, unknown> | undefined,
  formatViaGlobal: ((type: string, config: unknown) => string) | undefined = typeof window !==
  'undefined'
    ? window._autoFormatTrigger
    : undefined,
): string {
  if (type === 'schedule' && config) {
    return `Every ${config.interval || 1} ${config.unit || 'hours'}`;
  }
  if (type === 'daily_time' && config) return `Daily at ${config.time || '00:00'}`;
  if (type === 'weekly_time' && config) {
    const days = ((config.days as string[]) || [])
      .map((d) => d.charAt(0).toUpperCase() + d.slice(1))
      .join(', ');
    return `${days || 'Every day'} at ${config.time || '00:00'}`;
  }
  if (type === 'signal_received' && config) {
    return `Signal: ${config.signal_name || 'unknown'}`;
  }

  let label = TRIGGER_LABELS[type as string];
  if (!label && formatViaGlobal) {
    // Only worth asking when the type is unmapped — a mapped label always
    // wins, exactly as the comment at 4179 says.
    const viaGlobal = formatViaGlobal(type as string, config);
    if (viaGlobal) return viaGlobal;
  }
  label = label || autoSyncHumanizeType(type);

  const conditions = config?.conditions as { field?: string; operator?: string; value?: unknown }[];
  if (conditions?.length) {
    const first = conditions[0];
    label += ` (${first.field} ${first.operator} "${String(first.value)}"${
      conditions.length > 1 ? ` +${conditions.length - 1} more` : ''
    })`;
  }
  return label;
}

export interface AutoSyncAutomationCard {
  name: string;
  trigger: string;
  target: string;
  sourceLabel: string;
  next: string;
  enabled: boolean;
}

/**
 * 1883-1893. The read-only card's fields. `cfg.all` is checked against BOTH
 * `true` and the string `'true'` because the Automations page stores it either
 * way depending on which editor wrote it.
 */
export function autoSyncAutomationCardFields(
  auto: AutomationRow,
  playlists: MirroredRow[],
  now: number,
): AutoSyncAutomationCard {
  const cfg = (auto.action_config || {}) as Record<string, unknown>;
  const playlistId = autoSyncPlaylistIdFromAutomation(auto);
  const playlist = playlistId
    ? playlists.find((p) => parseInt(String(p.id), 10) === playlistId)
    : null;
  const isAll = cfg.all === true || cfg.all === 'true';
  return {
    name: auto.name || 'Playlist Pipeline',
    trigger: autoSyncFormatTrigger(
      auto.trigger_type,
      (auto.trigger_config || {}) as Record<string, unknown>,
    ),
    target: isAll
      ? 'All refreshable mirrored playlists'
      : playlist
        ? playlist.name || ''
        : playlistId
          ? `Playlist #${playlistId}`
          : 'Custom pipeline target',
    sourceLabel: playlist
      ? autoSyncSourceLabel(playlist.source)
      : isAll
        ? 'All sources'
        : 'Pipeline',
    // 1892: 'not scheduled', NOT the empty string the card helper returns.
    next: auto.next_run ? autoSyncNextRunLabel(auto.next_run, now) : 'not scheduled',
    enabled: auto.enabled !== false && auto.enabled !== 0,
  };
}

/* ── The run-history panel (1200-1253, 1394-1882) ───────────────────────── */

export interface AutoSyncHistorySnapshot {
  name?: string;
  source?: string;
  playlist_id?: number | string;
  track_count?: number | string;
  discovered_count?: number | string;
  wishlisted_count?: number | string;
  in_library_count?: number | string;
  [key: string]: unknown;
}

export interface AutoSyncHistoryEntry {
  id?: number | string;
  playlist_id?: number | string;
  playlist_name?: string;
  status?: string;
  source?: string;
  trigger_source?: string;
  started_at?: string;
  finished_at?: string;
  duration_seconds?: number | string;
  before_json?: AutoSyncHistorySnapshot | string;
  after_json?: AutoSyncHistorySnapshot | string;
  result_json?: Record<string, unknown> | string;
  log_lines?: (
    | string
    | { message?: string; log_line?: string; type?: string; log_type?: string }
  )[];
}

/** 1632-1642. A snapshot arrives as an object OR a JSON string OR junk. */
export function autoSyncParseHistoryObject(value: unknown): Record<string, unknown> {
  if (!value) return {};
  if (typeof value === 'object') return value as Record<string, unknown>;
  if (typeof value !== 'string') return {};
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

export interface AutoSyncNormalizedEntry extends AutoSyncHistoryEntry {
  id: number | string;
  before_json: AutoSyncHistorySnapshot;
  after_json: AutoSyncHistorySnapshot;
  result_json: Record<string, unknown>;
}

/** 1595-1615. A row that is not an object at all still renders as something. */
export function autoSyncNormalizeHistoryEntry(
  entry: AutoSyncHistoryEntry | null | undefined,
  index: number,
): AutoSyncNormalizedEntry {
  if (!entry || typeof entry !== 'object') {
    return {
      id: `unknown-${index}`,
      status: 'completed',
      playlist_name: 'Playlist pipeline run',
      trigger_source: 'pipeline',
      // The vanilla also sets `summary` here, which nothing reads.
      before_json: {},
      after_json: {},
      result_json: {},
    };
  }
  return {
    ...entry,
    id: entry.id ?? `history-${index}`,
    before_json: autoSyncParseHistoryObject(entry.before_json),
    after_json: autoSyncParseHistoryObject(entry.after_json),
    result_json: autoSyncParseHistoryObject(entry.result_json),
  };
}

/** 1666-1671. 'finished' and 'completed' are the same thing to a reader. */
export function autoSyncHistoryStatusLabel(status: string | undefined): string {
  if (status === 'completed' || status === 'finished') return 'Completed';
  if (status === 'error') return 'Error';
  if (status === 'skipped') return 'Skipped';
  return status || 'Run';
}

/** 1673-1677. Only two dots exist, so an unknown status reads as fine. */
export function autoSyncHistoryStatusClass(status: string | undefined): string {
  if (status === 'error' || status === 'skipped') return 'disabled';
  return 'enabled';
}

/** 1679-1685. */
export function autoSyncDurationLabel(seconds: number | string | undefined): string {
  const total = Math.max(0, Math.round(parseFloat(String(seconds)) || 0));
  if (total < 60) return `${total}s`;
  return `${Math.floor(total / 60)}m ${total % 60}s`;
}

/** 1687-1691. */
export function autoSyncDelta(after: unknown, before: unknown): number {
  return (parseInt(String(after), 10) || 0) - (parseInt(String(before), 10) || 0);
}

/** 1574-1579. '42 tracks' or '42 tracks (+3)'. */
export function autoSyncDeltaLabel(after: unknown, delta: number, unit: string): string {
  const a = parseInt(String(after), 10) || 0;
  if (!delta) return `${a} ${unit}`;
  return `${a} ${unit} (${delta > 0 ? '+' : ''}${delta})`;
}

/** 1668-1670 / 1770-1772. pos/neg/zero drives the colour. */
export function autoSyncDeltaClass(delta: number): string {
  return delta > 0 ? 'pos' : delta < 0 ? 'neg' : 'zero';
}

/** stats-automations.js 4265-4270, reached as a cross-file global. */
export function autoSyncTimeAgo(ts: string | undefined, now: number): string {
  if (!ts) return 'Never';
  const d = (now - autoSyncParseUTC(ts)) / 1000;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

/** 1861-1866. An unparseable value is shown RAW rather than swallowed. */
export function autoSyncFormatDateTime(value: string | undefined): string {
  if (!value) return '';
  const ts = autoSyncParseUTC(value);
  if (!Number.isFinite(ts)) return value;
  return new Date(ts).toLocaleString();
}

/** 1876-1882. */
export function autoSyncValueLabel(value: unknown): string {
  if (value === undefined || value === null || value === '') return 'Not recorded';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) {
    return value.length ? value.map(autoSyncValueLabel).join(', ') : 'None';
  }
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export type AutoSyncHistoryFilter = 'all' | 'error' | 'completed';

/** 1246-1249. Anything else falls back to 'all' rather than showing nothing. */
export function autoSyncNormalizeHistoryFilter(key: string | undefined): AutoSyncHistoryFilter {
  return key === 'error' || key === 'completed' ? key : 'all';
}

/**
 * 1399-1405 and 1211-1214 — the SAME predicate, used for both.
 *
 * DECLARED HARDENING: `entry?.status`, where the vanilla writes `h.status`.
 * The vanilla goes to real trouble to tolerate a malformed row — the
 * normalizer has a whole branch for "not an object at all" (1596-1607) and
 * each card build is wrapped in try/catch (1428-1432) — but its filter and
 * tab-count paths dereference `.status` BEFORE either of those runs, and the
 * tab counts run on every render regardless of the active filter (1211-1213).
 * So a null row throws out of renderAutoSyncHistoryPanel and blanks the entire
 * history panel, which is precisely the outcome the other two guards exist to
 * prevent. One optional chain restores the intent.
 */
export function autoSyncHistoryMatchesFilter(
  entry: AutoSyncHistoryEntry | null | undefined,
  filter: AutoSyncHistoryFilter,
): boolean {
  if (filter === 'error') return entry?.status === 'error' || entry?.status === 'skipped';
  if (filter === 'completed') {
    return entry?.status === 'completed' || entry?.status === 'finished';
  }
  return true;
}

export interface AutoSyncHistoryTab {
  key: AutoSyncHistoryFilter;
  label: string;
  count: number;
  hasErrors: boolean;
}

/**
 * 1210-1215. Counts come from the WHOLE loaded window, not the filtered view —
 * otherwise switching to Errors would report "Errors 3, Completed 0".
 */
export function autoSyncHistoryTabs(history: AutoSyncHistoryEntry[]): AutoSyncHistoryTab[] {
  const errors = history.filter((h) => autoSyncHistoryMatchesFilter(h, 'error')).length;
  return [
    { key: 'all', label: 'All', count: history.length, hasErrors: false },
    // 1217: only the Errors tab gets the attention class, and only when > 0.
    { key: 'error', label: 'Errors', count: errors, hasErrors: errors > 0 },
    {
      key: 'completed',
      label: 'Completed',
      count: history.filter((h) => autoSyncHistoryMatchesFilter(h, 'completed')).length,
      hasErrors: false,
    },
  ];
}

/** 1251-1254. Capped at 500 so a runaway click cannot ask for everything. */
export function autoSyncNextHistoryLimit(limit: number): number {
  return Math.min(500, limit + 50);
}

export interface AutoSyncHistoryLogLine {
  text: string;
  type: string;
}

/**
 * 1786-1794. The last 20 lines. A line is a string, or an object under any of
 * two message keys and two type keys, or — failing all that — its own JSON.
 */
export function autoSyncHistoryLogLines(
  logLines: AutoSyncHistoryEntry['log_lines'],
): AutoSyncHistoryLogLine[] {
  if (!Array.isArray(logLines) || !logLines.length) return [];
  return logLines.slice(-20).map((line) => ({
    text: typeof line === 'string' ? line : line.message || line.log_line || JSON.stringify(line),
    type: typeof line === 'object' ? line.type || line.log_type || 'info' : 'info',
  }));
}

export interface AutoSyncHistoryStat {
  label: string;
  before: number;
  after: number;
  delta: number;
}

/** 1722-1727. The four before/after cards, in fixed order. */
export function autoSyncHistoryStats(
  before: AutoSyncHistorySnapshot,
  after: AutoSyncHistorySnapshot,
): AutoSyncHistoryStat[] {
  const stat = (label: string, key: keyof AutoSyncHistorySnapshot): AutoSyncHistoryStat => ({
    label,
    before: parseInt(String(before[key]), 10) || 0,
    after: parseInt(String(after[key]), 10) || 0,
    delta: autoSyncDelta(after[key], before[key]),
  });
  return [
    stat('Tracks', 'track_count'),
    stat('Discovered', 'discovered_count'),
    stat('Wishlisted', 'wishlisted_count'),
    stat('In library', 'in_library_count'),
  ];
}

/**
 * 1740-1747. `tracks_discovered` is deliberately NOT here: it is a status
 * STRING ('completed'), not a count, and rendering it produced a nonsense
 * "Discovered: completed" pill. The vanilla's comment at 1737-1740 says so.
 */
export function autoSyncHistoryResultPills(
  result: Record<string, unknown>,
): { label: string; value: string }[] {
  return (
    [
      ['Refreshed', result.playlists_refreshed],
      ['Synced', result.tracks_synced],
      ['Skipped', result.sync_skipped],
      ['Wishlisted', result.wishlist_queued],
    ] as [string, unknown][]
  )
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([label, v]) => ({ label, value: String(v) }));
}

/* ── The modal shell (571-740, 1256-1312) ───────────────────────────────── */

export type AutoSyncTab = 'schedule' | 'weekly' | 'automations' | 'history';

export const AUTO_SYNC_TABS: readonly AutoSyncTab[] = [
  'schedule',
  'weekly',
  'automations',
  'history',
];

/** 733-735. An unknown tab falls back to the hourly board. */
export function autoSyncNormalizeTab(tab: string | undefined): AutoSyncTab {
  return (AUTO_SYNC_TABS as readonly string[]).includes(tab as string)
    ? (tab as AutoSyncTab)
    : 'schedule';
}

export interface AutoSyncSummary {
  scheduledCount: number;
  enabledCount: number;
  pipelineCount: number;
  totalTracks: number;
  /** 700-703. Drives the red badge on the Run History tab. */
  historyErrorCount: number;
}

/**
 * 658-664 and 700-703.
 *
 * Two things worth knowing. `scheduledCount` sums BOTH schedule maps, so a
 * playlist can only appear once — the one-schedule-per-playlist invariant the
 * save paths enforce is what makes that true. And `enabledCount` filters on
 * `s.enabled` as plain TRUTHINESS here (660-661), where every other read of
 * the same field treats it as tri-state (`!== false && !== 0`). That means a
 * schedule whose `enabled` the backend omitted counts as scheduled but NOT as
 * active. Transcribed as-is: it is a header statistic, and "fixing" it would
 * make the port's number differ from the vanilla's for the same data.
 */
export function autoSyncSummary(state: {
  playlists: MirroredRow[];
  playlistSchedules: Record<string, AutoSyncHourlyEntry>;
  weeklySchedules: Record<string, AutoSyncWeeklyEntry>;
  automationPipelines: AutomationRow[];
  runHistory: AutoSyncHistoryEntry[];
}): AutoSyncSummary {
  const hourly = Object.values(state.playlistSchedules || {});
  const weekly = Object.values(state.weeklySchedules || {});
  return {
    scheduledCount: hourly.length + weekly.length,
    enabledCount: hourly.filter((s) => s.enabled).length + weekly.filter((s) => s.enabled).length,
    pipelineCount: (state.automationPipelines || []).length,
    totalTracks: (state.playlists || []).reduce(
      (sum, p) => sum + (parseInt(String(p.track_count), 10) || 0),
      0,
    ),
    historyErrorCount: (state.runHistory || []).filter((h) =>
      autoSyncHistoryMatchesFilter(h, 'error'),
    ).length,
  };
}

/**
 * 1303-1313. The custom-interval value, validated. The vanilla collects it
 * with `window.prompt`, which this repo forbids; the port asks in a SoulSync
 * modal instead, so the parsing and the asking are separated here.
 */
export function autoSyncParseCustomInterval(raw: string): { hours: number } | { error: string } {
  const hours = parseInt(raw, 10);
  if (!Number.isFinite(hours) || hours < 1) {
    return { error: 'Interval must be a whole number of hours, 1 or greater' };
  }
  return { hours };
}

/* ── The save payloads (2069-2082, 2266-2277) ───────────────────────────── */

/**
 * The automation body both save paths POST or PUT. The two differ ONLY in
 * `trigger_type` and `trigger_config`; everything else — the `Auto-Sync:` name
 * prefix, the empty `then_actions`, the group and the ownership stamp — is
 * identical, and all three of those are what
 * `autoSyncIsScheduleOwned` later reads back to decide the board owns the row.
 */
export function autoSyncSchedulePayload(
  playlist: MirroredRow,
  playlistId: number | string,
  trigger: { trigger_type: string; trigger_config: unknown },
): Record<string, unknown> {
  return {
    name: `Auto-Sync: ${playlist.name}`,
    trigger_type: trigger.trigger_type,
    trigger_config: trigger.trigger_config,
    ...autoSyncActionForPlaylist(playlist, playlistId),
    then_actions: [],
    group_name: 'Playlist Auto-Sync',
    owned_by: 'auto_sync',
  };
}

/** 2098 / 2283. The success toast each save path shows. */
export function autoSyncSavedToast(
  playlistName: string,
  kind: 'hourly' | 'weekly',
  detail: string,
): string {
  return kind === 'hourly'
    ? `${playlistName} scheduled every ${detail}`
    : `${playlistName} scheduled ${detail.toLowerCase()}`;
}
