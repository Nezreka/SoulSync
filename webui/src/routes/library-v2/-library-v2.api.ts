import { queryOptions, type QueryClient } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  LibraryV2AlbumDetail,
  LibraryV2ArtistDetail,
  LibraryV2ArtistSummary,
  LibraryV2DiscographyStats,
  LibraryV2ImportState,
  LibraryV2JobState,
  LibraryV2Pagination,
  LibraryV2PlaylistDetail,
  LibraryV2PlaylistPipelineState,
  LibraryV2PlaylistSummary,
  LibraryV2QualityProfile,
  LibraryV2Search,
  LibraryV2Track,
} from './-library-v2.types';

export const LIBRARY_V2_QUERY_KEY = ['library-v2'] as const;

interface EnabledResponse {
  success: boolean;
  enabled: boolean;
}
interface ArtistsResponse {
  success: boolean;
  artists: LibraryV2ArtistSummary[];
  pagination: LibraryV2Pagination;
  error?: string;
}
interface ArtistResponse {
  success: boolean;
  artist?: LibraryV2ArtistDetail;
  error?: string;
}
interface AlbumResponse {
  success: boolean;
  album?: LibraryV2AlbumDetail;
  error?: string;
}
interface TrackResponse {
  success: boolean;
  track?: LibraryV2Track;
  error?: string;
}
interface QualityProfilesResponse {
  success: boolean;
  profiles: LibraryV2QualityProfile[];
  error?: string;
}

export async function fetchLibraryV2Enabled(): Promise<boolean> {
  const payload = await readJson<EnabledResponse>(apiClient.get('library/v2/enabled'));
  return Boolean(payload.enabled);
}

