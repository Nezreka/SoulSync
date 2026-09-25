/**
 * Tools page — the endpoint layer.
 *
 * Raw `fetch`, not the ky client, for the same reason the explorer port used it:
 * ky throws on any non-2xx, and several of these calls deliberately DON'T treat
 * a bad response as fatal (a status poll that 500s must leave the previous UI
 * state alone rather than blanking the card). Matching the vanilla's error
 * handling exactly matters more here than sharing a client.
 *
 * Each function names the vanilla it came from. Where the vanilla's handling is
 * odd — swallowed errors, a success flag checked in addition to the HTTP status,
 * a fetch whose result is ignored — that is preserved and commented, not tidied.
 *
 * NOT in scope: the discovery-pool modal, manual library match and config
 * migration each live in their own self-contained vanilla file and are opened,
 * not re-implemented. Their endpoints stay there.
 */

import type { FindingGroup, FindingTypeInfo } from './-tools.groups';
import type {
  BackupListResponse,
  BulkFixStatus,
  CacheHealthStats,
  DatabaseStats,
  DbRefreshType,
  DbUpdateState,
  DiscoveryPoolStats,
  DuplicateCleanState,
  MediaScanStatus,
  MetadataCacheStats,
  MetadataUpdateState,
  ReconcileIdsState,
  RepairFinding,
  RepairFindingCounts,
  RepairFindingsPage,
  RepairJob,
  RepairJobProgress,
  RepairJobRun,
  RepairStatus,
} from './-tools.types';

const JSON_HEADERS = { 'Content-Type': 'application/json' } as const;

function postJson(url: string, body?: unknown): Promise<Response> {
  if (body === undefined) return fetch(url, { method: 'POST' });
  return fetch(url, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) });
}

// ── Library Maintenance: status + jobs ───────────────────────────────────────

/**
 * `updateRepairStatus`. Returns null instead of throwing when the endpoint is
 * unavailable — the vanilla logs a warning and leaves the UI as it was, which
 * matters because this runs on a 5s poll.
 */
export async function fetchRepairStatus(): Promise<RepairStatus | null> {
  try {
    const response = await fetch('/api/repair/status');
    if (!response.ok) return null;
    return (await response.json()) as RepairStatus;
  } catch {
    return null;
  }
}

/** `openRepairModal`'s progress hydrate. `{}` means nothing is running. */
export async function fetchRepairProgress(): Promise<Record<string, RepairJobProgress>> {
  try {
    const response = await fetch('/api/repair/progress');
    if (!response.ok) return {};
    return ((await response.json()) as Record<string, RepairJobProgress>) || {};
  } catch {
    return {};
  }
}

/** `toggleRepairMaster`. Throws on failure so the caller can toast. */
export async function toggleRepairMaster(): Promise<{ enabled: boolean }> {
  const response = await fetch('/api/repair/toggle', { method: 'POST' });
  if (!response.ok) throw new Error('Failed to toggle');
  return (await response.json()) as { enabled: boolean };
}

/** `loadRepairJobs`. */
export async function fetchRepairJobs(): Promise<RepairJob[]> {
  const response = await fetch('/api/repair/jobs');
  if (!response.ok) throw new Error('Failed to fetch jobs');
  const data = (await response.json()) as { jobs?: RepairJob[] };
  return data.jobs || [];
}

/**
 * `toggleRepairJob`. The vanilla does NOT check the response — it flips the card
 * visuals optimistically and only catches a thrown network error.
 */
export async function setRepairJobEnabled(jobId: string, enabled: boolean): Promise<void> {
  await postJson(`/api/repair/jobs/${jobId}/toggle`, { enabled });
}

/**
 * `saveRepairJobSettings`. Also unchecked in the vanilla — it toasts success as
 * soon as the request resolves, whatever the status.
 */
export async function saveRepairJobSettings(
  jobId: string,
  intervalHours: number | null,
  settings: Record<string, unknown>,
): Promise<void> {
  await fetch(`/api/repair/jobs/${jobId}/settings`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify({ interval_hours: intervalHours, settings }),
  });
}

/** `runRepairJobNow`. Unchecked; the caller reloads the list after 1s. */
export async function runRepairJob(jobId: string): Promise<void> {
  await postJson(`/api/repair/jobs/${jobId}/run`);
}

