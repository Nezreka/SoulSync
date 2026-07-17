import { queryOptions, type QueryClient } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  LibraryV2AlbumDetail,
  LibraryV2ArtistAliasMember,
  LibraryV2ArtistDetail,
  LibraryV2ArtistSettings,
  LibraryV2ArtistSummary,
  LibraryV2DiscographyStats,
  LibraryV2FileTags,
  LibraryV2ImportState,
  LibraryV2JobState,
  LibraryV2Pagination,
  LibraryV2PlaylistDetail,
  LibraryV2PlaylistPipelineState,
  LibraryV2PlaylistSummary,
  LibraryV2ArtCandidate,
  LibraryV2ManualSkip,
  LibraryV2MatchService,
  LibraryV2QualityProfile,
  LibraryV2ReorganizePreview,
  LibraryV2ReorganizeQueueItem,
  LibraryV2ReorganizeQueueSnapshot,
  LibraryV2ReorganizeSource,
  LibraryV2Search,
  LibraryV2Track,
  LibraryV2TrackDownload,
  LibraryV2ArtistTableColumns,
  LibraryV2TrackTableColumns,
  LibraryV2UiPreferences,
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

/** Turn a missing album slot into a real track row (legacy "Add to Library"
 *  prerequisite). Returns the new/existing track id; monitoring is a separate
 *  setLibraryV2Monitored call so it runs through the proven wishlist mirror. */
export async function materializeLibraryV2MissingTrack(
  albumId: number,
  slot: { track_number: number; disc_number?: number; title?: string },
): Promise<{ track_id: number; created: boolean }> {
  const payload = await readJson<{
    success: boolean;
    track_id: number;
    created: boolean;
    error?: string;
  }>(apiClient.post(`library/v2/albums/${albumId}/missing-tracks/materialize`, { json: slot }));
  if (!payload.success) throw new Error(payload.error || 'Failed to add track to library');
  return { track_id: payload.track_id, created: payload.created };
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
  qualityProfileId: number | null,
  cascade = true,
  monitorExisting = false,
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/${entity}/${id}/quality-profile`, {
      json: {
        ...(qualityProfileId === null
          ? { inherit: true }
          : { quality_profile_id: qualityProfileId, inherit: false }),
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

/** Provider match chips for an artist (legacy Enhanced-View parity). */
export async function fetchLibraryV2ArtistMatchStatus(
  artistId: number,
): Promise<LibraryV2MatchService[]> {
  const payload = await readJson<{
    success: boolean;
    services: LibraryV2MatchService[];
    error?: string;
  }>(apiClient.get(`library/v2/artists/${artistId}/match-status`));
  if (!payload.success) throw new Error(payload.error || 'Failed to load match status');
  return payload.services ?? [];
}

export interface LibraryV2AlbumMatchBundle {
  album: LibraryV2MatchService[];
  tracks: Record<number, LibraryV2MatchService[]>;
}

/** Album chips + per-track chip map in one batched read. */
export async function fetchLibraryV2AlbumMatchStatus(
  albumId: number,
): Promise<LibraryV2AlbumMatchBundle> {
  const payload = await readJson<{
    success: boolean;
    album: LibraryV2MatchService[];
    tracks: Record<number, LibraryV2MatchService[]>;
    error?: string;
  }>(apiClient.get(`library/v2/albums/${albumId}/match-status`));
  if (!payload.success) throw new Error(payload.error || 'Failed to load match status');
  return { album: payload.album ?? [], tracks: payload.tracks ?? {} };
}

/** §40 — the artist's full alias group (canonical + linked aliases). Works
 *  whether ``artistId`` is itself the canonical row or one of its aliases.
 *  See docs/library-v2.md §24. */
export async function fetchLibraryV2ArtistAliases(
  artistId: number,
): Promise<{ canonicalArtistId: number; aliases: LibraryV2ArtistAliasMember[] }> {
  const payload = await readJson<{
    success: boolean;
    canonical_artist_id?: number;
    aliases?: LibraryV2ArtistAliasMember[];
    error?: string;
  }>(apiClient.get(`library/v2/artists/${artistId}/aliases`));
  if (!payload.success) throw new Error(payload.error || 'Failed to load aliases');
  return {
    canonicalArtistId: payload.canonical_artist_id ?? artistId,
    aliases: payload.aliases ?? [],
  };
}

/** §40: mark ``artistId`` as an alias of ``aliasOfId`` — the same real artist
 *  under a different, unlinked provider identity. Both rows keep their own
 *  albums/tracks (soft link, nothing is reassigned or deleted). */
export async function linkLibraryV2ArtistAlias(artistId: number, aliasOfId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/artists/${artistId}/link-alias`, {
      json: { alias_of: aliasOfId },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Link failed');
}

/** §40: detach ``artistId`` from its canonical artist, if any — it becomes a
 *  standalone entry again (its own albums are untouched either way). */
export async function unlinkLibraryV2ArtistAlias(artistId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.delete(`library/v2/artists/${artistId}/link-alias`),
  );
  if (!payload.success) throw new Error(payload.error || 'Unlink failed');
}

