/**
 * Types for the Tools page — the 11 tool cards plus the Library Maintenance
 * hero (jobs / findings / history).
 *
 * Shapes are taken from what the vanilla code actually reads off each endpoint,
 * not from an idealised API contract. Where the backend is loose (`details` on a
 * finding is a per-type free-form blob) the type stays loose too, so the port
 * can't pretend to a guarantee the server doesn't make.
 */

/**
 * A known set of values that the server is nonetheless free to extend.
 *
 * `'a' | 'b' | string` collapses to plain `string`, which loses the editor hints
 * AND trips no-redundant-type-constituents. This keeps the documented values
 * suggestable while still accepting whatever the API actually sends.
 */
export type LiteralUnion<T extends string> = T | (string & Record<never, never>);

// ── Library Maintenance ──────────────────────────────────────────────────────

/** The four anchors of the maintenance surface, in scroll order. They were
 *  tabs; tabs hid the findings you came for behind a room you had to know to
 *  enter, and hid whether the library was alright at all. */
export type RepairSection = 'health' | 'findings' | 'operations' | 'history';

export type RepairFindingStatus = 'pending' | 'resolved' | 'dismissed';

/** What the JOBS actually emit. `critical` was in this union for a long time
 *  and nothing ever produced it, while `error` — the corruption detector's
 *  severity, the most urgent findings in the system — was missing, so those
 *  rows were unreachable by every severity filter and count. The CSS class is
 *  still `.critical`; see `findingSeverityClass`. */
export type RepairSeverity = 'info' | 'warning' | 'error';

/** One row of a job's editable settings. `_section_*` keys are group headers,
 *  and `_interval_hours` is synthesised by the UI rather than sent by the API. */
export type RepairJobSettings = Record<string, unknown>;

export interface RepairJobRun {
  finished_at?: string | null;
  started_at?: string | null;
  items_scanned?: number | null;
  findings_created?: number | null;
  auto_fixed?: number | null;
  duration_seconds?: number | null;
  errors?: number | null;
  status?: string | null;
  display_name?: string | null;
  job_id?: string | null;
  id?: number | null;
  /** Why a run failed. Recorded since phase 1 and read by nobody until the
   *  run history learned to expand — 'failed' with no reason is the one thing
   *  a failed run is worth opening for. */
  error_text?: string | null;
}

export interface RepairJob {
  job_id: string;
  display_name: string;
  description?: string | null;
  help_text?: string | null;
  /** The family the backend files this job under — served, not guessed, so a
   *  new job cannot be grouped differently here than it is there. */
  category?: string | null;
  icon?: string | null;
  enabled: boolean;
  is_running: boolean;
  auto_fix?: boolean;
  interval_hours: number;
  next_run?: string | null;
  last_run?: RepairJobRun | null;
  /** Current pending count from the API. The job card prefers this over the
   *  historical `last_run.findings_created` — see `repairJobBadge`. */
  pending_findings_count?: number | null;
  settings?: RepairJobSettings | null;
  /** Per-key allowed values; presence turns the input into a <select>. */
  setting_options?: Record<string, unknown[]> | null;
}

/** Live progress frame pushed on `repair:progress`, keyed by job id. */
export interface RepairJobProgress {
  status: LiteralUnion<'running' | 'finished' | 'error'>;
  progress?: number | null;
  phase?: string | null;
  log?: Array<{ text: string; type?: string | null }> | null;
}

export interface RepairStatus {
  idle?: boolean;
  running?: boolean;
  paused?: boolean;
  enabled?: boolean;
  yield_reason?: string | null;
  findings_pending?: number | null;
  current_job?: { display_name?: string | null } | null;
  current_item?: { name?: string | null } | null;
  progress?: {
    current_job?: { scanned?: number; total?: number; percent?: number } | null;
    tracks?: { checked?: number; total?: number; repaired?: number } | null;
  } | null;
}

export interface RepairFinding {
  id: number;
  job_id: string;
  finding_type: string;
  severity: LiteralUnion<RepairSeverity>;
  status: LiteralUnion<RepairFindingStatus>;
  title: string;
  description?: string | null;
  file_path?: string | null;
  entity_type?: string | null;
  entity_id?: string | number | null;
  created_at?: string | null;
  user_action?: string | null;
  /** Per-finding-type payload. Deliberately unknown — every one of the 20
   *  renderers reads a different set of keys. */
  details?: Record<string, unknown> | null;
}

