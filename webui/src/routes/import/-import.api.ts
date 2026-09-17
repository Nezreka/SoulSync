import { queryOptions, type QueryClient } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';
import { appURL } from '@/platform/url-base';

import type {
  ImportAlbum,
  ImportAlbumMatch,
  ImportAlbumMatchPayload,
  ImportAlbumSearchPayload,
  ImportAutoImportResultsPayload,
  ImportAutoImportSettingsPayload,
  ImportAutoImportStatusPayload,
  ImportFingerprintPayload,
  ImportInboxPayload,
  ImportPreviewPayload,
  ImportProcessPayload,
  ImportUploadChunkPayload,
  ImportUploadPayload,
  LibraryCheckPayload,
  ImportSearchSourcesPayload,
  ImportStagingFilesPayload,
  ImportStagingGroupsPayload,
  ImportTrackSearchPayload,
  AutoImportQualityProfile,
  QualityProfilesPayload,
} from './-import.types';

export const IMPORT_QUERY_KEY = ['import'] as const;

// Per-track import does heavy synchronous enrichment server-side (metadata
// lookups, art, lyrics) and can legitimately take 60-90s/track — much longer
// when external sources are degraded. ky's default 10s timeout aborts those
// requests client-side even though the server completes the import (200),
// which left the progress bar stuck at 0 and showing "Failed" while files
// imported fine (#772). Give the import-process calls a generous bound so the
// responses actually arrive and the bar advances. Scoped to import only.
const IMPORT_REQUEST_TIMEOUT_MS = 300_000; // 5 min/track

export async function fetchImportInbox(): Promise<ImportInboxPayload> {
  return readJson<ImportInboxPayload>(apiClient.get('import/inbox'));
}

export async function fetchImportStagingFiles(): Promise<ImportStagingFilesPayload> {
  return readJson<ImportStagingFilesPayload>(apiClient.get('import/staging/files'));
}

export async function fetchImportStagingGroups(): Promise<ImportStagingGroupsPayload> {
  return readJson<ImportStagingGroupsPayload>(apiClient.get('import/staging/groups'));
}

export async function fetchImportStagingSuggestions(): Promise<ImportAlbumSearchPayload> {
  return readJson<ImportAlbumSearchPayload>(apiClient.get('import/staging/suggestions'));
}

export async function searchImportAlbums(
  query: string,
  source?: string,
): Promise<ImportAlbumSearchPayload> {
  return readJson<ImportAlbumSearchPayload>(
    apiClient.get('import/search/albums', {
      searchParams: {
        q: query,
        limit: '12',
        ...(source ? { source } : {}),
      },
    }),
  );
}

export async function fetchImportSearchSources(): Promise<ImportSearchSourcesPayload> {
  return readJson<ImportSearchSourcesPayload>(apiClient.get('import/search/sources'));
}

export async function matchImportAlbum(input: {
  albumId: string;
  source?: string | null;
  albumName?: string | null;
  albumArtist?: string | null;
  filePaths?: string[] | null;
}): Promise<ImportAlbumMatchPayload> {
  return readJson<ImportAlbumMatchPayload>(
    apiClient.post('import/album/match', {
      json: {
        album_id: input.albumId,
        source: input.source || '',
        album_name: input.albumName || '',
        album_artist: input.albumArtist || '',
        ...(input.filePaths?.length ? { file_paths: input.filePaths } : {}),
      },
      // #957: building the match payload fetches the tracklist + reads every staging file's tags.
      // On a slow NAS / large album that exceeds ky's default 10s and aborts with "Request timed
      // out" even though the server is still working. Same long bound the import-process calls use.
      timeout: IMPORT_REQUEST_TIMEOUT_MS,
    }),
  );
}

type ImportJobResponse = {
  job_id?: string;
  state?: 'queued' | 'running' | 'complete';
  result?: ImportProcessPayload;
  status?: number;
};

async function runImportJob(path: string, json: unknown): Promise<ImportProcessPayload> {
  // getRandomValues also works on plain HTTP LAN installs, where randomUUID
  // is unavailable. Retain the old timeout for servers predating async imports.
  const key = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
  const accepted = await readJson<ImportJobResponse & ImportProcessPayload>(
    apiClient.post(path, {
      json,
      headers: { Prefer: 'respond-async', 'Idempotency-Key': key },
      timeout: IMPORT_REQUEST_TIMEOUT_MS,
    }),
  );
  // Supports an older server during an upgrade.
  if (!accepted.job_id) return accepted;
  let failures = 0;
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    let job: ImportJobResponse;
    try {
      job = await readJson<ImportJobResponse>(apiClient.get(`import/jobs/${accepted.job_id}`));
      failures = 0;
    } catch (error) {
      failures += 1;
      if (failures >= 3 || (error instanceof Error && error.name === 'HTTPError')) throw error;
      continue;
    }
    if (job.state === 'complete') {
      if (!job.result) throw new Error('Import job returned no result');
      if (!job.result.success || (job.status ?? 200) >= 400) {
        throw new Error(job.result.error || 'Import processing failed');
      }
      return job.result;
    }
  }
}

