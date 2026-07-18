import { z } from 'zod';

// Single route `/library-v2` with the current view driven by search params:
//   - artist set -> artist detail (albums expand inline)
//   - album set  -> directly addressable album detail
//   - neither    -> artist overview
// Keeping it to one route (vs nested file routes) keeps the TanStack route tree
// small and avoids codegen surprises while still giving the full drill-down UX.
export const LIBRARY_V2_SORTS = ['name', 'added', 'albums', 'tracks'] as const;
export type LibraryV2Sort = (typeof LIBRARY_V2_SORTS)[number];

export const LIBRARY_V2_MONITOR_FILTERS = ['all', 'monitored', 'unmonitored'] as const;
export type LibraryV2MonitorFilter = (typeof LIBRARY_V2_MONITOR_FILTERS)[number];

export const libraryV2SearchSchema = z.object({
  section: z.enum(['artists', 'playlists']).default('artists').catch('artists'),
  q: z.string().default('').catch(''),
  sort: z.enum(LIBRARY_V2_SORTS).default('name').catch('name'),
  view: z.enum(['table', 'cards']).default('cards').catch('cards'),
  monitored: z.enum(LIBRARY_V2_MONITOR_FILTERS).default('all').catch('all'),
  page: z.coerce.number().int().positive().default(1).catch(1),
  artist: z.coerce.number().int().positive().optional().catch(undefined),
  album: z.coerce.number().int().positive().optional().catch(undefined),
  playlist: z.coerce.number().int().positive().optional().catch(undefined),
  /** Artist detail: show only owned releases or the full provider discography. */
  releases: z.enum(['library', 'all']).default('library').catch('library'),
});

export type LibraryV2Search = z.infer<typeof libraryV2SearchSchema>;

/** 'library' = imported from files; 'discography' = provider-only release.
 *  `(string & {})` keeps unknown future server values assignable without
 *  collapsing the union (would trip no-redundant-type-constituents). */
export type LibraryV2AlbumOrigin = 'library' | 'discography' | (string & {});

/** `until_top` is the persisted compatibility alias for `until_cutoff` with
 *  `upgrade_cutoff_index = 0`; it remains a first-class API read value. */
export type LibraryV2UpgradePolicy = 'acceptable' | 'until_cutoff' | 'until_top' | (string & {});
export type LibraryV2QualityProfileSource =
  | 'track'
  | 'album'
  | 'artist'
  | 'playlist'
  | 'global'
  | (string & {});

export interface LibraryV2Pagination {
  page: number;
  limit: number;
  total_count: number;
  total_pages: number;
  has_prev: boolean;
  has_next: boolean;
}

export interface LibraryV2ArtistSummary {
  id: number;
  name: string;
  image_url: string | null;
  genres: string[];
  monitored: boolean;
  monitor_new_items: string;
  quality_profile_id: number;
  quality_profile_source?: LibraryV2QualityProfileSource;
  quality_profile_source_id?: number | null;
  quality_profile_explicit?: boolean;
  added_at: string | null;
  album_count: number;
  single_count: number;
  track_count: number;
  tracks_present: number;
  tracks_missing: number;
  user_overrides: Record<string, unknown>;
}

export interface LibraryV2AlbumSummary {
  id: number;
  title: string;
  album_type: string;
  release_date: string | null;
  year: number | null;
  image_url: string | null;
  monitored: boolean;
  quality_profile_id: number;
  quality_profile_source?: LibraryV2QualityProfileSource;
  quality_profile_source_id?: number | null;
  quality_profile_explicit?: boolean;
  origin: LibraryV2AlbumOrigin;
  spotify_id: string | null;
  /** §48 rich-metadata-edit fields — provider baseline overlaid with any admin override. */
  explicit: boolean | null;
  label: string | null;
  style: string | null;
  mood: string | null;
  track_count: number;
  tracks_present: number;
  tracks_missing: number;
  user_overrides: Record<string, unknown>;
}