/**
 * `stopRepairJobNow`. This one DOES read the body: `stopped` false means the job
 * was already idle, which the caller reports differently and follows with a
 * reload, and an `error` field is thrown so the button can be restored.
 */
export async function stopRepairJob(jobId: string): Promise<{ stopped: boolean }> {
  const response = await postJson(`/api/repair/jobs/${jobId}/stop`);
  const data = (await response.json()) as { stopped?: boolean; error?: string };
  if (data.error) throw new Error(data.error);
  return { stopped: Boolean(data.stopped) };
}

/**
 * `loadRepairHistory`. Typed as RepairJobRun[] rather than `RepairJob['last_run'][]`
 * — that alias includes null/undefined (a job may never have run), but a row in
 * the history list always exists, so borrowing it would force pointless null
 * checks all through the history tab.
 */
export async function fetchRepairHistory(limit = 50): Promise<RepairJobRun[]> {
  const response = await fetch(`/api/repair/history?limit=${limit}`);
  if (!response.ok) throw new Error('Failed to fetch history');
  const data = (await response.json()) as { runs?: RepairJobRun[] };
  return data.runs || [];
}

// ── Library Maintenance: findings ────────────────────────────────────────────

export interface FindingsQuery {
  jobId?: string;
  severity?: string;
  status?: string;
  /** Scopes the list to one finding type — the unit the inbox works in. */
  findingType?: string;
  /** Whitelisted server-side: newest | oldest | severity | path. */
  sort?: string;
  /** Title + path search. Non-empty search escapes the grouping entirely. */
  q?: string;
  page: number;
  limit: number;
}

/**
 * `loadRepairFindings`. Only non-empty filters are sent — an empty `job_id`
 * means "all jobs" and must be omitted rather than passed blank.
 */
export async function fetchRepairFindings(query: FindingsQuery): Promise<RepairFindingsPage> {
  const params = new URLSearchParams();
  if (query.jobId) params.set('job_id', query.jobId);
  if (query.severity) params.set('severity', query.severity);
  if (query.status) params.set('status', query.status);
  if (query.findingType) params.set('finding_type', query.findingType);
  if (query.sort) params.set('sort', query.sort);
  if (query.q) params.set('q', query.q);
  params.set('page', String(query.page));
  params.set('limit', String(query.limit));

  const response = await fetch(`/api/repair/findings?${params}`);
  if (!response.ok) {
    // Carry the server's own message. The endpoint returns {error: "..."} on a
    // 500, and discarding it left users staring at a bare "Error loading
    // findings" with no way to tell us what broke — unfixable from their side
    // and undiagnosable from ours.
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { error?: string };
      if (body?.error) detail = body.error;
    } catch {
      /* non-JSON body — the status is all we have */
    }
    throw new Error(detail);
  }
  const data = (await response.json()) as Partial<RepairFindingsPage>;
  return { items: data.items || [], total: data.total || 0, page: data.page || 0 };
}

export interface FindingAlbumGroup {
  group_by: 'album' | 'artist';
  key: string;
  artist: string | null;
  album: string | null;
  count: number;
  worst_score: number | null;
  best_score: number | null;
  worst_quality: string;
  best_quality: string;
  album_thumb_url: string | null;
  artist_thumb_url: string | null;
  artist_id: string | null;
  first_seen: string | null;
  last_seen: string | null;
}

export interface FindingAlbumQuery {
  groupBy: 'album' | 'artist';
  jobId?: string;
  status?: string;
  findingType?: string;
  q?: string;
  limit?: number;
}

/**
 * Findings folded to one row per album or artist, worst audio first.
 *
 * Degrades to an empty list rather than throwing, the same way
 * `fetchFindingGroups` does: a grouped view that cannot load should leave the
 * flat list usable instead of taking the whole surface down with it.
 */
export async function fetchFindingAlbums(
  query: FindingAlbumQuery,
): Promise<FindingAlbumGroup[]> {
  try {
    const params = new URLSearchParams({ group_by: query.groupBy });
    if (query.jobId) params.set('job_id', query.jobId);
    if (query.status) params.set('status', query.status);
    if (query.findingType) params.set('finding_type', query.findingType);
    if (query.q) params.set('q', query.q);
    if (query.limit) params.set('limit', String(query.limit));
    const response = await fetch(`/api/repair/findings/albums?${params}`);
    if (!response.ok) return [];
    const data = (await response.json()) as { groups?: FindingAlbumGroup[] };
    return data.groups || [];
  } catch {
    return [];
  }
}