export async function processImportAlbumTrack(input: {
  album: ImportAlbum;
  match: ImportAlbumMatch;
}): Promise<ImportProcessPayload> {
  return runImportJob('import/album/process', { album: input.album, matches: [input.match] });
}

export async function searchImportTracks(query: string): Promise<ImportTrackSearchPayload> {
  return readJson<ImportTrackSearchPayload>(
    apiClient.get('import/search/tracks', {
      searchParams: {
        q: query,
        limit: '6',
      },
    }),
  );
}

export async function processImportSingleFile(file: unknown): Promise<ImportProcessPayload> {
  return runImportJob('import/singles/process', { files: [file] });
}

export async function fetchAutoImportStatus(): Promise<ImportAutoImportStatusPayload> {
  return readJson<ImportAutoImportStatusPayload>(apiClient.get('auto-import/status'));
}

export async function fetchAutoImportSettings(): Promise<ImportAutoImportSettingsPayload> {
  return readJson<ImportAutoImportSettingsPayload>(apiClient.get('auto-import/settings'));
}

export async function saveAutoImportSettings(input: {
  confidenceThreshold: number;
  scanInterval: number;
  qualityProfileId?: number | null;
  autoProcess?: boolean;
}): Promise<void> {
  await readJson<{ success: boolean; error?: string }>(
    apiClient.post('auto-import/settings', {
      json: {
        confidence_threshold: input.confidenceThreshold,
        scan_interval: input.scanInterval,
        quality_profile_id: input.qualityProfileId ?? null,
        ...(input.autoProcess === undefined ? {} : { auto_process: input.autoProcess }),
      },
    }),
  );
}

// Every quality profile (the app-wide default + any named custom ones).
export async function fetchQualityProfiles(): Promise<AutoImportQualityProfile[]> {
  const payload = await readJson<QualityProfilesPayload>(apiClient.get('quality-profile/custom'));
  return payload.profiles ?? [];
}

export async function fetchAutoImportResults(): Promise<ImportAutoImportResultsPayload> {
  return readJson<ImportAutoImportResultsPayload>(
    apiClient.get('auto-import/results', {
      searchParams: {
        limit: '100',
      },
    }),
  );
}

export async function toggleAutoImport(enabled: boolean): Promise<void> {
  await readJson<{ success: boolean; error?: string }>(
    apiClient.post('auto-import/toggle', {
      json: { enabled },
    }),
  );
}

export async function triggerAutoImportScan(): Promise<void> {
  await readJson<{ success: boolean; error?: string }>(apiClient.post('auto-import/scan-now'));
}

export async function approveAutoImportResult(id: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`auto-import/approve/${id}`),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to approve import');
  }
}

export async function rejectAutoImportResult(id: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`auto-import/reject/${id}`),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to dismiss import');
  }
}

export async function retryAutoImportResult(id: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`auto-import/retry/${id}`),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to retry');
}

export async function resolveAutoImportResult(id: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`auto-import/resolve/${id}`),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to record the import');
}

export async function approveAllAutoImportResults(): Promise<number> {
  const payload = await readJson<{ success: boolean; count?: number; error?: string }>(
    apiClient.post('auto-import/approve-all'),
  );
  return payload.count ?? 0;
}

export async function clearCompletedAutoImportResults(): Promise<number> {
  const payload = await readJson<{ success: boolean; count?: number; error?: string }>(
    apiClient.post('auto-import/clear-completed'),
  );
  return payload.count ?? 0;
}

// #957: scanning the staging folder is expensive (a 6k-file library takes a long time). Its contents
// only change when an import moves files out — and that path already invalidates these queries. So
// cache the result across tab/page switches instead of letting react-query's default staleTime:0 +
// refetchOnMount/refetchOnWindowFocus refetch (and re-scan the whole folder) on every tab change.
// Manual Refresh + the post-import flow still invalidate -> refetch -> fresh scan (the backend's own
// 6s TTL is short, so an invalidated refetch genuinely re-scans); gcTime keeps the cache alive while
// the user is on another page so coming back doesn't re-scan either.
const STAGING_CACHE = { staleTime: 30 * 60_000, gcTime: 60 * 60_000 } as const;

// The inbox polls: the worker moves items through identifying / importing on
// its own clock, and the page has to follow it without a refresh button.
export function importInboxQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'inbox'],
    queryFn: fetchImportInbox,
    staleTime: 4_000,
  });
}

