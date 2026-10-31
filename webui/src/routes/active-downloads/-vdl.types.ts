/**
 * Types for the unified downloads page's VIDEO view.
 *
 * The video side's rows come from `/api/video/downloads/*` (the
 * `api/video/downloads.py` blueprint, deliberately isolated from the music
 * DB). The shape below mirrors the `video_downloads` table rows the old
 * vanilla page consumed — same fields, same names — so the React rewrite is
 * a port, not a reinterpretation.
 */

/** One row from GET /api/video/downloads/active. */
export interface VdlDownload {
  id: number;
  kind?: string | null;
  title?: string | null;
  year?: number | string | null;
  status?: string | null;
  /** 0..100 download progress. */
  progress?: number | null;
  /** 'merging' | 'moving' while status === 'importing'. */
  import_phase?: string | null;
  /** 0..100 cross-drive copy progress while moving. */
  import_progress?: number | null;
  poster_url?: string | null;
  quality_label?: string | null;
  /** completed-but-below-cutoff: still hunting a better copy. */
  upgrade_watch?: boolean | null;
  release_title?: string | null;
  size_bytes?: number | null;
  speed_bps?: number | null;
  eta_seconds?: number | null;
  username?: string | null;
  dest_path?: string | null;
  error?: string | null;
  attempts?: number | null;
  /** TMDB id (or YouTube video id) — null for owned re-grabs without one. */
  media_id?: number | string | null;
  media_source?: string | null;
  /** peer/season/episode/channel/… — a JSON string or an already-parsed object. */
  search_ctx?: string | Record<string, unknown> | null;
  resolution?: string | null;
  /** slskd | torrent | usenet | youtube … */
  source?: string | null;
  codec?: string | null;
  filename?: string | null;
  /** true when this row IS the season pack (the batch head). */
  is_pack?: boolean | null;
  /** utc "YYYY-MM-DD HH:MM:SS" stamps off the row. */
  created_at?: string | null;
  completed_at?: string | null;
  /** when progress last moved (the stall clock). */
  progress_at?: string | null;
  /** JSON list of backup releases left to fall back on. */
  candidates?: string | unknown[] | null;
  /** JSON list of release files already tried. */
  tried_files?: string | unknown[] | null;
}

/** Lazy TMDB detail for an expanded card (GET /api/video/downloads/meta/…). */
export interface VdlMeta {
  title?: string | null;
  overview?: string | null;
  tagline?: string | null;
  backdrop_url?: string | null;
  logo?: string | null;
  genres?: string[] | null;
  rating?: number | null;
  runtime_minutes?: number | null;
  year?: number | string | null;
  network?: string | null;
  studio?: string | null;
  status?: string | null;
  director?: string | null;
  episode?: {
    season: number;
    episode: number;
    title?: string | null;
    overview?: string | null;
    air_date?: string | null;
    still_url?: string | null;
  } | null;
  cast?: { name?: string | null; character?: string | null; photo?: string | null }[] | null;
  trailer_url?: string | null;
  providers?: { name?: string | null; logo?: string | null }[] | null;
}

/** Cached YouTube detail (GET /api/video/downloads/yt-meta/<id>). */
export interface VdlYtMeta {
  title?: string | null;
  thumbnail_url?: string | null;
  duration?: string | null;
  view_count?: number | null;
  channel_id?: string | null;
}

/** The download's search_ctx, parsed. */
export interface VdlSearchCtx {
  season?: number | null;
  episode?: number | null;
  scope?: string | null;
  peer?: { slots?: number | null; queue?: number | null; speed?: number | null } | null;
  channel?: string | null;
  channel_title?: string | null;
  channel_id?: string | null;
  published_at?: string | null;
  description?: string | null;
}

/* ── filters ───────────────────────────────────────────────────────────── */

export const VDL_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'active', label: 'Active' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
] as const;

export type VdlFilter = (typeof VDL_FILTERS)[number]['value'];

/** The three top-level views. 'downloads' | 'review' | 'clients' — same split as music. */
export type VdlView = 'downloads' | 'review' | 'clients';

/** 'recycle' | 'blocklist' sub-tabs inside the review pane. */
export type VdlReviewSub = 'recycle' | 'blocklist';

/** Active per the old page: downloading, queued, searching, importing. */
export const VDL_ACTIVE_STATUSES = ['downloading', 'queued', 'searching', 'importing'] as const;
/** Failed per the old page: failed, cancelled, import_failed. */
export const VDL_FAILED_STATUSES = ['failed', 'cancelled', 'import_failed'] as const;
/**
 * Failed rows that can come off the list without a retry. import_failed stays:
 * it is Manual import's only pointer to a file still in the download area.
 */
export const VDL_REMOVABLE_STATUSES = ['failed', 'cancelled'] as const;

/** The old page's cadence: 2s while anything is active, 6s when idle. */
export const VDL_POLL_ACTIVE_MS = 2000;
export const VDL_POLL_IDLE_MS = 6000;

/* ── review pane ───────────────────────────────────────────────────────── */

export interface VdlRecycleEntry {
  name: string;
  trash_dir?: string | null;
  original_path?: string | null;
  reason?: string | null;
  size?: number | null;
  age_seconds?: number | null;
}

export interface VdlRecycleBin {
  items: VdlRecycleEntry[];
  recycle_enabled?: boolean | null;
  keep_days?: number | null;
}

export interface VdlBlockEntry {
  id: number | string;
  title?: string | null;
  release_title?: string | null;
  filename?: string | null;
  username?: string | null;
  reason?: string | null;
  season_number?: number | null;
  episode_number?: number | null;
}

/* ── history modal ─────────────────────────────────────────────────────── */

export type VdlHistoryTab = 'all' | 'movie' | 'show' | 'youtube';

export interface VdlHistoryItem {
  id: number | string;
  kind?: string | null;
  title?: string | null;
  release_title?: string | null;
  outcome?: string | null;
  season_number?: number | null;
  episode_number?: number | null;
  year?: number | string | null;
  quality_label?: string | null;
  resolution?: string | null;
  video_codec?: string | null;
  size_bytes?: number | null;
  poster_url?: string | null;
  source?: string | null;
  username?: string | null;
  filename?: string | null;
  dest_path?: string | null;
  error?: string | null;
  grabbed_at?: string | null;
  completed_at?: string | null;
}

export interface VdlHistoryCounts {
  movie?: number;
  show?: number;
  youtube?: number;
  total?: number;
}