/** Manually match an entity to a provider id, reusing the app-wide legacy
 *  endpoint (keys on the legacy row id carried by the match chip). */
export async function manualMatchLibraryV2Entity(input: {
  entity_type: 'artist' | 'album' | 'track';
  legacy_entity_id: number;
  service: string;
  service_id: string;
  artist_legacy_id?: number;
  watchlist_row_id?: number;
}): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.put('library/manual-match', {
      json: {
        entity_type: input.entity_type,
        entity_id: input.legacy_entity_id,
        service: input.service,
        service_id: input.service_id,
        ...(input.artist_legacy_id ? { artist_id: input.artist_legacy_id } : {}),
        ...(input.watchlist_row_id ? { watchlist_row_id: input.watchlist_row_id } : {}),
      },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Manual match failed');
}

/** Clear a wrong provider identity, optionally keeping the linked Watchlist
 * row in the same transaction as the legacy library row. */
export async function clearLibraryV2EntityMatch(input: {
  entity_type: 'artist' | 'album' | 'track';
  legacy_entity_id: number;
  service: string;
  watchlist_row_id?: number;
}): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.put('library/clear-match', {
      json: {
        entity_type: input.entity_type,
        entity_id: input.legacy_entity_id,
        service: input.service,
        ...(input.watchlist_row_id ? { watchlist_row_id: input.watchlist_row_id } : {}),
      },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Clear match failed');
}

export interface LibraryV2MatchSearchResult {
  id: string;
  name: string;
  extra?: string;
  image?: string;
  provider?: string;
  /** §52.5: only Spotify and Deezer artist search actually supply these —
   *  0/undefined means "not provided by this provider", not a real zero. */
  followers?: number;
  popularity?: number;
}

export interface LibraryV2MatchRelease {
  id: string;
  title: string;
  image?: string | null;
  release_date?: string | null;
  album_type?: string | null;
  total_tracks?: number | null;
}

export interface LibraryV2MatchReleasePreview {
  supported: boolean;
  albums: LibraryV2MatchRelease[];
}

/** Search a provider for candidate matches (reuses the app-wide endpoint). */
export async function searchLibraryV2MatchService(input: {
  service: string;
  entity_type: 'artist' | 'album' | 'track';
  query: string;
}): Promise<LibraryV2MatchSearchResult[]> {
  const payload = await readJson<{
    success: boolean;
    results: LibraryV2MatchSearchResult[];
    error?: string;
  }>(apiClient.post('library/search-service', { json: input }));
  if (!payload.success) throw new Error(payload.error || 'Provider search failed');
  return payload.results ?? [];
}

/** Exact-provider album context for disambiguating an artist candidate. */
export async function fetchLibraryV2MatchArtistReleases(input: {
  service: string;
  artist_id: string;
  artist_name: string;
  limit?: number;
}): Promise<LibraryV2MatchReleasePreview> {
  const payload = await readJson<{
    success: boolean;
    supported?: boolean;
    albums?: LibraryV2MatchRelease[];
    error?: string;
  }>(apiClient.post('library/match-artist-releases', { json: input }));
  if (!payload.success) throw new Error(payload.error || 'Release preview failed');
  return { supported: payload.supported !== false, albums: payload.albums ?? [] };
}

interface SourceInfoResponse {
  success: boolean;
  downloads: LibraryV2TrackDownload[];
  manual_skips?: LibraryV2ManualSkip[];
  error?: string;
}

export interface LibraryV2TrackSourceInfo {
  downloads: LibraryV2TrackDownload[];
  manual_skips: LibraryV2ManualSkip[];
}

/** Download provenance + manual check-skip audit for a track (legacy
 *  "Source Info" popover parity, extended with the §18.3 lifecycle log). */
export async function fetchLibraryV2TrackSourceInfo(
  trackId: number,
): Promise<LibraryV2TrackSourceInfo> {
  const payload = await readJson<SourceInfoResponse>(
    apiClient.get(`library/v2/tracks/${trackId}/source-info`),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to load source info');
  return { downloads: payload.downloads ?? [], manual_skips: payload.manual_skips ?? [] };
}

/** Blacklist a download source so the pipeline skips it (reuses the app-wide route). */
export async function blacklistLibraryV2Source(input: {
  track_title: string;
  track_artist?: string;
  blocked_filename: string;
  blocked_username: string;
}): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post('library/blacklist', {
      json: { reason: 'user_rejected', track_artist: '', ...input },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to blacklist source');
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

/** §52.5/§56.2: on-demand Spotify stats for the current-match identity card —
 *  omitted entirely when the artist has no linked spotify id or the live
 *  lookup failed/was rate-limited, never a fake zero. */
export interface LibraryV2ArtistStats {
  followers: number;
  popularity: number;
  name?: string | null;
  image_url?: string | null;
  genres?: string[];
}

export interface LibraryV2ArtistSettingsResponse {
  settings: LibraryV2ArtistSettings;
  metadata_sources: string[];
  global_metadata_source: string | null;
  artist_stats: LibraryV2ArtistStats | null;
}

export async function fetchLibraryV2ArtistSettings(
  artistId: number,
): Promise<LibraryV2ArtistSettingsResponse> {
  const payload = await readJson<{
    success: boolean;
    error?: string;
    settings?: LibraryV2ArtistSettings;
    metadata_sources?: string[];
    global_metadata_source?: string | null;
    artist_stats?: LibraryV2ArtistStats | null;
  }>(apiClient.get(`library/v2/artists/${artistId}/settings`));
  if (!payload.success || !payload.settings) {
    throw new Error(payload.error || 'Artist settings could not be loaded');
  }
  return {
    settings: payload.settings,
    metadata_sources: payload.metadata_sources ?? [],
    global_metadata_source: payload.global_metadata_source ?? null,
    artist_stats: payload.artist_stats ?? null,
  };
}

export async function updateLibraryV2ArtistSettings(
  artistId: number,
  settings: Pick<
    LibraryV2ArtistSettings,
    | 'monitor_new_items'
    | 'include_albums'
    | 'include_eps'
    | 'include_singles'
    | 'include_live'
    | 'include_remixes'
    | 'include_acoustic'
    | 'include_compilations'
    | 'include_instrumentals'
    | 'auto_download'
    | 'lookback_days'
    | 'preferred_metadata_source'
  >,
): Promise<LibraryV2ArtistSettingsResponse> {
  const payload = await readJson<{
    success: boolean;
    error?: string;
    settings?: LibraryV2ArtistSettings;
    metadata_sources?: string[];
    global_metadata_source?: string | null;
  }>(
    apiClient.put(`library/v2/artists/${artistId}/settings`, {
      json: {
        monitor_new_items: settings.monitor_new_items,
        include_albums: settings.include_albums,
        include_eps: settings.include_eps,
        include_singles: settings.include_singles,
        include_live: settings.include_live,
        include_remixes: settings.include_remixes,
        include_acoustic: settings.include_acoustic,
        include_compilations: settings.include_compilations,
        include_instrumentals: settings.include_instrumentals,
        auto_download: settings.auto_download,
        lookback_days: settings.lookback_days,
        preferred_metadata_source: settings.preferred_metadata_source,
      },
    }),
  );
  if (!payload.success || !payload.settings) {
    throw new Error(payload.error || 'Artist settings could not be saved');
  }
  return {
    settings: payload.settings,
    metadata_sources: payload.metadata_sources ?? [],
    global_metadata_source: payload.global_metadata_source ?? null,
    artist_stats: null,
  };
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
  mode: 'database_only' | 'permanent';
  actor: string;
  actor_profile_id: number | null;
  file_count: number;
  total_size: number;
  items: Array<{
    id: number;
    status: 'planned' | 'deleting' | 'deleted' | 'removed' | 'failed';
    error: string | null;
    resolved_path: string;
    file_ids: number[];
  }>;
}

export async function removeLibraryV2FileRecords(
  entity: 'artists' | 'albums',
  id: number,
  fileIds?: number[],
): Promise<LibraryV2FileDeleteOperation> {
  const payload = await readJson<{
    success: boolean;
    error?: string;
    operation?: LibraryV2FileDeleteOperation;
  }>(
    apiClient.post(`library/v2/${entity}/${id}/file-remove`, {
      json: fileIds?.length ? { file_ids: fileIds } : {},
    }),
  );
  if (!payload.success || !payload.operation) {
    throw new Error(payload.error || 'File records could not be removed');
  }
  return payload.operation;
}

export async function fetchLibraryV2FileDeletePreview(
  entity: 'artists' | 'albums',
  id: number,
  fileIds?: number[],
): Promise<LibraryV2FileDeletePreview> {
  const searchParams = fileIds?.length
    ? new URLSearchParams({ file_ids: fileIds.join(',') })
    : undefined;
  const payload = await readJson<{ success: boolean; error?: string } & LibraryV2FileDeletePreview>(
    apiClient.get(`library/v2/${entity}/${id}/file-delete-preview`, { searchParams }),
  );
  if (!payload.success) throw new Error(payload.error || 'File delete preview failed');
  return payload;
}

export async function deleteLibraryV2Files(
  entity: 'artists' | 'albums',
  id: number,
  previewToken: string,
  fileIds?: number[],
): Promise<LibraryV2FileDeleteOperation> {
  const payload = await readJson<{
    success: boolean;
    error?: string;
    operation?: LibraryV2FileDeleteOperation;
  }>(
    apiClient.post(`library/v2/${entity}/${id}/file-delete`, {
      json: {
        preview_token: previewToken,
        ...(fileIds?.length ? { file_ids: fileIds } : {}),
      },
    }),
  );
  if (!payload.success || !payload.operation) {
    throw new Error(payload.error || 'Physical file deletion failed');
  }
  return payload.operation;
}

export interface LibraryV2ArtistTrackFile {
  file_id: number;
  track_id: number;
  track_title: string | null;
  track_number: number | null;
  disc_number: number | null;
  album_id: number;
  album_title: string | null;
  path: string;
  size: number | null;
  format: string | null;
  bitrate: number | null;
  sample_rate: number | null;
  bit_depth: number | null;
  quality_tier: string | null;
  file_state: string;
  is_primary: boolean;
  added_at: string | null;
}

/** C2 (Manage Track Files): every physical file this artist owns, flat and
 *  paginated — feeds the "Files" tab whose selection drives the ADR-05
 *  preview/delete above. */
export async function fetchLibraryV2ArtistTrackFiles(
  artistId: number,
  { search = '', page = 1, limit = 100 }: { search?: string; page?: number; limit?: number } = {},
): Promise<{ files: LibraryV2ArtistTrackFile[]; pagination: LibraryV2Pagination }> {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  params.set('page', String(page));
  params.set('limit', String(limit));
  const payload = await readJson<{
    success: boolean;
    error?: string;
    files?: LibraryV2ArtistTrackFile[];
    pagination?: LibraryV2Pagination;
  }>(apiClient.get(`library/v2/artists/${artistId}/track-files`, { searchParams: params }));
  if (!payload.success) throw new Error(payload.error || 'Track files failed');
  return {
    files: payload.files ?? [],
    pagination: payload.pagination ?? {
      page,
      limit,
      total_count: 0,
      total_pages: 0,
      has_prev: false,
      has_next: false,
    },
  };
}

export type LibraryV2HistoryCategory =
  | 'grabbed'
  | 'imported'
  | 'failed'
  | 'quarantined'
  | 'blocklist'
  | 'moved'
  | 'deleted'
  | 'override'
  | 'info';

export interface LibraryV2HistoryEntry {
  date: string | null;
  event_type: string;
  category: LibraryV2HistoryCategory;
  title: string | null;
  detail: string | null;
  source: string | null;
  status?: 'passed' | 'failed' | 'skipped' | 'not_run' | 'error' | null;
  payload?: Record<string, unknown>;
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

/** §52.9: the merged search→grab→quality→quarantine→import pipeline for one
 * track, including attempts that never produced a `lib2_track_files` row. */
export async function fetchLibraryV2TrackHistory(
  trackId: number,
): Promise<LibraryV2HistoryEntry[]> {
  const payload = await readJson<{
    success: boolean;
    history?: LibraryV2HistoryEntry[];
    error?: string;
  }>(apiClient.get(`library/v2/tracks/${trackId}/history`));
  if (!payload.success) throw new Error(payload.error || 'History failed');
  return payload.history ?? [];
}

/** §52.9 album branch: same merged resolver as artist/track history, scoped
 * to just this release. */
export async function fetchLibraryV2AlbumHistory(
  albumId: number,
): Promise<LibraryV2HistoryEntry[]> {
  const payload = await readJson<{
    success: boolean;
    history?: LibraryV2HistoryEntry[];
    error?: string;
  }>(apiClient.get(`library/v2/albums/${albumId}/history`));
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

/** Lidarr-style scoped Automatic Search (deep-dive C1): searches missing
 *  tracks AND, if the quality profile allows it, upgrades — for exactly this
 *  artist/album/track, never the whole wishlist. Replaces the client-side
 *  best-pick heuristic (A4) and the mislabeled album-row global search (A3).
 *  Returns a job id to poll via fetchLibraryV2JobStatus; the job's `result`
 *  carries `{checked, queued, searching, batch_id}`. */
export async function startLibraryV2ScopedSearch(
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post(`library/v2/${entity}/${id}/search`, { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Search failed');
  if (!payload.job_id) throw new Error('Search did not return a job id');
  return payload.job_id;
}

/** Analyze an album's files and write track+album ReplayGain tags. Returns a
 *  job id to poll via fetchLibraryV2JobStatus (legacy Enrich→ReplayGain). */
export async function startLibraryV2AlbumReplayGain(albumId: number): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post(`library/v2/albums/${albumId}/replaygain`, { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'ReplayGain analysis failed');
  if (!payload.job_id) throw new Error('ReplayGain did not return a job id');
  return payload.job_id;
}

/** Analyze one track and write its track-level ReplayGain tags (synchronous).
 *  Returns the track gain in dB. */
export async function analyzeLibraryV2TrackReplayGain(trackId: number): Promise<number | null> {
  const payload = await readJson<{
    success: boolean;
    track_gain_db?: number | null;
    error?: string;
  }>(apiClient.post(`library/v2/tracks/${trackId}/replaygain`, { json: {} }));
  if (!payload.success) throw new Error(payload.error || 'ReplayGain analysis failed');
  return payload.track_gain_db ?? null;
}

/** Fetch + write lyrics for one track from LRClib (synchronous) — the "LR"
 *  badge's missing→click path (deep-dive B3). */
export async function fetchLibraryV2TrackLyrics(trackId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; fetched?: boolean; error?: string }>(
    apiClient.post(`library/v2/tracks/${trackId}/fetch-lyrics`, { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Lyrics fetch failed');
}

/** Re-query one metadata provider for one entity (legacy Enrich parity, §44).
 *  Delegates to the same per-provider worker the legacy Enhanced View's
 *  Enrich dropdown uses, then resyncs the refreshed fields onto the lib2 row. */
export async function enrichLibraryV2Entity(
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
  service: string,
): Promise<{ message?: string; resynced: boolean }> {
  const payload = await readJson<{
    success: boolean;
    message?: string;
    error?: string;
    resynced: boolean;
  }>(apiClient.post(`library/v2/${entity}/${id}/enrich`, { json: { service } }));
  if (!payload.success) throw new Error(payload.error || payload.message || 'Enrichment failed');
  return { message: payload.message, resynced: payload.resynced };
}

/** Sources authed/configured on this instance — for the artist-level
 *  "Reorganize All" source picker (docs §50, no per-album ID coverage check). */
export async function fetchLibraryV2ReorganizeSourcesGlobal(): Promise<
  LibraryV2ReorganizeSource[]
> {
  const payload = await readJson<{
    success: boolean;
    sources: LibraryV2ReorganizeSource[];
    error?: string;
  }>(apiClient.get('library/v2/reorganize/sources'));
  if (!payload.success) throw new Error(payload.error || 'Failed to load reorganize sources');
  return payload.sources;
}

/** Sources this album has a stored provider ID for AND an authed client —
 *  for the per-album source picker (docs §50). */
export async function fetchLibraryV2AlbumReorganizeSources(
  albumId: number,
): Promise<LibraryV2ReorganizeSource[]> {
  const payload = await readJson<{
    success: boolean;
    sources: LibraryV2ReorganizeSource[];
    error?: string;
  }>(apiClient.get(`library/v2/albums/${albumId}/reorganize/sources`));
  if (!payload.success) throw new Error(payload.error || 'Failed to load reorganize sources');
  return payload.sources;
}

/** Preview current-vs-proposed file paths for one lib2 album, without moving
 *  anything (docs §50). */
export async function previewLibraryV2AlbumReorganize(
  albumId: number,
  options: { source?: string | null; mode?: 'api' | 'tags' } = {},
): Promise<LibraryV2ReorganizePreview> {
  const payload = await readJson<LibraryV2ReorganizePreview & { error?: string }>(
    apiClient.post(`library/v2/albums/${albumId}/reorganize/preview`, {
      json: { source: options.source ?? null, mode: options.mode ?? 'api' },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Reorganize preview failed');
  return payload;
}

/** Enqueue one lib2 album for reorganize — returns immediately, the queue
 *  worker processes items FIFO (docs §50). */
export async function applyLibraryV2AlbumReorganize(
  albumId: number,
  options: { source?: string | null; mode?: 'api' | 'tags'; renameOnly?: boolean } = {},
): Promise<{ queued: boolean; queueId?: string; reason?: string }> {
  const payload = await readJson<{
    success: boolean;
    queued?: boolean;
    queue_id?: string;
    reason?: string;
    error?: string;
  }>(
    apiClient.post(`library/v2/albums/${albumId}/reorganize`, {
      json: {
        source: options.source ?? null,
        mode: options.mode ?? 'api',
        rename_only: Boolean(options.renameOnly),
      },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Reorganize failed');
  return { queued: Boolean(payload.queued), queueId: payload.queue_id, reason: payload.reason };
}

/** Enqueue every album of one lib2 artist for reorganize (docs §50). Same
 *  source/mode pick applied to every album — no per-album overrides. */
export async function applyLibraryV2ArtistReorganizeAll(
  artistId: number,
  options: { source?: string | null; mode?: 'api' | 'tags' } = {},
): Promise<{ enqueued: number; alreadyQueued: number; totalAlbums: number }> {
  const payload = await readJson<{
    success: boolean;
    enqueued?: number;
    already_queued?: number;
    total_albums?: number;
    error?: string;
  }>(
    apiClient.post(`library/v2/artists/${artistId}/reorganize-all`, {
      json: { source: options.source ?? null, mode: options.mode ?? 'api' },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Reorganize-all failed');
  return {
    enqueued: payload.enqueued ?? 0,
    alreadyQueued: payload.already_queued ?? 0,
    totalAlbums: payload.total_albums ?? 0,
  };
}

interface RawReorganizeQueueItem {
  queue_id: string;
  album_id: string;
  album_title: string;
  artist_name: string;
  status: LibraryV2ReorganizeQueueItem['status'];
  result_status: string | null;
  current_track: string | null;
  progress_total: number;
  progress_processed: number;
  finished_at: number | null;
}

function normalizeReorganizeQueueItem(raw: RawReorganizeQueueItem): LibraryV2ReorganizeQueueItem {
  return {
    queueId: raw.queue_id,
    albumId: raw.album_id,
    albumTitle: raw.album_title,
    artistName: raw.artist_name,
    status: raw.status,
    resultStatus: raw.result_status ?? null,
    currentTrack: raw.current_track ?? null,
    progressTotal: raw.progress_total ?? 0,
    progressProcessed: raw.progress_processed ?? 0,
    finishedAt: raw.finished_at ?? null,
  };
}

/** Snapshot of the (legacy, shared) reorganize queue — the lib2 Reorganize
 *  modals poll this so an enqueued move has visible live status instead of
 *  being fire-and-forget (deep-dive G7). Same endpoint the legacy Enhanced
 *  View's status panel uses; album/artist ids in items are LEGACY ids
 *  (the lib2→legacy reorganize bridge, docs §50), so lib2 callers match by
 *  `queueId` (per-album apply) or `artistName` (best-effort for the
 *  artist-wide bulk apply, which doesn't get per-item ids back). */
export async function fetchLibraryV2ReorganizeQueueSnapshot(): Promise<LibraryV2ReorganizeQueueSnapshot> {
  const payload = await readJson<{
    success: boolean;
    active?: RawReorganizeQueueItem | null;
    queued?: RawReorganizeQueueItem[];
    recent?: RawReorganizeQueueItem[];
    error?: string;
  }>(apiClient.get('library/reorganize/queue'));
  if (!payload.success) throw new Error(payload.error || 'Failed to load the reorganize queue');
  return {
    active: payload.active ? normalizeReorganizeQueueItem(payload.active) : null,
    queued: (payload.queued ?? []).map(normalizeReorganizeQueueItem),
    recent: (payload.recent ?? []).map(normalizeReorganizeQueueItem),
  };
}

/** Candidate cover-art images for an album, for the art picker (docs §49). */
export async function fetchLibraryV2AlbumArtOptions(
  albumId: number,
  options: { refresh?: boolean } = {},
): Promise<LibraryV2ArtCandidate[]> {
  const params = new URLSearchParams();
  if (options.refresh) params.set('refresh', '1');
  const payload = await readJson<{
    success: boolean;
    candidates: LibraryV2ArtCandidate[];
    error?: string;
  }>(apiClient.get(`library/v2/albums/${albumId}/art-options`, { searchParams: params }));
  if (!payload.success) throw new Error(payload.error || 'Failed to load cover art options');
  return payload.candidates;
}

/** Apply a cover chosen in the picker (docs §49). Pins the choice so a later
 *  refresh won't clobber it; returns the local artwork URL to re-render. */
export async function applyLibraryV2AlbumArt(albumId: number, url: string): Promise<string> {
  const payload = await readJson<{ success: boolean; image_url?: string; error?: string }>(
    apiClient.post(`library/v2/albums/${albumId}/art`, { json: { url } }),
  );
  if (!payload.success || !payload.image_url) {
    throw new Error(payload.error || 'Failed to apply cover art');
  }
  return payload.image_url;
}

/** Candidate photos for an artist, for the image picker (deep-dive A9). */
export async function fetchLibraryV2ArtistArtOptions(
  artistId: number,
  options: { refresh?: boolean } = {},
): Promise<LibraryV2ArtCandidate[]> {
  const params = new URLSearchParams();
  if (options.refresh) params.set('refresh', '1');
  const payload = await readJson<{
    success: boolean;
    candidates: LibraryV2ArtCandidate[];
    error?: string;
  }>(apiClient.get(`library/v2/artists/${artistId}/art-options`, { searchParams: params }));
  if (!payload.success) throw new Error(payload.error || 'Failed to load photo options');
  return payload.candidates;
}

/** Apply a photo chosen in the picker (deep-dive A9). Pins the choice so a
 *  later refresh won't clobber it; returns the local artwork URL to re-render. */
export async function applyLibraryV2ArtistArt(artistId: number, url: string): Promise<string> {
  const payload = await readJson<{ success: boolean; image_url?: string; error?: string }>(
    apiClient.post(`library/v2/artists/${artistId}/art`, { json: { url } }),
  );
  if (!payload.success || !payload.image_url) {
    throw new Error(payload.error || 'Failed to apply photo');
  }
  return payload.image_url;
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

export function libraryV2ImportStatusQueryOptions(refetchIntervalMs = 1000) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'import-status'],
    queryFn: fetchLibraryV2ImportStatus,
    refetchInterval: (query) => (query.state.data?.running ? refetchIntervalMs : false),
  });
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

export function libraryV2ArtistMatchStatusQueryOptions(artistId: number) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist-match', artistId],
    queryFn: () => fetchLibraryV2ArtistMatchStatus(artistId),
    enabled: artistId > 0,
    staleTime: 30_000,
  });
}

export function libraryV2ArtistAliasesQueryOptions(artistId: number) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist-aliases', artistId],
    queryFn: () => fetchLibraryV2ArtistAliases(artistId),
    enabled: artistId > 0,
  });
}

export function libraryV2AlbumMatchStatusQueryOptions(albumId: number) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'album-match', albumId],
    queryFn: () => fetchLibraryV2AlbumMatchStatus(albumId),
    enabled: albumId > 0,
    staleTime: 30_000,
  });
}

export function libraryV2TrackSourceInfoQueryOptions(trackId: number, enabled: boolean) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'track-source-info', trackId],
    queryFn: () => fetchLibraryV2TrackSourceInfo(trackId),
    enabled: enabled && trackId > 0,
    staleTime: 30_000,
  });
}

interface FileTagsResponse extends LibraryV2FileTags {
  success: boolean;
  error?: string;
}

/** Live embedded tags + lyrics read straight from the file (§18.1). */
export async function fetchLibraryV2TrackFileTags(trackId: number): Promise<LibraryV2FileTags> {
  const payload = await readJson<FileTagsResponse>(
    apiClient.get(`library/v2/tracks/${trackId}/file-tags`),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to read file tags');
  return payload;
}

export async function editTrackFileTag(trackId: number, key: string, value: string): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/tracks/${trackId}/file-tags/edit`, {
      json: { key, value },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to edit tag');
}

export function libraryV2TrackFileTagsQueryOptions(trackId: number, enabled: boolean) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'track-file-tags', trackId],
    queryFn: () => fetchLibraryV2TrackFileTags(trackId),
    enabled: enabled && trackId > 0,
    staleTime: 30_000,
  });
}

export function libraryV2QualityProfilesQueryOptions() {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'quality-profiles'],
    queryFn: fetchLibraryV2QualityProfiles,
  });
}

// --- UI display preferences (B5) ---------------------------------------------

interface UiPreferencesResponse {
  success: boolean;
  preferences: LibraryV2UiPreferences;
  error?: string;
}

/** Deep-partial patch for the PUT endpoint's shallow-merge-per-section
 *  contract (core/library2/ui_preferences.py's ``_merge_section``): each
 *  section field, and each field within `columns`, is independently optional. */
type UiPreferencesPatch = {
  track_table?: {
    columns?: Partial<LibraryV2TrackTableColumns>;
    column_order?: (keyof LibraryV2TrackTableColumns)[];
    show_all_match_providers?: boolean;
    visible_match_providers?: Record<string, boolean>;
    quality_show_format?: boolean;
    quality_show_resolution?: boolean;
    quality_show_bitrate?: boolean;
  };
  artist_table?: {
    columns?: Partial<LibraryV2ArtistTableColumns>;
    column_order?: (keyof LibraryV2ArtistTableColumns)[];
  };
};

export async function fetchLibraryV2UiPreferences(): Promise<LibraryV2UiPreferences> {
  const payload = await readJson<UiPreferencesResponse>(apiClient.get('library/v2/ui-preferences'));
  if (!payload.success) throw new Error(payload.error || 'Failed to load UI preferences');
  return payload.preferences;
}

/** Deep-partial patch — merged server-side onto the stored (or default)
 *  preferences one section deep, so callers only send what changed. */
export async function updateLibraryV2UiPreferences(
  patch: UiPreferencesPatch,
): Promise<LibraryV2UiPreferences> {
  const payload = await readJson<UiPreferencesResponse>(
    apiClient.put('library/v2/ui-preferences', { json: patch }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to update UI preferences');
  return payload.preferences;
}

export function libraryV2UiPreferencesQueryOptions() {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'ui-preferences'],
    queryFn: fetchLibraryV2UiPreferences,
    staleTime: 60_000,
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
