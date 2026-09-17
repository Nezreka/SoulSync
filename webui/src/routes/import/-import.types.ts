import { z } from 'zod';

export const IMPORT_AUTO_FILTER_VALUES = ['all', 'pending', 'imported', 'failed'] as const;
export type ImportAutoFilter = (typeof IMPORT_AUTO_FILTER_VALUES)[number];

export const importAutoSearchSchema = z.object({
  autoFilter: z.enum(IMPORT_AUTO_FILTER_VALUES).default('all').catch('all'),
});

export type ImportAutoSearch = z.infer<typeof importAutoSearchSchema>;

/** The inbox's filter pills. "attention" is what needs a person. */
export const IMPORT_INBOX_FILTER_VALUES = ['attention', 'all', 'history'] as const;
export type ImportInboxFilter = (typeof IMPORT_INBOX_FILTER_VALUES)[number];

export const importInboxSearchSchema = z.object({
  filter: z.enum(IMPORT_INBOX_FILTER_VALUES).default('attention').catch('attention'),
});

export type ImportInboxSearch = z.infer<typeof importInboxSearchSchema>;

export type ImportInboxStatus =
  | 'waiting'
  | 'identifying'
  | 'needs_review'
  | 'needs_identify'
  | 'queued'
  | 'importing'
  | 'imported'
  | 'failed'
  | 'dismissed';

export interface ImportInboxFile {
  filename: string;
  full_path: string;
  rel_path: string;
  title: string;
  artist: string;
  album: string;
  track_number?: number | string | null;
  disc_number?: number | string | null;
  extension: string;
  format: string;
  duration_ms: number;
  bitrate: number;
  size: number;
}

export interface ImportInboxMatchLine {
  track_name: string;
  track_number?: number | string | null;
  file: string;
  file_path: string;
  confidence: number;
}

export interface ImportInboxMatch {
  matched_count: number;
  total_tracks: number;
  matches: ImportInboxMatchLine[];
}

/** One staging item: an album folder, a loose-file group, or a single file,
 * with whatever the worker has done about it folded in. */
export interface ImportInboxItem {
  key: string;
  kind: 'album' | 'single';
  name: string;
  artist: string;
  folder_name: string;
  folder_path: string;
  rel_path: string;
  in_staging: boolean;
  files: ImportInboxFile[];
  file_count: number;
  total_duration_ms: number;
  total_size: number;
  formats: string[];
  status: ImportInboxStatus;
  confidence: number | null;
  image_url?: string | null;
  album_id?: string | null;
  identification_method?: string | null;
  error_message?: string | null;
  match: ImportInboxMatch | null;
  history_id: number | null;
  created_at?: string | null;
  processed_at?: string | null;
  live: { track_index: number; track_total: number; track_name: string } | null;
}

export interface ImportInboxSummary {
  items: number;
  files: number;
  size: number;
  attention: number;
  by_status: Partial<Record<ImportInboxStatus, number>>;
}

export interface ImportInboxWorker {
  available: boolean;
  running: boolean;
  paused: boolean;
  current_status: string;
  last_scan_time?: string | null;
  stats: Record<string, number>;
}

export interface ImportInboxPayload {
  success: boolean;
  error?: string;
  scanning?: boolean;
  progress?: ImportScanProgress;
  staging_path?: string;
  items?: ImportInboxItem[];
  summary?: ImportInboxSummary;
  problems?: ImportStagingProblem[];
  worker?: ImportInboxWorker;
}

export interface ImportStagingFile {
  filename: string;
  rel_path?: string;
  full_path: string;
  title?: string | null;
  artist?: string | null;
  album?: string | null;
  track_number?: string | number | null;
  disc_number?: string | number | null;
  extension?: string | null;
  size?: number | null;
  duration_ms?: number | null;
  bitrate?: number | null;
  manual_match?: ImportTrackResult;
}

/** While a large staging folder is still being scanned in the background (#947), the
 * staging endpoints return `scanning: true` + progress instead of files/groups; the query
 * polls until the scan completes and real data arrives. */
export interface ImportScanProgress {
  scanned: number;
  total: number;
}

/** A folder the scan could not list: files under it are invisible, not absent. */
export interface ImportStagingProblem {
  path: string;
  error: string;
}