/** `loadRepairFindingsDashboard`. */
export async function fetchFindingCounts(): Promise<RepairFindingCounts> {
  const response = await fetch('/api/repair/findings/counts');
  if (!response.ok) throw new Error('Failed to fetch counts');
  return (await response.json()) as RepairFindingCounts;
}

/**
 * The inbox payload: one row per finding TYPE with per-status counts.
 *
 * Empty array rather than a throw on failure — the inbox degrading to "no
 * groups" alongside a visible error beats the whole maintenance surface
 * disappearing because one of three parallel loads lost its connection.
 */
export async function fetchFindingGroups(): Promise<FindingGroup[]> {
  try {
    const response = await fetch('/api/repair/findings/groups');
    if (!response.ok) return [];
    const data = (await response.json()) as { groups?: FindingGroup[] };
    return data.groups || [];
  } catch {
    return [];
  }
}

/**
 * The finding-type catalog — label, verb, fixable, destructive, per type.
 *
 * The single source of truth for what the UI may offer. Same degradation as
 * the groups call: an empty catalog renders review-only rows, which is the
 * safe way to be wrong.
 */
export async function fetchFindingTypes(): Promise<FindingTypeInfo[]> {
  try {
    const response = await fetch('/api/repair/finding-types');
    if (!response.ok) return [];
    const data = (await response.json()) as { types?: FindingTypeInfo[] };
    return data.types || [];
  } catch {
    return [];
  }
}

/** The undo half of dismiss — dismiss is permanent by design, which is only
 *  safe to offer freely if it can be taken back. */
export async function reopenFinding(id: number): Promise<boolean> {
  try {
    const response = await postJson(`/api/repair/findings/${id}/reopen`);
    const data = (await response.json()) as { success?: boolean };
    return Boolean(data.success);
  } catch {
    return false;
  }
}

/** Dismiss every PENDING finding of one type — the group-level dismiss.
 *  Scoped by type server-side; the alternative was shipping thousands of ids
 *  to the browser only to post them straight back. */
export async function dismissFindingType(
  findingType: string,
): Promise<{ success?: boolean; updated?: number; error?: string }> {
  const response = await postJson('/api/repair/findings/bulk', {
    finding_type: findingType,
    action: 'dismiss',
  });
  return (await response.json()) as { success?: boolean; updated?: number; error?: string };
}

export interface FixResult {
  success?: boolean;
  message?: string;
  error?: string;
}

/**
 * `fixRepairFinding` / `selectDuplicateToKeep` / `applyCoverArtTarget` — all
 * three POST the same endpoint; only the `fix_action` differs (an action name, a
 * track id to keep, or 'album'/'artist' for which image to apply).
 */
export async function fixFinding(id: number, fixAction?: string | null): Promise<FixResult> {
  const response = await postJson(
    `/api/repair/findings/${id}/fix`,
    fixAction ? { fix_action: fixAction } : {},
  );
  return (await response.json()) as FixResult;
}

/** `resolveRepairFinding`. Response ignored by the vanilla. */
export async function resolveFinding(id: number): Promise<void> {
  await postJson(`/api/repair/findings/${id}/resolve`);
}

/** `dismissRepairFinding`. Response ignored by the vanilla. */
export async function dismissFinding(id: number): Promise<void> {
  await postJson(`/api/repair/findings/${id}/dismiss`);
}

/** Used by `bulkFixFindings`' inline dismiss paths, which DO check `resp.ok`. */
export async function dismissFindingChecked(id: number): Promise<boolean> {
  const response = await postJson(`/api/repair/findings/${id}/dismiss`);
  return response.ok;
}

/** `bulkRepairAction`. Response ignored; the toast fires regardless. */
export async function bulkFindingAction(ids: number[], action: string): Promise<void> {
  await postJson('/api/repair/findings/bulk', { ids, action });
}

export interface ClearFindingsResult {
  success?: boolean;
  deleted?: number;
  error?: string;
}

/** `clearRepairFindings` and the discography "Just Clear" path. */
/**
 * Delete findings matching the CURRENT filters.
 *
 * Takes the same shape the list query does, because the button promises
 * "matching current filters" and used to send only two of them — a user who
 * had searched for one album and pressed Clear lost every finding the job and
 * status filters matched (#1142). Anything added to `FindingsQuery` that
 * narrows the list has to be threaded through here too.
 */
