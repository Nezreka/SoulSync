import { queryOptions, type QueryClient } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  ImportAlbum,
  ImportAlbumMatch,
  ImportAlbumMatchPayload,
  ImportAlbumSearchPayload,
  ImportAutoImportResultsPayload,
  ImportAutoImportSettingsPayload,
  ImportAutoImportStatusPayload,
  ImportProcessPayload,
  ImportStagingFilesPayload,
  ImportStagingGroupsPayload,
  ImportTrackSearchPayload,
} from './-import.types';

export const IMPORT_QUERY_KEY = ['import'] as const;

function assertSuccess<T extends { success: boolean; error?: string }>(
  payload: T,
  fallback: string,
): T {
  if (!payload.success) {
    throw new Error(payload.error || fallback);
  }
  return payload;
}

export async function fetchImportStagingFiles(): Promise<ImportStagingFilesPayload> {
  return assertSuccess(
    await readJson<ImportStagingFilesPayload>(apiClient.get('import/staging/files')),
    'Failed to load import folder',
  );
}

export async function fetchImportStagingGroups(): Promise<ImportStagingGroupsPayload> {
  return assertSuccess(
    await readJson<ImportStagingGroupsPayload>(apiClient.get('import/staging/groups')),
    'Failed to load auto-detected albums',
  );
}

export async function fetchImportStagingSuggestions(): Promise<ImportAlbumSearchPayload> {
  return assertSuccess(
    await readJson<ImportAlbumSearchPayload>(apiClient.get('import/staging/suggestions')),
    'Failed to load import suggestions',
  );
}

export async function searchImportAlbums(query: string): Promise<ImportAlbumSearchPayload> {
  return assertSuccess(
    await readJson<ImportAlbumSearchPayload>(
      apiClient.get('import/search/albums', {
        searchParams: {
          q: query,
          limit: '12',
        },
      }),
    ),
    'Album search failed',
  );
}

export async function matchImportAlbum(input: {
  albumId: string;
  source?: string | null;
  albumName?: string | null;
  albumArtist?: string | null;
  filePaths?: string[] | null;
}): Promise<ImportAlbumMatchPayload> {
  return assertSuccess(
    await readJson<ImportAlbumMatchPayload>(
      apiClient.post('import/album/match', {
        json: {
          album_id: input.albumId,
          source: input.source || '',
          album_name: input.albumName || '',
          album_artist: input.albumArtist || '',
          ...(input.filePaths?.length ? { file_paths: input.filePaths } : {}),
        },
      }),
    ),
    'Failed to match album',
  );
}

export async function processImportAlbumTrack(input: {
  album: ImportAlbum;
  match: ImportAlbumMatch;
}): Promise<ImportProcessPayload> {
  return assertSuccess(
    await readJson<ImportProcessPayload>(
      apiClient.post('import/album/process', {
        json: {
          album: input.album,
          matches: [input.match],
        },
      }),
    ),
    'Failed to process album track',
  );
}

export async function searchImportTracks(query: string): Promise<ImportTrackSearchPayload> {
  return assertSuccess(
    await readJson<ImportTrackSearchPayload>(
      apiClient.get('import/search/tracks', {
        searchParams: {
          q: query,
          limit: '6',
        },
      }),
    ),
    'Track search failed',
  );
}

export async function processImportSingleFile(file: unknown): Promise<ImportProcessPayload> {
  return assertSuccess(
    await readJson<ImportProcessPayload>(
      apiClient.post('import/singles/process', {
        json: {
          files: [file],
        },
      }),
    ),
    'Failed to process single',
  );
}

export async function fetchAutoImportStatus(): Promise<ImportAutoImportStatusPayload> {
  return assertSuccess(
    await readJson<ImportAutoImportStatusPayload>(apiClient.get('auto-import/status')),
    'Failed to load auto-import status',
  );
}

export async function fetchAutoImportSettings(): Promise<ImportAutoImportSettingsPayload> {
  return assertSuccess(
    await readJson<ImportAutoImportSettingsPayload>(apiClient.get('auto-import/settings')),
    'Failed to load auto-import settings',
  );
}

export async function saveAutoImportSettings(input: {
  confidenceThreshold: number;
  scanInterval: number;
}): Promise<void> {
  assertSuccess(
    await readJson<{ success: boolean; error?: string }>(
      apiClient.post('auto-import/settings', {
        json: {
          confidence_threshold: input.confidenceThreshold,
          scan_interval: input.scanInterval,
        },
      }),
    ),
    'Failed to save auto-import settings',
  );
}

export async function fetchAutoImportResults(): Promise<ImportAutoImportResultsPayload> {
  return assertSuccess(
    await readJson<ImportAutoImportResultsPayload>(
      apiClient.get('auto-import/results', {
        searchParams: {
          limit: '100',
        },
      }),
    ),
    'Failed to load auto-import results',
  );
}

export async function toggleAutoImport(enabled: boolean): Promise<void> {
  assertSuccess(
    await readJson<{ success: boolean; error?: string }>(
      apiClient.post('auto-import/toggle', {
        json: { enabled },
      }),
    ),
    'Failed to toggle auto-import',
  );
}

export async function triggerAutoImportScan(): Promise<void> {
  assertSuccess(
    await readJson<{ success: boolean; error?: string }>(apiClient.post('auto-import/scan-now')),
    'Failed to trigger scan',
  );
}

export async function approveAutoImportResult(id: number): Promise<void> {
  assertSuccess(
    await readJson<{ success: boolean; error?: string }>(
      apiClient.post(`auto-import/approve/${id}`),
    ),
    'Failed to approve import',
  );
}

export async function rejectAutoImportResult(id: number): Promise<void> {
  assertSuccess(
    await readJson<{ success: boolean; error?: string }>(
      apiClient.post(`auto-import/reject/${id}`),
    ),
    'Failed to dismiss import',
  );
}

export async function approveAllAutoImportResults(): Promise<number> {
  const payload = assertSuccess(
    await readJson<{ success: boolean; count?: number; error?: string }>(
      apiClient.post('auto-import/approve-all'),
    ),
    'Failed to approve imports',
  );
  return payload.count ?? 0;
}

export async function clearCompletedAutoImportResults(): Promise<number> {
  const payload = assertSuccess(
    await readJson<{ success: boolean; count?: number; error?: string }>(
      apiClient.post('auto-import/clear-completed'),
    ),
    'Failed to clear import history',
  );
  return payload.count ?? 0;
}

export function importStagingFilesQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'staging-files'],
    queryFn: fetchImportStagingFiles,
  });
}

export function importStagingGroupsQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'staging-groups'],
    queryFn: fetchImportStagingGroups,
  });
}

export function importStagingSuggestionsQueryOptions() {
  return queryOptions({
    queryKey: [...IMPORT_QUERY_KEY, 'staging-suggestions'],
    queryFn: fetchImportStagingSuggestions,
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

export function invalidateImportQueries(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: IMPORT_QUERY_KEY });
}

export function invalidateImportStagingQueries(queryClient: QueryClient) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'staging-files'] }),
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'staging-groups'] }),
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'staging-suggestions'] }),
  ]);
}

export function invalidateAutoImportQueries(queryClient: QueryClient) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'auto-import-status'] }),
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'auto-import-settings'] }),
    queryClient.invalidateQueries({ queryKey: [...IMPORT_QUERY_KEY, 'auto-import-results'] }),
  ]);
}