export interface LibraryV2ArtistDetail {
  id: number;
  name: string;
  image_url: string | null;
  summary: string | null;
  /** §48 rich-metadata-edit fields — provider baseline overlaid with any admin override. */
  style: string | null;
  mood: string | null;
  label: string | null;
  genres: string[];
  monitored: boolean;
  monitor_new_items: string;
  quality_profile: LibraryV2QualityProfile | null;
  quality_profile_source?: LibraryV2QualityProfileSource;
  quality_profile_source_id?: number | null;
  quality_profile_explicit?: boolean;
  albums: LibraryV2AlbumSummary[];
  eps: LibraryV2AlbumSummary[];
  singles: LibraryV2AlbumSummary[];
  album_count: number;
  single_count: number;
  /** Provider-only releases currently persisted for this artist. */
  discography_count: number;
  user_overrides: Record<string, unknown>;
}

/** §52.3: one settings surface backed by the existing admin Watchlist row. */
export interface LibraryV2ArtistSettings {
  artist_id: number;
  watchlist_row_id: number;
  watchlist_name: string;
  watchlist_image_url: string | null;
  provider_ids: Partial<
    Record<'spotify' | 'itunes' | 'deezer' | 'discogs' | 'amazon' | 'musicbrainz', string | null>
  >;
  monitor_new_items: 'all' | 'new' | 'none';
  include_albums: boolean;
  include_eps: boolean;
  include_singles: boolean;
  include_live: boolean;
  include_remixes: boolean;
  include_acoustic: boolean;
  include_compilations: boolean;
  include_instrumentals: boolean;
  auto_download: boolean;
  lookback_days: number | null;
  preferred_metadata_source: string | null;
}

/** One member of an artist's §40 alias group (docs/library-v2.md §24) — the
 *  same real artist under a different, unlinked provider identity. */
export interface LibraryV2ArtistAliasMember {
  id: number;
  name: string;
  image_url: string | null;
}

export interface LibraryV2TrackArtist {
  id: number;
  name: string;
  role: string;
}

export interface LibraryV2TrackFile {
  /** lib2_track_files row id — used to scope ADR-05 file-delete to a caller
   *  selection (B6 bulk delete from the track table). */
  file_id: number;
  path: string;
  format: string | null;
  bitrate: number | null;
  sample_rate: number | null;
  bit_depth: number | null;
  size: number | null;
  quality_tier: string;
  import_status: string | null;
  verification_status: string | null;
  /** Deep-dive A7/C4: raw AcoustID outcome ('pass'|'skip'|null) — narrower
   *  than verification_status, populated by the autolink import callback. */
  acoustid_status?: string | null;
  /** Compact pipeline detail with no dedicated column (AcoustID reason,
   *  quality-profile fallback applied). Always an object, `{}` when empty. */
  pipeline_result?: LibraryV2PipelineResult;
  source: string | null;
  file_state: string | null;
  has_replaygain?: boolean;
  has_lyrics?: boolean;
}

/** Deep-dive A7/C4: what `autolink.py` stashes per file beyond the dedicated
 *  status columns — all keys optional, present only when they apply. */
export interface LibraryV2PipelineResult {
  acoustid_message?: string;
  version_mismatch_fallback?: string;
  quality_fallback?: string[];
}

/** One provider's match state for an entity (legacy Enhanced-View match chips). */
export interface LibraryV2MatchService {
  service: string;
  label: string;
  /** 'matched' | 'not_found' | 'pending' (kept as string for forward-compat). */
  status: string;
  external_id: string | null;
  last_attempted: string | null;
  /** The legacy row id — media servers may use numeric or opaque TEXT ids. */
  legacy_entity_id: number | string | null;
  /** Always present on current servers; enables matching lib2-native rows
   * that have no legacy back-reference. */
  library_v2_entity_id?: number | null;
  /** Is this provider configured/usable on this instance right now (A8)?
   *  Always ``true`` when the server has no availability signal (older
   *  cached response shape). */
  available?: boolean;
  /** How the current provider id was chosen. Pre-feature rows are `legacy`
   * because automatic vs manual cannot be reconstructed safely. */
  match_origin?: 'automatic' | 'manual' | 'legacy' | null;
  matched_at?: string | null;
}

/** One candidate cover-art image for the art picker (docs §49). */
export interface LibraryV2ArtCandidate {
  url: string;
  source: string;
  type?: string;
  front?: boolean;
}

/** One reorganize metadata source option (docs §50). */
export interface LibraryV2ReorganizeSource {
  source: string;
  label: string;
}