export async function fetchLibraryV2Artists(
  search: Pick<LibraryV2Search, 'q' | 'sort' | 'page' | 'monitored'>,
): Promise<ArtistsResponse> {
  const params = new URLSearchParams();
  if (search.q) params.set('search', search.q);
  params.set('sort', search.sort);
  params.set('monitored', search.monitored);
  params.set('page', String(search.page));
  const payload = await readJson<ArtistsResponse>(
    apiClient.get('library/v2/artists', { searchParams: params }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to load library');
  return payload;
}

export async function setLibraryV2Monitored(
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
  monitored: boolean,
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/${entity}/${id}/monitor`, { json: { monitored } }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to update monitoring');
}

export async function fetchLibraryV2QualityProfiles(): Promise<LibraryV2QualityProfile[]> {
  const payload = await readJson<QualityProfilesResponse>(
    apiClient.get('library/v2/quality-profiles'),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to load quality profiles');
  return payload.profiles;
}

export async function setLibraryV2QualityProfile(
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
  qualityProfileId: number,
  cascade = true,
  monitorExisting = false,
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/${entity}/${id}/quality-profile`, {
      json: {
        quality_profile_id: qualityProfileId,
        cascade,
        // Assigning a profile is a quality decision; monitoring the entity's
        // tracks for upgrades (a wanted-action) is a separate, explicit
        // opt-in (audit P1-15).
        monitor_existing: monitorExisting,
      },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to update quality profile');
}

export async function refreshLibraryV2(entity: 'artists' | 'albums', id: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/${entity}/${id}/refresh`, { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to refresh');
}

export async function fetchLibraryV2Artist(artistId: number): Promise<LibraryV2ArtistDetail> {
  const payload = await readJson<ArtistResponse>(apiClient.get(`library/v2/artists/${artistId}`));
  if (!payload.success || !payload.artist) throw new Error(payload.error || 'Artist not found');
  return payload.artist;
}

export async function fetchLibraryV2Album(
  albumId: number,
  options: { resolve?: boolean } = {},
): Promise<LibraryV2AlbumDetail> {
  const params = new URLSearchParams();
  if (options.resolve) params.set('resolve', '1');
  const payload = await readJson<AlbumResponse>(
    apiClient.get(`library/v2/albums/${albumId}`, { searchParams: params }),
  );
  if (!payload.success || !payload.album) throw new Error(payload.error || 'Album not found');
  return payload.album;
}

export async function refreshLibraryV2Discography(
  artistId: number,
): Promise<LibraryV2DiscographyStats> {
  const payload = await readJson<{ success: boolean; error?: string } & LibraryV2DiscographyStats>(
    apiClient.post(`library/v2/artists/${artistId}/discography/refresh`, {
      json: {},
      timeout: 60_000, // provider discography lookup can take a few seconds
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Discography refresh failed');
  return payload;
}

export async function bulkMonitorLibraryV2Releases(
  artistId: number,
  scope: 'albums' | 'eps' | 'singles' | 'all' | 'missing',
  monitored: boolean,
  albumIds?: number[],
): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post(`library/v2/artists/${artistId}/releases/monitor`, {
      json: { scope, monitored, ...(albumIds ? { album_ids: albumIds } : {}) },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Bulk monitor failed');
  if (!payload.job_id) throw new Error('Bulk monitor did not return a job id');
  return payload.job_id;
}

export async function editLibraryV2Artist(
  artistId: number,
  monitorNewItems: 'all' | 'none' | 'new',
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/artists/${artistId}/edit`, {
      json: { monitor_new_items: monitorNewItems },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Edit failed');
}

export const LIBRARY_V2_ALBUM_TYPES = ['album', 'ep', 'single', 'compilation', 'live'] as const;
export type LibraryV2AlbumType = (typeof LIBRARY_V2_ALBUM_TYPES)[number];

/** Entity names accepted by the central field-level metadata override store. */
export type LibraryV2MetadataEntity =
  | 'artist'
  | 'release_group'
  | 'track'
  | 'release_edition'
  | 'recording';

export async function updateLibraryV2MetadataOverrides(
  entity: LibraryV2MetadataEntity,
  entityId: number,
  values: Record<string, unknown>,
  clear: string[] = [],
): Promise<Record<string, unknown>> {
  const payload = await readJson<{
    success: boolean;
    overrides?: Record<string, unknown>;
    error?: string;
  }>(
    apiClient.patch(`library/v2/metadata-overrides/${entity}/${entityId}`, {
      json: { set: values, clear },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Edit failed');
  return payload.overrides ?? {};
}

/** Trigger the wishlist processor — the real "Search Monitored": every
 *  monitored missing/upgradable track is mirrored to the wishlist, so
 *  processing it searches and downloads them through the normal pipeline. */
export async function processWishlist(): Promise<string> {
  const payload = await readJson<{ success: boolean; message?: string; error?: string }>(
    apiClient.post('wishlist/process', { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Wishlist processing failed to start');
  return payload.message ?? 'Wishlist processing started';
}

export async function deleteLibraryV2Entity(
  entity: 'artists' | 'albums',
  id: number,
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.delete(`library/v2/${entity}/${id}`),
  );
  if (!payload.success) throw new Error(payload.error || 'Delete failed');
}

/** Impact preview for artist delete: owned releases cascade, featured
 *  appearances on other artists' releases are only detached. */
export interface LibraryV2ArtistDeletePreview {
  artist: string;
  albums: number;
  tracks: number;
  file_links: number;
  detached_albums: number;
}

export async function fetchLibraryV2ArtistDeletePreview(
  id: number,
): Promise<LibraryV2ArtistDeletePreview> {
  const payload = await readJson<
    { success: boolean; error?: string } & LibraryV2ArtistDeletePreview
  >(apiClient.get(`library/v2/artists/${id}/delete-preview`));
  if (!payload.success) throw new Error(payload.error || 'Delete preview failed');
  return payload;
}

export interface LibraryV2FileDeletePreviewItem {
  file_ids: number[];
  track_ids: number[];
  stored_paths: string[];
  path: string | null;
  root: string | null;
  size: number | null;
  deletable: boolean;
  reason: string | null;
  album_title: string | null;
  track_titles: string[];
}

export interface LibraryV2FileDeletePreview {
  entity: 'artists' | 'albums';
  entity_id: number;
  title: string;
  configured_roots: string[];
  files: LibraryV2FileDeletePreviewItem[];
  file_count: number;
  deletable_count: number;
  unsafe_count: number;
  total_size: number;
  preview_token: string;
}

export interface LibraryV2FileDeleteOperation {
  id: string;
  status: 'planned' | 'executing' | 'completed' | 'partial';
  file_count: number;
  total_size: number;
  items: Array<{
    id: number;
    status: 'planned' | 'deleting' | 'deleted' | 'failed';
    error: string | null;
    resolved_path: string;
    file_ids: number[];
  }>;
}

export async function fetchLibraryV2FileDeletePreview(
  entity: 'artists' | 'albums',
  id: number,
): Promise<LibraryV2FileDeletePreview> {
  const payload = await readJson<{ success: boolean; error?: string } & LibraryV2FileDeletePreview>(
    apiClient.get(`library/v2/${entity}/${id}/file-delete-preview`),
  );
  if (!payload.success) throw new Error(payload.error || 'File delete preview failed');
  return payload;
}

export async function deleteLibraryV2Files(
  entity: 'artists' | 'albums',
  id: number,
  previewToken: string,
): Promise<LibraryV2FileDeleteOperation> {
  const payload = await readJson<{
    success: boolean;
    error?: string;
    operation?: LibraryV2FileDeleteOperation;
  }>(
    apiClient.post(`library/v2/${entity}/${id}/file-delete`, {
      json: { preview_token: previewToken },
    }),
  );
  if (!payload.success || !payload.operation) {
    throw new Error(payload.error || 'Physical file deletion failed');
  }
  return payload.operation;
}

export interface LibraryV2HistoryEntry {
  title: string | null;
  album: string | null;
  source: string | null;
  source_detail: string | null;
  quality: string | null;
  bit_depth: number | null;
  sample_rate: number | null;
  bitrate: number | null;
  file_path: string | null;
  status: string | null;
  date: string | null;
}

export async function fetchLibraryV2ArtistHistory(
  artistId: number,
): Promise<LibraryV2HistoryEntry[]> {
  const payload = await readJson<{
    success: boolean;
    history?: LibraryV2HistoryEntry[];
    error?: string;
  }>(apiClient.get(`library/v2/artists/${artistId}/history`));
  if (!payload.success) throw new Error(payload.error || 'History failed');
  return payload.history ?? [];
}

export interface LibraryV2TagDiffField {
  field: string;
  file_value: unknown;
  db_value: unknown;
  changed: boolean;
}

export interface LibraryV2TagPreviewTrack {
  track_id: number;
  title: string | null;
  track_number: number | null;
  album_id: number;
  album_title: string | null;
  file_path: string | null;
  diff: LibraryV2TagDiffField[];
  has_changes: boolean;
  error?: string;
}

export async function fetchLibraryV2TagPreview(
  entity: 'artists' | 'albums',
  id: number,
): Promise<{ tracks: LibraryV2TagPreviewTrack[]; changed_count: number; truncated: boolean }> {
  const payload = await readJson<{
    success: boolean;
    tracks?: LibraryV2TagPreviewTrack[];
    changed_count?: number;
    truncated?: boolean;
    error?: string;
  }>(apiClient.get(`library/v2/${entity}/${id}/tag-preview`, { timeout: 120_000 }));
  if (!payload.success) throw new Error(payload.error || 'Tag preview failed');
  return {
    tracks: payload.tracks ?? [],
    changed_count: payload.changed_count ?? 0,
    truncated: payload.truncated ?? false,
  };
}

export async function writeLibraryV2Tags(trackIds: number[], embedCover = true): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post('library/v2/tags/write', {
      json: { track_ids: trackIds, embed_cover: embedCover },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Write tags failed');
  if (!payload.job_id) throw new Error('Tag writer did not return a job id');
  return payload.job_id;
}

export interface LibraryV2DuplicateSide {
  track_id: number;
  album_title: string | null;
  monitored: boolean;
  file: {
    path: string;
    format: string | null;
    bitrate: number | null;
    sample_rate: number | null;
    bit_depth: number | null;
  } | null;
}

export interface LibraryV2DuplicatePair {
  title: string | null;
  single: LibraryV2DuplicateSide;
  album: LibraryV2DuplicateSide;
}

/** Re-home every file link onto the other track of a validated duplicate pair.
 *  Files on disk are untouched (run Rename/Reorganize afterwards); the now
 *  fileless source is unmonitored so it is not immediately re-downloaded. */
export async function moveLibraryV2TrackFile(
  fromTrackId: number,
  toTrackId: number,
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/tracks/${fromTrackId}/move-file`, {
      json: { to_track_id: toTrackId },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Move failed');
}

/** Unlink a duplicate pair: the single stops pointing at the album version
 *  (it becomes its own canonical recording again). */
export async function unlinkLibraryV2Duplicate(trackId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/tracks/${trackId}/canonical`, {
      json: { canonical_track_id: null },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Unlink failed');
}

export async function fetchLibraryV2Duplicates(
  artistId: number,
): Promise<LibraryV2DuplicatePair[]> {
  const payload = await readJson<{
    success: boolean;
    pairs?: LibraryV2DuplicatePair[];
    error?: string;
  }>(apiClient.get(`library/v2/artists/${artistId}/duplicates`));
  if (!payload.success) throw new Error(payload.error || 'Duplicates failed');
  return payload.pairs ?? [];
}

/** Trigger a repair job (Stats → Repair jobs) immediately. Artist scope uses
 *  the lib2 id so the server can derive an exact file allowlist. */
export async function runRepairJob(
  jobId: string,
  artist?: { id: number; name: string },
): Promise<void> {
  const payload = await readJson<{ success?: boolean; error?: string }>(
    apiClient.post(`repair/jobs/${jobId}/run`, {
      json: artist ? { artist_id: artist.id, artist_name: artist.name } : {},
    }),
  );
  if (payload.error) throw new Error(payload.error);
}

export async function startLibraryV2UpgradeScan(): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post('library/v2/upgrade-scan', { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Upgrade scan failed');
  if (!payload.job_id) throw new Error('Upgrade scan did not return a job id');
  return payload.job_id;
}

export async function fetchLibraryV2JobStatus(jobId?: string): Promise<LibraryV2JobState> {
  const params = new URLSearchParams();
  if (jobId) params.set('job_id', jobId);
  return readJson<LibraryV2JobState>(
    apiClient.get('library/v2/jobs/status', { searchParams: params }),
  );
}

export async function fetchLibraryV2Track(trackId: number): Promise<LibraryV2Track> {
  const payload = await readJson<TrackResponse>(apiClient.get(`library/v2/tracks/${trackId}`));
  if (!payload.success || !payload.track) throw new Error(payload.error || 'Track not found');
  return payload.track;
}

export async function startLibraryV2Import(reset = false): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post('library/v2/import', { json: { reset } }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to start import');
}

export async function fetchLibraryV2ImportStatus(): Promise<LibraryV2ImportState> {
  return readJson<LibraryV2ImportState>(apiClient.get('library/v2/import/status'));
}

export function libraryV2EnabledQueryOptions() {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'enabled'],
    queryFn: fetchLibraryV2Enabled,
  });
}

export function libraryV2ArtistsQueryOptions(
  search: Pick<LibraryV2Search, 'q' | 'sort' | 'page' | 'monitored'>,
) {
  return queryOptions({
    queryKey: [
      ...LIBRARY_V2_QUERY_KEY,
      'artists',
      search.q,
      search.sort,
      search.monitored,
      search.page,
    ],
    queryFn: () => fetchLibraryV2Artists(search),
  });
}

export function libraryV2ArtistQueryOptions(artistId: number) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist', artistId],
    queryFn: () => fetchLibraryV2Artist(artistId),
    enabled: artistId > 0,
  });
}

export function libraryV2AlbumQueryOptions(albumId: number, options: { resolve?: boolean } = {}) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'album', albumId, options.resolve ?? false],
    queryFn: () => fetchLibraryV2Album(albumId, options),
    enabled: albumId > 0,
  });
}

export function libraryV2QualityProfilesQueryOptions() {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'quality-profiles'],
    queryFn: fetchLibraryV2QualityProfiles,
  });
}