export function importStagingFilesQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'staging-files'],
    queryFn: fetchImportStagingFiles,
    ...STAGING_CACHE,
  });
}

export function importStagingGroupsQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'staging-groups'],
    queryFn: fetchImportStagingGroups,
    ...STAGING_CACHE,
  });
}

export function importStagingSuggestionsQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'staging-suggestions'],
    queryFn: fetchImportStagingSuggestions,
    ...STAGING_CACHE,
  });
}

export function importSearchSourcesQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'search-sources'],
    queryFn: fetchImportSearchSources,
    ...STAGING_CACHE,
  });
}

export function autoImportStatusQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'auto-import-status'],
    queryFn: fetchAutoImportStatus,
  });
}

export function autoImportSettingsQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'auto-import-settings'],
    queryFn: fetchAutoImportSettings,
  });
}

export function autoImportResultsQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'auto-import-results'],
    queryFn: fetchAutoImportResults,
  });
}

export function qualityProfilesQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'quality-profiles'],
    queryFn: fetchQualityProfiles,
  });
}

export function invalidateImportQueries(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: IMPORT_QUERY_KEY });
}

export function invalidateImportStagingQueries(queryClient: QueryClient) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'inbox'] }),
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'staging-files'] }),
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'staging-groups'] }),
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'staging-suggestions'] }),
  ]);
}

export function invalidateAutoImportQueries(queryClient: QueryClient) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'inbox'] }),
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'auto-import-status'] }),
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'auto-import-settings'] }),
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'auto-import-results'] }),
  ]);
}

/** What an album import would do, per track. Nothing is written. */
export async function previewImportAlbum(input: {
  album: ImportAlbum;
  matches: ImportAlbumMatch[];
}): Promise<ImportPreviewPayload> {
  return readJson<ImportPreviewPayload>(
    apiClient.post('import/album/preview', { json: input, timeout: 60_000 }),
  );
}

/** Which of these track names the library already has, for this artist. */
export async function checkLibraryTracks(input: {
  artistName: string;
  albumName?: string;
  tracks: { name: string }[];
}): Promise<LibraryCheckPayload> {
  return readJson<LibraryCheckPayload>(
    apiClient.post('library/check-tracks', {
      json: {
        artist_name: input.artistName,
        album_name: input.albumName ?? '',
        tracks: input.tracks,
      },
      timeout: 60_000,
    }),
  );
}

/** pieces of a few megabytes get through any reverse proxy's body cap */
const UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024;

function postForm(url: string, form: FormData, onProgress?: (fraction: number) => void) {
  return new Promise<ImportUploadChunkPayload>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      let payload: ImportUploadChunkPayload | null = null;
      try {
        payload = JSON.parse(xhr.responseText) as ImportUploadChunkPayload;
      } catch {
        payload = null;
      }
      if (xhr.status >= 200 && xhr.status < 300 && payload?.success) resolve(payload);
      else reject(new Error(payload?.error || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error('Upload failed'));
    xhr.send(form);
  });
}

/**
 * Upload one file into the import folder in pieces, keeping the relative
 * path the browser knows (a dropped folder's name). XHR rather than ky: ky
 * has no upload progress, and a 400 MB FLAC deserves a bar. Pieces because
 * a proxy in front of a docker install may cap a body at a megabyte.
 */
export async function uploadImportFile(
  file: File,
  relativePath: string,
  onProgress?: (fraction: number) => void,
): Promise<ImportUploadPayload> {
  const total = Math.max(1, Math.ceil(file.size / UPLOAD_CHUNK_BYTES));
  const uploadId = Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) =>
    b.toString(16).padStart(2, '0'),
  ).join('');
  let last: ImportUploadChunkPayload | null = null;
  for (let index = 0; index < total; index += 1) {
    const start = index * UPLOAD_CHUNK_BYTES;
    const piece = file.slice(start, Math.min(file.size, start + UPLOAD_CHUNK_BYTES));
    const form = new FormData();
    form.append('upload_id', uploadId);
    form.append('index', String(index));
    form.append('total', String(total));
    form.append('path', relativePath);
    form.append('chunk', piece, file.name);
    last = await postForm(appURL('/api/import/upload/chunk'), form, (fraction) => {
      if (onProgress) onProgress((index + fraction) / total);
    });
  }
  if (onProgress) onProgress(1);
  return { success: true, saved: last?.saved ? [last.saved] : [], skipped: [] };
}

/** AcoustID the first few files of an item, on demand. */
export async function fingerprintImportFiles(
  filePaths: string[],
): Promise<ImportFingerprintPayload> {
  return readJson<ImportFingerprintPayload>(
    apiClient.post('import/fingerprint', { json: { file_paths: filePaths }, timeout: 120_000 }),
  );
}