/** One per-track row of a reorganize preview plan. */
export interface LibraryV2ReorganizeTrackPreview {
  track_id: number | null;
  title: string;
  track_number: number | null;
  disc_number: number | null;
  current_path: string | null;
  new_path: string | null;
  file_exists: boolean;
  unchanged: boolean;
  collision: boolean;
  matched: boolean;
  reason: string | null;
}

/** `POST .../reorganize/preview` response (docs §50). */
export interface LibraryV2ReorganizePreview {
  success: boolean;
  /** 'planned' | 'no_source_id' — a failed 'no_album'/'no_tracks' surfaces as a thrown error instead. */
  status: string;
  source: string | null;
  album: string;
  artist: string;
  transfer_dir: string;
  tracks: LibraryV2ReorganizeTrackPreview[];
}

/** One item in the (legacy, shared) reorganize queue — polled by the lib2
 *  Reorganize modals so a queued/running move has a face (deep-dive G7). */
export interface LibraryV2ReorganizeQueueItem {
  queueId: string;
  albumId: string;
  albumTitle: string;
  artistName: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  resultStatus: string | null;
  currentTrack: string | null;
  progressTotal: number;
  progressProcessed: number;
  finishedAt: number | null;
}

/** `GET /api/library/reorganize/queue` snapshot (docs G7). */
export interface LibraryV2ReorganizeQueueSnapshot {
  active: LibraryV2ReorganizeQueueItem | null;
  queued: LibraryV2ReorganizeQueueItem[];
  recent: LibraryV2ReorganizeQueueItem[];
}

/** One row from the legacy `track_downloads` provenance table (Source Info popover). */
export interface LibraryV2TrackDownload {
  id: number;
  source_service: string | null;
  source_username: string | null;
  source_filename: string | null;
  source_size: number | null;
  audio_quality: string | null;
  bitrate: number | null;
  sample_rate: number | null;
  bit_depth: number | null;
  status: string | null;
  track_title: string | null;
  track_artist: string | null;
  created_at: string | null;
}

/** One `lib2_manual_skips` audit row — a user-approved check override
 *  (e.g. AcoustID or quality skipped) bound to this track's file. */
export interface LibraryV2ManualSkip {
  id: number;
  skipped_checks: string[];
  reason: string | null;
  acknowledged: boolean;
  created_at: string | null;
}

/** Live embedded tags read straight from the file via mutagen (§18.1),
 *  backing the Track Detail modal's Tags + Lyrics tabs. `tags` is a flat
 *  lowercase-key → string map (matches `core.library.file_tags`). */
export interface LibraryV2FileTags {
  available: boolean;
  reason?: string;
  format?: string;
  bitrate?: number;
  duration?: number;
  has_picture?: boolean;
  tags?: Record<string, string>;
}

export interface LibraryV2Track {
  /** null for a "missing" placeholder row (an expected track we don't have yet). */
  id: number | null;
  title: string | null;
  track_number: number | null;
  disc_number: number | null;
  duration: number | null;
  bpm: number | null;
  /** §48 rich-metadata-edit fields — provider baseline overlaid with any admin override. */
  explicit: boolean | null;
  style: string | null;
  mood: string | null;
  isrc: string | null;
  monitored: boolean;
  quality_profile_id: number;
  quality_profile_source?: LibraryV2QualityProfileSource;
  quality_profile_source_id?: number | null;
  quality_profile_explicit?: boolean;
  canonical_track_id: number | null;
  artists: LibraryV2TrackArtist[];
  file: LibraryV2TrackFile | null;
  file_status: 'present' | 'missing' | 'duplicate_single';
  metadata_gaps: string[];
  is_missing?: boolean;
  /** Quality vs the album's profile (null when missing or not measurable). */
  meets_profile?: boolean | null;
  upgrade_candidate?: boolean | null;
  /** Field-level admin corrections applied over provider metadata. */
  user_overrides?: Record<string, unknown>;
}

export interface LibraryV2AlbumDetail {
  id: number;
  title: string;
  album_type: string;
  release_date: string | null;
  year: number | null;
  image_url: string | null;
  genres: string[];
  /** §48 rich-metadata-edit fields — provider baseline overlaid with any admin override. */
  explicit: boolean | null;
  label: string | null;
  style: string | null;
  mood: string | null;
  monitored: boolean;
  origin: LibraryV2AlbumOrigin;
  quality_profile: LibraryV2QualityProfile | null;
  quality_profile_source?: LibraryV2QualityProfileSource;
  quality_profile_source_id?: number | null;
  quality_profile_explicit?: boolean;
  primary_artist: { id: number; name: string } | null;
  tracks: LibraryV2Track[];
  track_count: number;
  tracks_present: number;
  tracks_missing: number;
  upgrades_available?: number;
  user_overrides: Record<string, unknown>;
}

