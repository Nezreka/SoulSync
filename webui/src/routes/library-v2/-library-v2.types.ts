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
  origin: LibraryV2AlbumOrigin;
  spotify_id: string | null;
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
  genres: string[];
  monitored: boolean;
  monitor_new_items: string;
  quality_profile: LibraryV2QualityProfile | null;
  albums: LibraryV2AlbumSummary[];
  eps: LibraryV2AlbumSummary[];
  singles: LibraryV2AlbumSummary[];
  album_count: number;
  single_count: number;
  /** Provider-only releases currently persisted for this artist. */
  discography_count: number;
  user_overrides: Record<string, unknown>;
}

export interface LibraryV2TrackArtist {
  id: number;
  name: string;
  role: string;
}

export interface LibraryV2TrackFile {
  path: string;
  format: string | null;
  bitrate: number | null;
  sample_rate: number | null;
  bit_depth: number | null;
  size: number | null;
  quality_tier: string;
  import_status: string | null;
  verification_status: string | null;
  source: string | null;
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

export interface LibraryV2Track {
  /** null for a "missing" placeholder row (an expected track we don't have yet). */
  id: number | null;
  title: string | null;
  track_number: number | null;
  disc_number: number | null;
  duration: number | null;
  isrc: string | null;
  monitored: boolean;
  quality_profile_id: number;
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
  monitored: boolean;
  origin: LibraryV2AlbumOrigin;
  quality_profile: LibraryV2QualityProfile | null;
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
}

export interface LibraryV2JobState {
  job_id: string | null;
  running: boolean;
  kind: string | null;
  current: number;
  total: number;
  result: Record<string, number> | null;
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
