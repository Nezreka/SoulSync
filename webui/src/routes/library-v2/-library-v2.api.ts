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
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/${entity}/${id}/quality-profile`, {
      json: { quality_profile_id: qualityProfileId, cascade },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to update quality profile');
}

export async function refreshLibraryV2(
  entity: 'artists' | 'albums',
  id: number,
): Promise<void> {
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
  const payload = await readJson<
    { success: boolean; error?: string } & LibraryV2DiscographyStats
  >(
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
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/artists/${artistId}/releases/monitor`, {
      json: { scope, monitored },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Bulk monitor failed');
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

export async function deleteLibraryV2Entity(
  entity: 'artists' | 'albums',
  id: number,
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.delete(`library/v2/${entity}/${id}`),
  );
  if (!payload.success) throw new Error(payload.error || 'Delete failed');
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

export async function startLibraryV2UpgradeScan(): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post('library/v2/upgrade-scan', { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Upgrade scan failed');
}

export async function fetchLibraryV2JobStatus(): Promise<LibraryV2JobState> {
  return readJson<LibraryV2JobState>(apiClient.get('library/v2/jobs/status'));
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

export async function listSearchSources(): Promise<string[]> {
  try {
    const payload = await readJson<{ sources?: { name?: string }[] | string[] }>(
      apiClient.get('search/sources'),
    );
    const raw = payload.sources ?? [];
    return raw.map((s) => (typeof s === 'string' ? s : (s.name ?? ''))).filter(Boolean);
  } catch {
    return [];
  }
}

export async function startSourceDownload(
  result: SourceSearchResult,
  options: DownloadOptions = {},
): Promise<void> {
  const checks = {
    skip_acoustid: options.skipAcoustid === true,
    quality_check: options.qualityCheck !== false,
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
): Promise<SourceSearchResult | null> {
  const all = await searchSources(query);
  if (all.length === 0) return null;
  // Prefer lossless, then highest quality_score, then most upload slots.
  const score = (r: SourceSearchResult) => {
    const q = (r.quality ?? r.dominant_quality ?? '').toLowerCase();
    const lossless = q.includes('flac') || q.includes('alac') || q.includes('wav') ? 1 : 0;
    return lossless * 1e6 + (r.quality_score ?? 0) * 100 + (r.free_upload_slots ?? 0);
  };
  const best = [...all].sort((a, b) => score(b) - score(a))[0];
  await startSourceDownload(best, options);
  return best;
}