export interface RepairFindingsPage {
  items: RepairFinding[];
  total: number;
  page: number;
}

export interface RepairFindingCounts {
  pending?: number;
  resolved?: number;
  dismissed?: number;
  /** Its own STATUS, not a flavour of resolved — a sum of the other three
   *  misses these rows entirely, which is why `total` exists. */
  auto_fixed?: number;
  total?: number;
  by_job?: Record<
    string,
    { total: number; warning?: number; info?: number; display_name?: string }
  >;
}

export interface BulkFixStatus {
  running?: boolean;
  done?: number;
  total?: number;
  fixed?: number;
  failed?: number;
  stopped?: boolean;
  errors?: Array<{ error: string }> | null;
}

/** The action a fix prompt resolves to. `null` = the user cancelled. */
export type OrphanFixAction = 'staging' | 'delete';
export type DeadFileFixAction = 'redownload' | 'remove';
export type AcoustidFixAction = 'retag' | 'relocate' | 'redownload' | 'delete';
export type QualityFixAction = 'redownload' | 'delete' | 'ignore';
export type BackfillFixAction = 'add_to_wishlist' | 'dismiss';
/** `safe` keeps a field the user set by hand, which is the default everywhere
 *  in Library v2; `overwrite_manual` deliberately hands it back to the
 *  catalogue. Sent as the finding's `fix_action`, per row or per group. */
export type RetagFixAction = 'safe' | 'overwrite_manual';

// ── Cache health ─────────────────────────────────────────────────────────────

export type CacheHealthScore = 'healthy' | 'fair' | 'poor';

export interface CacheHealthStats {
  total_entities: number;
  total_searches: number;
  junk_entities: number;
  stale_mb_nulls: number;
  total_musicbrainz?: number;
  by_source?: Record<string, number>;
  by_type?: Record<string, number>;
  avg_age_days?: number;
  total_access_hits?: number;
  expiring_24h?: number;
  expiring_7d?: number;
}

// ── Tool cards ───────────────────────────────────────────────────────────────

export interface DatabaseStats {
  artists: number;
  albums: number;
  tracks: number;
  database_size_mb: number;
  last_full_refresh?: string | null;
  /** Drives the "<Plex|Jellyfin|Navidrome> Database Updater" card title. */
  server_source?: string | null;
}

export type DbRefreshType = 'incremental' | 'full' | 'deep';

/** Shared shape of the four long-running tool jobs (db update, duplicate
 *  cleaner, reconcile-ids, metadata update). Each endpoint spells its counters
 *  differently, so the per-tool progress types below narrow it. */
export interface ToolRunState {
  status: LiteralUnion<'idle' | 'running' | 'finished' | 'error' | 'completed' | 'stopping'>;
  phase?: string | null;
  progress?: number | null;
  error_message?: string | null;
}

export interface DbUpdateState extends ToolRunState {
  processed?: number;
  total?: number;
}

export interface DuplicateCleanState extends ToolRunState {
  files_scanned?: number;
  total_files?: number;
  duplicates_found?: number;
  deleted?: number;
  space_freed_mb?: number;
}

export interface ReconcileIdsState extends ToolRunState {
  current?: string | null;
  processed?: number;
  total?: number;
  ids_filled?: number;
  entities_updated?: number;
  conflicts?: number;
  unreadable?: number;
}

export interface MetadataUpdateState extends ToolRunState {
  current_artist?: string | null;
  processed?: number;
  total?: number;
  percentage?: number;
  successful?: number;
  failed?: number;
  error?: string | null;
}

export interface MediaScanStatus {
  is_scanning?: boolean;
  status?: string | null;
  progress_message?: string | null;
}

export interface BackupEntry {
  filename: string;
  created: string;
  size_mb: number;
  version?: string | null;
}

export interface BackupListResponse {
  success: boolean;
  count: number;
  db_size_mb: number;
  backups: BackupEntry[];
}

export interface MetadataCacheStats {
  artists?: Record<string, number>;
  albums?: Record<string, number>;
  tracks?: Record<string, number>;
  total_hits?: number;
  musicbrainz_total?: number;
  searches?: number;
}

export interface DiscoveryPoolStats {
  matched?: number;
  failed?: number;
}

/** Which media servers each conditional card is shown for. */
export type ActiveMediaServer = LiteralUnion<'plex' | 'jellyfin' | 'navidrome'>;