export interface LibraryV2RankedTarget {
  label?: string;
  format?: string;
  bit_depth?: number;
  min_sample_rate?: number;
  min_bitrate?: number;
}

export interface LibraryV2QualityProfile {
  id: number;
  name: string;
  description: string | null;
  upgrade_policy: LibraryV2UpgradePolicy;
  /** For `until_cutoff`: target that counts as done. `until_top` always uses 0. */
  upgrade_cutoff_index: number;
  ranked_targets: LibraryV2RankedTarget[];
  repair_job_id: string;
  repair_settings: Record<string, unknown>;
  is_default: boolean;
}

export interface LibraryV2ImportState {
  running: boolean;
  stage: string | null;
  current: number;
  total: number;
  stats: Record<string, number> | null;
  error: string | null;
  finished_at: number | null;
  artwork_cache: LibraryV2ArtworkCacheState;
}

export interface LibraryV2ArtworkCacheState {
  running: boolean;
  current: number;
  total: number;
  stats: Record<string, number> | null;
  error: string | null;
  started_at: number | null;
  finished_at: number | null;
}

export interface LibraryV2JobState {
  job_id: string | null;
  running: boolean;
  kind: string | null;
  current: number;
  total: number;
  result: Record<string, number | string | null> | null;
  error: string | null;
  started_at: number | null;
  finished_at: number | null;
  jobs?: LibraryV2JobState[];
}

export interface LibraryV2DiscographyStats {
  added: number;
  enriched: number;
  removed: number;
  total: number;
  source: string | null;
}

export interface LibraryV2PlaylistPipelineState {
  run_id: string;
  playlist_id: number;
  status: 'idle' | 'running' | 'finished' | 'error' | 'skipped' | (string & {});
  progress: number;
  phase: string;
  error?: string | null;
}

export interface LibraryV2PlaylistSummary {
  id: number;
  source: string;
  source_playlist_id: string;
  name: string;
  display_name: string;
  description: string | null;
  owner: string | null;
  image_url: string | null;
  track_count: number;
  total_count: number;
  discovered_count: number;
  wishlisted_count: number;
  in_library_count: number;
  updated_at: string | null;
  pipeline_state: LibraryV2PlaylistPipelineState | null;
}

export interface LibraryV2PlaylistTrack {
  id: number;
  position: number;
  track_name: string;
  artist_name: string;
  album_name: string;
  duration_ms: number;
  image_url: string | null;
  source_track_id: string | null;
  extra_data: string | null;
}

export interface LibraryV2PlaylistDetail extends LibraryV2PlaylistSummary {
  tracks: LibraryV2PlaylistTrack[];
}

/** B5: which optional track-table columns are shown. #/Title/Actions are
 *  always shown and have no entry here. */
export interface LibraryV2TrackTableColumns {
  disc: boolean;
  artists: boolean;
  duration: boolean;
  bpm: boolean;
  match: boolean;
  quality: boolean;
  features: boolean;
  metadata: boolean;
  file_path: boolean;
  /** H1: row play button, reuses the Legacy player via the shell bridge. */
  play: boolean;
}

/** Round 5 (deep-dive D6): which optional artist-overview table columns are
 *  shown. Only meaningful in table view — the card grid has its own fixed
 *  layout. Mon./Artist/Albums/Singles/Tracks/Missing are always shown. */
export interface LibraryV2ArtistTableColumns {
  quality_profile: boolean;
  genres: boolean;
  added: boolean;
}

export interface LibraryV2UiPreferences {
  track_table: {
    columns: LibraryV2TrackTableColumns;
    column_order: (keyof LibraryV2TrackTableColumns)[];
    show_all_match_providers: boolean;
    visible_match_providers: Record<string, boolean>;
    quality_show_format: boolean;
    quality_show_resolution: boolean;
    quality_show_bitrate: boolean;
  };
  artist_table: {
    columns: LibraryV2ArtistTableColumns;
    column_order: (keyof LibraryV2ArtistTableColumns)[];
  };
}