// --- Playlists (Phase E) -----------------------------------------------------
// These are intentionally thin clients for the existing mirrored-playlist
// domain and its one shared lifecycle pipeline. Library v2 must not grow a
// second playlist decision engine or importer.

export async function fetchLibraryV2Playlists(): Promise<LibraryV2PlaylistSummary[]> {
  const payload = await readJson<LibraryV2PlaylistSummary[] | { error?: string }>(
    apiClient.get('mirrored-playlists'),
  );
  if (!Array.isArray(payload)) throw new Error(payload.error || 'Failed to load playlists');
  return payload;
}

export async function fetchLibraryV2Playlist(playlistId: number): Promise<LibraryV2PlaylistDetail> {
  const payload = await readJson<LibraryV2PlaylistDetail | { error?: string }>(
    apiClient.get(`mirrored-playlists/${playlistId}`),
  );
  if ('error' in payload && payload.error) throw new Error(payload.error);
  return payload as LibraryV2PlaylistDetail;
}

export async function runLibraryV2PlaylistPipeline(
  playlistId: number,
): Promise<LibraryV2PlaylistPipelineState> {
  const payload = await readJson<{
    success?: boolean;
    state?: LibraryV2PlaylistPipelineState;
    error?: string;
  }>(apiClient.post(`mirrored-playlists/${playlistId}/pipeline/run`, { json: {} }));
  if (!payload.success || !payload.state) {
    throw new Error(payload.error || 'Playlist pipeline failed to start');
  }
  return payload.state;
}