export interface ImportStagingFilesPayload {
  success: boolean;
  files?: ImportStagingFile[];
  staging_path?: string;
  error?: string;
  scanning?: boolean;
  progress?: ImportScanProgress;
  problems?: ImportStagingProblem[];
}

export interface ImportStagingGroup {
  album: string;
  artist: string;
  file_count: number;
  files?: Array<{
    filename: string;
    full_path: string;
    title?: string | null;
    track_number?: string | number | null;
  }>;
  file_paths: string[];
}

export interface ImportStagingGroupsPayload {
  success: boolean;
  groups?: ImportStagingGroup[];
  error?: string;
  scanning?: boolean;
  progress?: ImportScanProgress;
}

export interface ImportAlbumResult {
  id: string;
  name: string;
  artist: string;
  /** Provider that returned this result row. */
  source: string;
  image_url?: string | null;
  total_tracks?: number | null;
  release_date?: string | null;
  format?: string | null;
  country?: string | null;
  disambiguation?: string | null;
  status?: string | null;
  label?: string | null;
}

export interface ImportAlbumSearchPayload {
  success: boolean;
  albums?: ImportAlbumResult[];
  suggestions?: ImportAlbumResult[];
  /** Provider used to seed the lookup chain for this response. */
  primary_source?: string | null;
  /** Explicit source the caller picked, if any (echoed back). */
  source_override?: string | null;
  ready?: boolean;
  error?: string;
}

export interface ImportSearchSource {
  source: string;
  label: string;
  active: boolean;
}

export interface ImportSearchSourcesPayload {
  success: boolean;
  sources?: ImportSearchSource[];
  error?: string;
}

export interface ImportTrackResult {
  id: string;
  name: string;
  artist: string;
  album?: string | null;
  /** Provider that returned this result row. */
  source: string;
  image_url?: string | null;
  duration_ms?: number | null;
}

export interface ImportTrackSearchPayload {
  success: boolean;
  tracks?: ImportTrackResult[];
  /** Provider used to seed the lookup chain for this response. */
  primary_source?: string | null;
  error?: string;
}

export interface ImportAlbum {
  id?: string | number | null;
  name: string;
  artist: string;
  /** Provider used to resolve this selected album. */
  source: string;
  image_url?: string | null;
  total_tracks?: number | null;
  release_date?: string | null;
  format?: string | null;
  country?: string | null;
  disambiguation?: string | null;
  status?: string | null;
  label?: string | null;
}

export interface ImportAlbumTrack {
  id?: string | number | null;
  name?: string | null;
  title?: string | null;
  track_number?: string | number | null;
  trackNumber?: string | number | null;
  disc_number?: number | null;
  duration_ms?: number | null;
}

export interface ImportAlbumMatch {
  track?: ImportAlbumTrack | null;
  spotify_track?: ImportAlbumTrack | null;
  staging_file?: ImportStagingFile | null;
  confidence: number;
}

export interface ImportAlbumMatchPayload {
  success: boolean;
  album?: ImportAlbum;
  matches?: ImportAlbumMatch[];
  error?: string;
}

export interface ImportProcessPayload {
  success: boolean;
  processed?: number;
  total?: number;
  errors?: string[];
  error?: string;
}

export interface ImportAutoImportActiveItem {
  folder_hash?: string | null;
  folder_name?: string | null;
  status?: string | null;
  track_index?: number | null;
  track_total?: number | null;
  track_name?: string | null;
}

export interface ImportAutoImportStatusPayload {
  success: boolean;
  running?: boolean;
  paused?: boolean;
  current_status?: string | null;
  last_scan_time?: string | null;
  active_imports?: ImportAutoImportActiveItem[];
  stats?: {
    scanned?: number;
    auto_processed?: number;
    pending_review?: number;
    failed?: number;
  };
  error?: string;
}

export interface ImportAutoImportSettingsPayload {
  success: boolean;
  enabled?: boolean;
  scan_interval?: number;
  confidence_threshold?: number;
  auto_process?: boolean;
  // Per-context quality-profile override — null/undefined means "use the
  // app-wide default profile" (Settings -> Quality), same as every other
  // context that doesn't specify its own.
  quality_profile_id?: number | null;
  error?: string;
}

