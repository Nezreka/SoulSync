/**
 * Active-downloads page shapes.
 *
 * Every field is traced to its producer rather than inferred from how the
 * vanilla read it:
 *   - download rows   → core/downloads/status.py, and there are TWO shapes
 *   - batch summaries → build_unified_downloads_response
 *   - album bundle    → _build_album_bundle_status
 *   - quarantine      → core/imports/quarantine.py list_quarantine_entries
 *   - config          → web_server.py get_verification_config
 */

// ── Downloads ─────────────────────────────────────────────────────────────

/**
 * A download row.
 *
 * The server emits this from two different builders and they do NOT carry the
 * same fields — the union is real, not defensive typing:
 *
 *   LIVE task rows  have history_id, retry_info, retry_trigger, progress,
 *                   error, batch_*, playlist_id, track_index, batch_total;
 *                   they have NO created_at and NO file_path.
 *   HISTORY rows    (is_persistent_history: true) have created_at and
 *                   file_path; they have NO history_id, retry_info or
 *                   retry_trigger, and their task_id is `history-<id>`.
 *
 * That split is exactly why verifHistoryId has two branches, and why the
 * unverified detail panel shows "File"/"Downloaded" for some rows and not
 * others. Typing them as one always-present shape would hide that.
 */
export interface AdlDownload {
  task_id: string;
  title: string;
  artist: string;
  album: string;
  artwork: string;
  status: string;
  progress: number;
  error: string | null;
  verification_status: string | null;
  batch_id: string;
  batch_name: string;
  batch_source: string;
  playlist_id: string;
  track_index: number;
  batch_total: number;
  timestamp: number;
  priority: number;
  quality: string;
  is_persistent_history: boolean;

  /** Live rows only — the library_history id, set at import. */
  history_id?: string | number | null;
  /** Live rows only — retry-engine attempt info. */
  retry_info?: string | number | null;
  retry_trigger?: string | null;
  /** History rows only. */
  created_at?: string;
  file_path?: string;
}

/** One live batch. `album_bundle` is present only for release downloads. */
export interface AdlBatch {
  batch_id: string;
  playlist_id: string;
  batch_name: string;
  source_page: string;
  phase: string;
  total: number;
  completed: number;
  failed: number;
  active: number;
  queued: number;
  album_bundle?: AdlAlbumBundle;
}

/**
 * Release-download progress.
 *
 * EVERY field is optional: the builder ends with a comprehension that strips
 * every key whose value is None, so a bundle mid-search carries almost nothing.
 */
export interface AdlAlbumBundle {
  state?: string;
  source?: string;
  release?: string;
  progress?: number;
  progress_percent?: number;
  /** Pre-formatted by the downloader (e.g. "1.2 MB/s"), not a byte count. */
  speed?: string;
  downloaded?: string;
  size?: string;
  seeders?: number;
  grabs?: number;
  count?: number;
}

export interface AdlDownloadsResponse {
  success?: boolean;
  downloads?: AdlDownload[];
  batches?: AdlBatch[];
  total?: number;
}

/** One completed batch in the history rail. */
export interface AdlBatchHistoryEntry {
  playlist_name?: string;
  tracks_downloaded?: number;
  tracks_failed?: number;
  total_tracks?: number;
  completed_at?: string;
  source_page?: string;
}

// ── Verification / quarantine ─────────────────────────────────────────────

export interface AdlVerificationConfig {
  acoustid_enabled?: boolean;
  require_verified?: boolean;
}

/** A quarantined file plus its sidecar. */
export interface AdlQuarantineEntry {
  id: string;
  filename: string;
  original_filename: string;
  reason: string;
  expected_track: string;
  expected_artist: string;
  group_key: string;
  timestamp: string;
  size_bytes: number;
  /**
   * Whether the sidecar embedded the full pipeline context.
   *
   * Drives which approve button the row gets: with context it can be
   * re-imported in one click; without (legacy thin sidecar) the only option is
   * Recover-to-Staging.
   */
  has_full_context: boolean;
  trigger: string;
  source_username: string;
  source_filename: string;
  thumb_url: string;
  quality: string;
}

// ── Filters ───────────────────────────────────────────────────────────────

export const ADL_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'active', label: 'Active' },
  { value: 'queued', label: 'Queued' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
] as const;

export type AdlFilter = (typeof ADL_FILTERS)[number]['value'] | 'unverified';

export type AdlSubView = 'unverified' | 'quarantine';

/**
 * Which statuses each FILTER pill accepts.
 *
 * Deliberately separate from the status→class mapping below, because they
 * disagree on one value: `cancelled` is filtered as FAILED but renders with its
 * own `cancelled` class. Deriving the filter from the class would silently drop
 * every cancelled row out of the Failed pill.
 */
export const ADL_FILTER_STATUSES: Record<string, readonly string[]> = {
  active: ['downloading', 'searching', 'post_processing'],
  queued: ['queued'],
  completed: ['completed', 'skipped', 'already_owned'],
  failed: ['failed', 'not_found', 'cancelled'],
};

/** Verification statuses that put a row in the review queue. */
export const ADL_REVIEW_STATUSES: readonly string[] = ['unverified', 'force_imported'];

/**
 * Statuses the bulk verification actions treat as "done".
 *
 * Note this is the COMPLETED set, not the filter's — _verifUnverifiedIds used
 * ['completed','skipped','already_owned'] while the unverified filter pill used
 * the same three. Kept as one constant since they agree.
 */
export const ADL_DONE_STATUSES: readonly string[] = ['completed', 'skipped', 'already_owned'];

/** How long a finished batch card lingers before it disappears (seconds). */
export const BATCH_FADE_SECONDS = 15;

/** Poll intervals, in milliseconds. */
export const ADL_POLL_MS = 2000;
export const ADL_BATCH_HISTORY_POLL_MS = 60000;
/** Quarantine refreshes every 7th downloads poll (~15s). */
export const ADL_QUARANTINE_EVERY_N_POLLS = 7;