export function libraryV2PlaylistsQueryOptions() {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'playlists'],
    queryFn: fetchLibraryV2Playlists,
    refetchInterval: (query) =>
      query.state.data?.some((playlist) => playlist.pipeline_state?.status === 'running')
        ? 2_000
        : false,
  });
}

export function libraryV2PlaylistQueryOptions(playlistId: number) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'playlist', playlistId],
    queryFn: () => fetchLibraryV2Playlist(playlistId),
    enabled: playlistId > 0,
    refetchInterval: (query) =>
      query.state.data?.pipeline_state?.status === 'running' ? 2_000 : false,
  });
}

// --- Mirror outbox (lib2 → legacy wishlist/watchlist, audit P0-04) -----------

export interface LibraryV2MirrorStatus {
  pending: number;
  failed: number;
}

export async function fetchLibraryV2MirrorStatus(): Promise<LibraryV2MirrorStatus> {
  const payload = await readJson<{
    success: boolean;
    pending?: number;
    failed?: number;
    error?: string;
  }>(apiClient.get('library/v2/mirror-status'));
  if (!payload.success) throw new Error(payload.error || 'Failed to load mirror status');
  return { pending: payload.pending ?? 0, failed: payload.failed ?? 0 };
}

export async function retryLibraryV2Mirror(): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post('library/v2/mirror-retry', { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Mirror retry failed');
}

export function libraryV2MirrorStatusQueryOptions() {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'mirror-status'],
    queryFn: fetchLibraryV2MirrorStatus,
    refetchInterval: 60_000,
  });
}