// Minimal shape from GET /api/quality-profile/custom.
export interface AutoImportQualityProfile {
  id: number;
  name: string;
  is_default: boolean;
}

export interface QualityProfilesPayload {
  success: boolean;
  profiles?: AutoImportQualityProfile[];
  error?: string;
}

export interface ImportAutoImportMatchData {
  matched_count?: number;
  total_tracks?: number;
  matches?: Array<{
    track_name?: string | null;
    track?: { name?: string | null };
    file?: string | null;
    confidence?: number | null;
    import_status?: 'completed' | 'failed' | null;
    import_error?: string | null;
  }>;
}

export interface ImportAutoImportResult {
  id: number;
  status: string;
  folder_hash?: string | null;
  folder_name: string;
  album_name?: string | null;
  artist_name?: string | null;
  image_url?: string | null;
  confidence?: number | null;
  total_files?: number | null;
  identification_method?: string | null;
  match_data?: string | ImportAutoImportMatchData | null;
  error_message?: string | null;
  created_at?: string | null;
}

export interface ImportAutoImportResultsPayload {
  success: boolean;
  results?: ImportAutoImportResult[];
  error?: string;
}

export type ImportQueueStatus = 'running' | 'done' | 'error';
export type ImportQueueJobType = 'album' | 'singles';

export interface ImportQueueEntry {
  id: number;
  type: ImportQueueJobType;
  label: string;
  sublabel: string;
  imageUrl?: string | null;
  status: ImportQueueStatus;
  processed: number;
  total: number;
  errors: string[];
  /** Set when the backend refused to process because the active media server
   * isn't connected (see `is_active_media_server_ready` in core/imports/side_effects.py).
   * The queue item renders a Settings link instead of the plain error list. */
  blockedByMediaServer?: boolean;
}

export interface ImportAlbumQueueJob {
  type: 'album';
  label: string;
  sublabel: string;
  imageUrl?: string | null;
  items: ImportAlbumMatch[];
  albumData: ImportAlbum;
  /** the auto-import row this job resolves, when it came from the inbox */
  historyId?: number | null;
}

export interface ImportSinglesQueueJob {
  type: 'singles';
  label: string;
  sublabel: string;
  imageUrl?: string | null;
  items: ImportStagingFile[];
  historyId?: number | null;
}

export type ImportQueueJob = ImportAlbumQueueJob | ImportSinglesQueueJob;

/** One track of the pre-import preview: where it lands and which tags change. */
export interface ImportPreviewTags {
  title: string;
  artist: string;
  albumartist: string;
  album: string;
  track_number: number | string | null;
  disc_number: number | string | null;
  year: string;
}

export interface ImportPreviewTrack {
  file: string;
  full_path: string;
  destination: string | null;
  path_error: string | null;
  before: ImportPreviewTags;
  after: ImportPreviewTags;
  changed: (keyof ImportPreviewTags)[];
}

export interface ImportPreviewPayload {
  success: boolean;
  tracks?: ImportPreviewTrack[];
  error?: string;
}

/** /api/library/check-tracks: per track name, whether the library has it. */
export interface LibraryOwnedEntry {
  owned: boolean;
  track_id?: number | null;
  title?: string | null;
  file_path?: string | null;
  format?: string | null;
  bitrate?: number | null;
  album?: string | null;
}

export interface LibraryCheckPayload {
  success: boolean;
  owned_tracks?: Record<string, LibraryOwnedEntry>;
  error?: string;
}

export interface ImportUploadChunkPayload {
  success: boolean;
  error?: string;
  received?: number;
  total?: number;
  saved?: { file: string; size: number };
}

export interface ImportUploadPayload {
  success: boolean;
  saved?: { file: string; size: number }[];
  skipped?: { file: string; reason: string }[];
  staging_path?: string;
  error?: string;
}

export interface ImportFingerprintResult {
  file: string;
  status: string;
  error?: string | null;
  title?: string | null;
  artist?: string | null;
  mbid?: string | null;
  score?: number | null;
}

export interface ImportFingerprintPayload {
  success: boolean;
  error?: string;
  code?: string;
  results?: ImportFingerprintResult[];
  recognised?: number;
  artist?: string | null;
  title?: string | null;
}