export async function clearFindings(
  filters: Pick<FindingsQuery, 'jobId' | 'status' | 'severity' | 'findingType' | 'q'>,
): Promise<ClearFindingsResult> {
  const body: Record<string, string> = {};
  if (filters.jobId) body.job_id = filters.jobId;
  if (filters.status) body.status = filters.status;
  if (filters.severity) body.severity = filters.severity;
  if (filters.findingType) body.finding_type = filters.findingType;
  if (filters.q?.trim()) body.q = filters.q.trim();
  const response = await postJson('/api/repair/findings/clear', body);
  return (await response.json()) as ClearFindingsResult;
}

export interface BulkFixStartResult {
  started?: boolean;
  already_running?: boolean;
  total?: number;
  error?: string;
}

/**
 * `fixAllMatchingFindings`. Runs in the BACKGROUND: at library scale a
 * synchronous fix-all outlives every browser and proxy timeout, so the user was
 * told it failed while the server quietly kept going. This returns immediately
 * and the caller polls `fetchBulkFixStatus`.
 */
export async function startBulkFix(options: {
  jobId?: string;
  severity?: string;
  fixAction?: string | null;
  /** Scopes the run to one finding type. A `fixAction` is only accepted when
   *  the resolved selection is a single type — the backend 400s otherwise,
   *  because one type's "delete" is another's "track id to keep". */
  findingType?: string;
  /** Skips every destructive type. What "Fix all safe" sends. */
  safeOnly?: boolean;
}): Promise<BulkFixStartResult> {
  const body: Record<string, string | boolean> = {};
  if (options.jobId) body.job_id = options.jobId;
  if (options.severity) body.severity = options.severity;
  if (options.fixAction) body.fix_action = options.fixAction;
  if (options.findingType) body.finding_type = options.findingType;
  if (options.safeOnly) body.safe_only = true;
  const response = await postJson('/api/repair/findings/bulk-fix-start', body);
  return (await response.json()) as BulkFixStartResult;
}

/**
 * `_watchBulkFixRun`'s poll and `_checkBulkFixResume`. Returns null on a
 * transient failure so the poller keeps going rather than declaring the run
 * finished — the vanilla's `catch { return; }` has exactly that effect.
 */
export async function fetchBulkFixStatus(): Promise<BulkFixStatus | null> {
  try {
    const response = await fetch('/api/repair/bulk-fix/status');
    return (await response.json()) as BulkFixStatus;
  } catch {
    return null;
  }
}

/** `stopBulkFixRun`. Fire and forget — the vanilla explicitly swallows failures. */
export function stopBulkFix(): void {
  void fetch('/api/repair/bulk-fix/stop', { method: 'POST' }).catch(() => {});
}

// ── Cache health ─────────────────────────────────────────────────────────────

/**
 * `_loadCacheHealthStats` / `openCacheHealthModal`. The inline bar hides itself
 * when both totals are zero; that check belongs to the caller, so this returns
 * the stats untouched.
 */
export async function fetchCacheHealth(): Promise<CacheHealthStats | null> {
  try {
    const response = await fetch('/api/repair/cache-health');
    if (!response.ok) return null;
    return (await response.json()) as CacheHealthStats;
  } catch {
    return null;
  }
}

// ── Database updater ─────────────────────────────────────────────────────────

/** `fetchAndUpdateDbStats`. */
export async function fetchDatabaseStats(): Promise<DatabaseStats | null> {
  try {
    const response = await fetch('/api/database/stats');
    if (!response.ok) return null;
    return (await response.json()) as DatabaseStats;
  } catch {
    return null;
  }
}

export interface StartDbUpdateResult {
  ok: boolean;
  error?: string;
}

/**
 * `handleDbUpdateButtonClick`.
 *
 * Checks BOTH the HTTP status and the body's success flag: a 200 carrying
 * `success: false` must not be mistaken for a started job (#859). Deep scan and
 * full refresh use distinct backend flags and deep takes precedence server-side,
 * so only its flag is sent when it is selected.
 */
export async function startDatabaseUpdate(
  refreshType: DbRefreshType,
): Promise<StartDbUpdateResult> {
  const body =
    refreshType === 'deep' ? { deep_scan: true } : { full_refresh: refreshType === 'full' };
  const response = await postJson('/api/database/update', body);
  const data = (await response.json().catch(() => ({}))) as { success?: boolean; error?: string };
  if (response.ok && data.success !== false) return { ok: true };
  return { ok: false, error: data.error || 'Failed to start update.' };
}

