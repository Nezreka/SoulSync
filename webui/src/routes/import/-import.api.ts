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

export async function fetchImportStagingFiles(): Promise<ImportStagingFilesPayload> {
  return readJson<ImportStagingFilesPayload>(apiClient.get('import/staging/files'));
}

export async function fetchImportStagingGroups(): Promise<ImportStagingGroupsPayload> {
  return readJson<ImportStagingGroupsPayload>(apiClient.get('import/staging/groups'));
}

export async function fetchImportStagingSuggestions(): Promise<ImportAlbumSearchPayload> {
  return readJson<ImportAlbumSearchPayload>(apiClient.get('import/staging/suggestions'));
}

export async function searchImportAlbums(query: string): Promise<ImportAlbumSearchPayload> {
  return readJson<ImportAlbumSearchPayload>(
    apiClient.get('import/search/albums', {
      searchParams: {
        q: query,
        limit: '12',
      },
    }),
  );
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
    }),
  );
}

export async function processImportAlbumTrack(input: {
  album: ImportAlbum;
  match: ImportAlbumMatch;
}): Promise<ImportProcessPayload> {
  return readJson<ImportProcessPayload>(
    apiClient.post('import/album/process', {
      json: {
        album: input.album,
        matches: [input.match],
      },
    }),
  );
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
  return readJson<ImportProcessPayload>(
    apiClient.post('import/singles/process', {
      json: {
        files: [file],
      },
    }),
  );
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
}): Promise<void> {
  await readJson<{ success: boolean; error?: string }>(
    apiClient.post('auto-import/settings', {
      json: {
        confidence_threshold: input.confidenceThreshold,
        scan_interval: input.scanInterval,
      },
    }),
  );
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