export function invalidateLibraryV2(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
}

// --- Interactive Search → SoulSync download pipeline -------------------------
// Reuses the existing multi-source file search (`/api/search`) and download
// (`/api/download`) endpoints — the same pipeline the main Search page drives,
// honouring the configured source priorities (download_source.mode/hybrid_order).

export interface SourceSearchResult {
  result_type: 'track' | 'album';
  username: string;
  filename: string;
  size: number;
  bitrate?: number | null;
  sample_rate?: number | null;
  bit_depth?: number | null;
  duration?: number | null;
  quality?: string | null;
  artist?: string | null;
  title?: string | null;
  album?: string | null;
  track_count?: number | null;
  free_upload_slots?: number | null;
  queue_length?: number | null;
  quality_score?: number | null;
  // Album-result fields (when result_type === 'album'):
  album_title?: string | null;
  album_path?: string | null;
  dominant_quality?: string | null;
  total_size?: number | null;
  tracks?: Array<Record<string, unknown>>;
  _source_metadata?: {
    protocol?: string;
    indexer?: string;
    grabs?: number;
    seeders?: number;
    leechers?: number;
    publish_date?: string | null;
  } | null;
}

export interface DownloadOptions {
  skipAcoustid?: boolean;
  qualityCheck?: boolean;
}