/** `handleDbUpdateButtonClick`'s stop branch. */
export async function stopDatabaseUpdate(): Promise<boolean> {
  const response = await fetch('/api/database/update/stop', { method: 'POST' });
  return response.ok;
}

/**
 * `checkAndUpdateDbProgress` and `armDbUpdateSafetyPoll`'s tick. Null on failure
 * keeps the poll alive — the vanilla explicitly does NOT stop polling on a
 * network error.
 */
export async function fetchDbUpdateStatus(timeoutMs = 10000): Promise<DbUpdateState | null> {
  try {
    const response = await fetch('/api/database/update/status', {
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) return null;
    return (await response.json()) as DbUpdateState;
  } catch {
    return null;
  }
}

// ── Reconcile IDs from file tags ─────────────────────────────────────────────

export interface StartReconcileResult {
  ok: boolean;
  error?: string;
}

/** `handleReconcileIdsButtonClick`. */
export async function startReconcileIds(): Promise<StartReconcileResult> {
  const response = await fetch('/api/library/reconcile-embedded-ids', { method: 'POST' });
  if (response.ok) return { ok: true };
  const data = (await response.json().catch(() => ({}))) as { error?: string };
  return { ok: false, error: data.error || 'could not start' };
}

/** `checkAndUpdateReconcileIdsProgress`. */
export async function fetchReconcileIdsStatus(
  timeoutMs = 10000,
): Promise<ReconcileIdsState | null> {
  try {
    const response = await fetch('/api/library/reconcile-embedded-ids/status', {
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) return null;
    return (await response.json()) as ReconcileIdsState;
  } catch {
    return null;
  }
}

// ── Duplicate cleaner ────────────────────────────────────────────────────────

export interface StartDuplicateCleanResult {
  ok: boolean;
  error?: string;
}

/** `handleDuplicateCleanButtonClick`. */
export async function startDuplicateClean(): Promise<StartDuplicateCleanResult> {
  const response = await fetch('/api/duplicate-cleaner/start', {
    method: 'POST',
    headers: JSON_HEADERS,
  });
  if (response.ok) return { ok: true };
  const data = (await response.json().catch(() => ({}))) as { error?: string };
  return { ok: false, error: data.error };
}

/** `handleDuplicateCleanButtonClick`'s stop branch. */
export async function stopDuplicateClean(): Promise<boolean> {
  const response = await fetch('/api/duplicate-cleaner/stop', { method: 'POST' });
  return response.ok;
}

/** `checkAndUpdateDuplicateCleanProgress`. Null keeps the poll alive. */
export async function fetchDuplicateCleanStatus(
  timeoutMs = 10000,
): Promise<DuplicateCleanState | null> {
  try {
    const response = await fetch('/api/duplicate-cleaner/status', {
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) return null;
    return (await response.json()) as DuplicateCleanState;
  } catch {
    return null;
  }
}

// ── Metadata updater ─────────────────────────────────────────────────────────

/** `handleMetadataUpdateButtonClick`. Throws with the server's message. */
export async function startMetadataUpdate(refreshIntervalDays: number): Promise<void> {
  const response = await postJson('/api/metadata/start', {
    refresh_interval_days: refreshIntervalDays,
  });
  const data = (await response.json()) as { success?: boolean; error?: string };
  if (!data.success) throw new Error(data.error || 'Failed to start metadata update');
}

/** `handleMetadataUpdateButtonClick`'s stop branch. */
export async function stopMetadataUpdate(): Promise<void> {
  const response = await fetch('/api/metadata/stop', { method: 'POST', headers: JSON_HEADERS });
  if (!response.ok) throw new Error('Failed to stop metadata update');
}

/** `checkMetadataUpdateStatus` / `checkAndRestoreMetadataUpdateState`. */
export async function fetchMetadataUpdateStatus(): Promise<MetadataUpdateState | null> {
  try {
    const response = await fetch('/api/metadata/status');
    const data = (await response.json()) as { success?: boolean; status?: MetadataUpdateState };
    if (data.success && data.status) return data.status;
    return null;
  } catch {
    return null;
  }
}

// ── Media server + scan ──────────────────────────────────────────────────────

/**
 * `checkAndHideMetadataUpdaterForNonPlex` / `checkAndShowMediaScanForPlex`.
 * Both call this; the metadata card shows for Plex AND Jellyfin, the scan card
 * for Plex only.
 */
export async function fetchActiveMediaServer(): Promise<string | null> {
  try {
    const response = await fetch('/api/active-media-server');
    const data = (await response.json()) as { success?: boolean; active_server?: string };
    return data.success ? (data.active_server ?? null) : null;
  } catch {
    return null;
  }
}

export interface ScanRequestResult {
  success?: boolean;
  error?: string;
  scan_info?: { delay_seconds?: number } | null;
}

/** `handleMediaScanButtonClick`. The delay drives the countdown before polling. */
export async function requestMediaScan(reason: string): Promise<ScanRequestResult> {
  const response = await postJson('/api/scan/request', { reason });
  return (await response.json()) as ScanRequestResult;
}

/** `handleMediaScanButtonClick`'s completion poll. */
export async function fetchMediaScanStatus(): Promise<MediaScanStatus | null> {
  try {
    const response = await fetch('/api/scan/status');
    const data = (await response.json()) as { success?: boolean; status?: MediaScanStatus };
    if (data.success && data.status) return data.status;
    return null;
  } catch {
    return null;
  }
}

// ── Backups ──────────────────────────────────────────────────────────────────

/** `loadBackupList`. */
export async function fetchBackups(): Promise<BackupListResponse | null> {
  try {
    const response = await fetch('/api/database/backups');
    const data = (await response.json()) as BackupListResponse;
    return data.success ? data : null;
  } catch {
    return null;
  }
}

export interface BackupNowResult {
  success?: boolean;
  size_mb?: number;
  error?: string;
}

/** `handleBackupNowClick`. */
export async function createBackup(): Promise<BackupNowResult> {
  const response = await fetch('/api/database/backup', { method: 'POST' });
  return (await response.json()) as BackupNowResult;
}

/** `downloadBackup` builds an <a> rather than fetching; this is just the URL. */
export function backupDownloadUrl(filename: string): string {
  return `/api/database/backups/${encodeURIComponent(filename)}/download`;
}

export interface RestoreBackupResult {
  success?: boolean;
  restored_from?: string;
  artist_count?: number;
  safety_backup?: string;
  version_warning?: string;
  /** Set when the backup was taken on a different SoulSync version; the caller
   *  confirms and retries with force. */
  version_mismatch?: boolean;
  backup_version?: string;
  current_version?: string;
  error?: string;
}

/** `restoreBackup`. `force` re-sends after the version-mismatch confirmation. */
export async function restoreBackup(filename: string, force = false): Promise<RestoreBackupResult> {
  const url = `/api/database/backups/${encodeURIComponent(filename)}/restore`;
  const response = force ? await postJson(url, { force: true }) : await postJson(url);
  return (await response.json()) as RestoreBackupResult;
}

/** `deleteBackup`. */
export async function deleteBackup(
  filename: string,
): Promise<{ success?: boolean; deleted?: string; error?: string }> {
  const response = await fetch(`/api/database/backups/${encodeURIComponent(filename)}`, {
    method: 'DELETE',
  });
  return (await response.json()) as { success?: boolean; deleted?: string; error?: string };
}

// ── Metadata cache ───────────────────────────────────────────────────────────

/** `loadMetadataCacheStats`. Silently null — the cache may not be initialised. */
export async function fetchMetadataCacheStats(): Promise<MetadataCacheStats | null> {
  try {
    const response = await fetch('/api/metadata-cache/stats');
    if (!response.ok) return null;
    return (await response.json()) as MetadataCacheStats;
  } catch {
    return null;
  }
}

export interface ClearCacheResult {
  success?: boolean;
  cleared?: number;
}

/** `clearMetadataCache`. */
export async function clearMetadataCache(): Promise<ClearCacheResult> {
  const response = await fetch('/api/metadata-cache/clear', { method: 'DELETE' });
  return (await response.json()) as ClearCacheResult;
}

/**
 * `clearMetadataCacheBySource`. Same endpoint as clear-all with a `source` query
 * param, NOT a separate route (web_server.py has exactly two clear routes:
 * `/clear` and `/clear-musicbrainz`).
 *
 * The vanilla interpolates the source raw; encoded here because it costs nothing
 * for the current values (spotify/itunes/deezer/beatport/discogs are all
 * URL-safe) and stops a future source name from breaking the query.
 */
export async function clearMetadataCacheBySource(source: string): Promise<ClearCacheResult> {
  const response = await fetch(`/api/metadata-cache/clear?source=${encodeURIComponent(source)}`, {
    method: 'DELETE',
  });
  return (await response.json()) as ClearCacheResult;
}

/**
 * `clearMusicBrainzCache`. `failedOnly` is also how the Failed MB Lookups modal
 * clears everything, so the two share this call.
 */
export async function clearMusicBrainzCache(failedOnly = false): Promise<ClearCacheResult> {
  const url = failedOnly
    ? '/api/metadata-cache/clear-musicbrainz?failed_only=true'
    : '/api/metadata-cache/clear-musicbrainz';
  const response = await fetch(url, { method: 'DELETE' });
  return (await response.json()) as ClearCacheResult;
}

// ── Blacklist ────────────────────────────────────────────────────────────────

export interface BlacklistEntry {
  id: number;
  track_artist?: string | null;
  track_title?: string | null;
  blocked_filename?: string | null;
  blocked_username?: string | null;
  created_at?: string | null;
}

/**
 * `loadBlacklistCount` and `openBlacklistModal` hit the same endpoint but read
 * it DIFFERENTLY, so both flags are returned rather than one collapsed answer:
 *
 * - the count does `data.entries?.length || 0` and never looks at `success`
 * - the modal treats `!data.success` as an empty list, even if entries came back
 *
 * Collapsing that to "just the entries" would make the modal show rows the
 * vanilla hides. Each caller reproduces its own behaviour from these two fields.
 */
export async function fetchBlacklist(): Promise<{
  success: boolean;
  entries: BlacklistEntry[];
}> {
  try {
    const response = await fetch('/api/library/blacklist');
    const data = (await response.json()) as { success?: boolean; entries?: BlacklistEntry[] };
    return { success: Boolean(data.success), entries: data.entries || [] };
  } catch {
    return { success: false, entries: [] };
  }
}

/** `_removeBlacklistEntry`. */
export async function removeBlacklistEntry(id: number): Promise<boolean> {
  const response = await fetch(`/api/library/blacklist/${id}`, { method: 'DELETE' });
  const data = (await response.json()) as { success?: boolean };
  return Boolean(data.success);
}

// ── Discovery pool (stats only) ──────────────────────────────────────────────

/**
 * `loadDiscoveryPoolStats` — just the two card counters. The modal itself stays
 * in stats-automations.js and is opened by name, so its endpoints aren't here.
 *
 * NULL, not zeroes, when the payload has no `stats`. The vanilla reads
 * `data.stats.matched` unguarded, so a missing `stats` throws into an empty
 * catch and the card keeps whatever it was showing — which on first load is the
 * em dash placeholder. Returning zeroes here would print a confident "0 matched"
 * over a pool that simply failed to load.
 */
export async function fetchDiscoveryPoolStats(): Promise<DiscoveryPoolStats | null> {
  try {
    const response = await fetch('/api/discovery-pool');
    const data = (await response.json()) as { stats?: DiscoveryPoolStats };
    if (!data.stats) return null;
    return { matched: data.stats.matched || 0, failed: data.stats.failed || 0 };
  } catch {
    return null;
  }
}

// ── Artist lookup (findings → artist page) ───────────────────────────────────

/**
 * `openFindingArtist`'s fallback for findings that predate the stored
 * `artist_id`. limit=50 because the search is a CONTAINS match sorted
 * alphabetically, so a short name ("Low") can sort behind many substring hits
 * and a small page would push the exact match off page one.
 */
export async function searchLibraryArtists(
  name: string,
  limit = 50,
): Promise<Array<{ id: string | number; name: string }>> {
  const response = await fetch(
    `/api/library/artists?search=${encodeURIComponent(name)}&limit=${limit}`,
  );
  const data = (await response.json()) as {
    artists?: Array<{ id: string | number; name: string }>;
  };
  return data?.artists || [];
}

/** Exact (case-insensitive) name match only — the vanilla refuses to guess a
 *  fuzzy result and says so instead. */
export function findExactArtist<T extends { name?: string | null; id?: unknown }>(
  artists: readonly T[],
  name: string,
): T | null {
  const match = artists.find((artist) => (artist.name || '').toLowerCase() === name.toLowerCase());
  return match && match.id != null ? match : null;
}

export type { RepairFinding };