/** The Library-v2 entity a manual grab acts for. Only the ids travel — the
 *  server re-validates them and resolves the entity's EFFECTIVE quality
 *  profile itself, so the client can't dictate the profile (audit P1-16).
 *  `qualityProfileId` is display-only (search-modal preview badge). */
export interface Lib2EntityRef {
  trackId?: number;
  albumId?: number;
  qualityProfileId?: number;
}

function lib2EntityFields(entity?: Lib2EntityRef): Record<string, number> {
  return {
    ...(entity?.trackId ? { lib2_track_id: entity.trackId } : {}),
    ...(entity?.albumId ? { lib2_album_id: entity.albumId } : {}),
  };
}

export async function searchSources(query: string, source?: string): Promise<SourceSearchResult[]> {
  const payload = await readJson<{ results?: SourceSearchResult[]; error?: string }>(
    apiClient.post('search', {
      json: { query, ...(source ? { source } : {}) },
      timeout: 90_000, // source search (Soulseek etc.) can take up to ~75s
    }),
  );
  if (payload.error) throw new Error(payload.error);
  return payload.results ?? [];
}

export interface DownloadSearchSource {
  name: string;
  display_name: string;
}

export interface DownloadSearchSources {
  mode: string;
  sources: DownloadSearchSource[];
}

export async function listSearchSources(): Promise<DownloadSearchSources> {
  try {
    const payload = await readJson<{
      mode?: string;
      sources?: Array<{ name?: string; display_name?: string } | string>;
    }>(apiClient.get('search/sources'));
    const raw = payload.sources ?? [];
    return {
      mode: payload.mode ?? 'unknown',
      sources: raw
        .map((source) => {
          const name = typeof source === 'string' ? source : (source.name ?? '');
          const displayName =
            typeof source === 'string' ? source : (source.display_name ?? source.name ?? '');
          return { name, display_name: displayName };
        })
        .filter((source) => source.name),
    };
  } catch {
    return { mode: 'unknown', sources: [] };
  }
}

export async function startSourceDownload(
  result: SourceSearchResult,
  options: DownloadOptions = {},
  entity?: Lib2EntityRef,
): Promise<void> {
  const checks = {
    skip_acoustid: options.skipAcoustid === true,
    quality_check: options.qualityCheck !== false,
    ...lib2EntityFields(entity),
  };
  const json =
    result.result_type === 'album'
      ? {
          result_type: 'album',
          album_name: result.album_title,
          tracks: result.tracks ?? [],
          ...checks,
        }
      : {
          result_type: 'track',
          username: result.username,
          filename: result.filename,
          size: result.size,
          title: result.title,
          artist: result.artist,
          quality: result.quality,
          ...checks,
        };
  const payload = await readJson<{ success?: boolean; error?: string; blocked?: boolean }>(
    apiClient.post('download', { json, timeout: 30_000 }),
  );
  if (payload.blocked) throw new Error('This artist is blocklisted.');
  if (payload.error) throw new Error(payload.error);
}

/** Auto-grab: search and download the best result (for non-interactive "Search"). */
export async function autoGrabBest(
  query: string,
  options: DownloadOptions = {},
  entity?: Lib2EntityRef,
): Promise<SourceSearchResult | null> {
  const all = await searchSources(query);
  if (all.length === 0) return null;
  // A track action must not grab a release-bundle result (audit P1-18):
  // the pipeline would import one arbitrary file of a whole album.
  const pool = entity?.trackId ? all.filter((r) => r.result_type === 'track') : all;
  if (pool.length === 0) return null;
  // Prefer lossless, then highest quality_score, then most upload slots.
  const score = (r: SourceSearchResult) => {
    const q = (r.quality ?? r.dominant_quality ?? '').toLowerCase();
    const lossless = q.includes('flac') || q.includes('alac') || q.includes('wav') ? 1 : 0;
    return lossless * 1e6 + (r.quality_score ?? 0) * 100 + (r.free_upload_slots ?? 0);
  };
  const best = [...pool].sort((a, b) => score(b) - score(a))[0];
  await startSourceDownload(best, options, entity);
  return best;
}
