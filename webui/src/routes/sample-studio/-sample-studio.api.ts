/**
 * Sample Studio — API layer.
 *
 * Library search reuses the session-auth GET /api/library/tracks wrapper
 * (free-text `q` over title + artist) and GET /api/library/recently-added
 * (default browse view). Analysis + peaks come from the Phase 1 api/sample.py
 * endpoints via the session-auth /api/sample/* wrappers in web_server.py.
 */

import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  AnalysisStatus,
  PreviewResult,
  SampleAnalysis,
  SamplePeaks,
  StashEntry,
  StashFormat,
  StemName,
  StemsInfo,
  StudioTrack,
} from './-sample-studio.types';

import { isAnalysisError } from './-sample-studio.types';

export const SAMPLE_STUDIO_QUERY_KEY = ['sample-studio'] as const;

interface Envelope<T> {
  success: boolean;
  data: T;
  error: string | null;
}

interface TrackSearchResponse {
  tracks: StudioTrack[];
}

interface RecentlyAddedResponse {
  items: StudioTrack[];
  type: string;
}

interface AnalysisPayload {
  track_id: number;
  status: AnalysisStatus;
  bpm: number | null;
  onsets: number[] | null;
  duration_s: number | null;
}

/** Search the library by title/artist. Empty query -> recently-added tracks. */
export function studioTrackSearchQueryOptions(query: string) {
  const q = query.trim();
  return queryOptions({
    queryKey: [...SAMPLE_STUDIO_QUERY_KEY, 'tracks', q] as const,
    queryFn: async (): Promise<StudioTrack[]> => {
      if (!q) {
        const payload = await readJson<Envelope<RecentlyAddedResponse>>(
          apiClient.get('library/recently-added', {
            searchParams: { type: 'tracks', limit: 50 },
          }),
        );
        return payload.data.items.map(toStudioTrack);
      }
      const payload = await readJson<Envelope<TrackSearchResponse>>(
        apiClient.get('library/tracks', {
          searchParams: { q, limit: 50 },
        }),
      );
      return payload.data.tracks.map(toStudioTrack);
    },
  });
}

/**
 * serialize_track ships `duration` in MILLISECONDS (the tracks.duration DB
 * unit); every Sample Studio consumer (length filter, editor header,
 * waveform fallback) works in seconds. Convert once here — the search
 * queryFn above is the single choke point both the search and the browse
 * view flow through, so every StudioTrack in the app carries seconds.
 */
function toStudioTrack(row: StudioTrack): StudioTrack {
  const ms = row.duration;
  return { ...row, duration: typeof ms === 'number' ? ms / 1000 : ms };
}

function normalizeAnalysis(payload: AnalysisPayload): SampleAnalysis {
  return {
    track_id: payload.track_id,
    status: payload.status,
    bpm: payload.bpm ?? null,
    onsets: Array.isArray(payload.onsets) ? payload.onsets : [],
    duration_s: payload.duration_s ?? null,
  };
}

/**
 * Track analysis. Refetches on an interval while the status isn't done —
 * this is the lazy-backfill poll: opening a never-analyzed track enqueues it
 * server-side (202) and we poll until the worker finishes.
 *
 * Worker-recorded errors are sticky server-side so the poll observes them
 * (then refetchInterval stops and the UI shows Try again). Bumping
 * `retryNonce` re-fires the query with ?retry=1, which clears the recorded
 * error and queues the track again.
 */
export function studioAnalysisQueryOptions(trackId: number | null, retryNonce = 0) {
  return queryOptions({
    queryKey: [...SAMPLE_STUDIO_QUERY_KEY, 'analysis', trackId, retryNonce] as const,
    enabled: trackId !== null,
    queryFn: async (): Promise<SampleAnalysis> => {
      if (trackId === null) throw new Error('trackId is required');
      const searchParams: Record<string, string | number> = { track_id: trackId };
      if (retryNonce > 0) searchParams.retry = 1;
      const payload = await readJson<Envelope<AnalysisPayload>>(
        apiClient.get('sample/analysis', { searchParams }),
      );
      return normalizeAnalysis(payload.data);
    },
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (!status || status === 'done' || isAnalysisError(status)) return false;
      return 2500;
    },
  });
}

export function studioPeaksQueryOptions(
  trackId: number | null,
  buckets = 1500,
  stem: StemName | null = null,
) {
  return queryOptions({
    queryKey: [...SAMPLE_STUDIO_QUERY_KEY, 'peaks', trackId, buckets, stem] as const,
    enabled: trackId !== null,
    staleTime: Infinity,
    queryFn: async (): Promise<SamplePeaks> => {
      if (trackId === null) throw new Error('trackId is required');
      const params: Record<string, string | number> = { track_id: trackId, buckets };
      if (stem) params.stem = stem;
      const payload = await readJson<Envelope<SamplePeaks>>(
        apiClient.get('sample/peaks', { searchParams: params }),
      );
      return payload.data;
    },
    retry: 1,
  });
}

/** Stream URL for the editor's <audio> element — reuses /stream/library-audio. */
export function studioStreamUrl(filePath: string): string {
  return `/stream/library-audio?path=${encodeURIComponent(filePath)}`;
}

// ── Phase 3: preview / chop / stash ────────────────────────────────────

export interface PreviewParams {
  start: number;
  end: number;
  pitchSt: number;
  targetBpm: number | null;
  /** Cut from a separated stem instead of the full mix. */
  stem?: StemName | null;
}

interface PreviewResponse {
  preview_id: string;
  engine: string;
  duration_s: number;
}

/** Fast server render of the in/out region for auditioning pitch/BPM changes. */
export async function requestPreview(
  trackId: number,
  params: PreviewParams,
): Promise<PreviewResult> {
  const payload = await readJson<Envelope<PreviewResponse>>(
    apiClient.post('sample/preview', {
      json: {
        track_id: trackId,
        start_s: params.start,
        end_s: params.end,
        pitch_st: params.pitchSt,
        target_bpm: params.targetBpm,
        stem: params.stem ?? null,
      },
    }),
  );
  return {
    preview_id: payload.data.preview_id,
    engine: payload.data.engine,
    duration_s: payload.data.duration_s,
  };
}

/** Preview audio URL for an <audio> element (short-lived server cache). */
export function previewAudioUrl(previewId: string): string {
  return `/api/sample/preview/${encodeURIComponent(previewId)}`;
}

export interface SaveChopParams extends PreviewParams {
  name: string;
  tags: string[];
  format: StashFormat;
  /** Destination sample folder — one of the configured folders, else the default. */
  folder?: string | null;
}

/** Final render + stash row (file + bookmark). */
export async function saveChop(trackId: number, params: SaveChopParams): Promise<StashEntry> {
  const payload = await readJson<Envelope<StashEntry>>(
    apiClient.post('sample/chop', {
      json: {
        track_id: trackId,
        start_s: params.start,
        end_s: params.end,
        pitch_st: params.pitchSt,
        target_bpm: params.targetBpm,
        stem: params.stem ?? null,
        name: params.name,
        tags: params.tags,
        format: params.format,
        folder: params.folder ?? null,
      },
    }),
  );
  return payload.data;
}

export interface SampleFolders {
  folders: string[];
  default: string | null;
}

/** Configured sample output folders (first = default destination). */
export function studioSampleFoldersQueryOptions() {
  return queryOptions({
    queryKey: [...SAMPLE_STUDIO_QUERY_KEY, 'folders'] as const,
    staleTime: Infinity,
    queryFn: async (): Promise<SampleFolders> => {
      const payload = await readJson<Envelope<SampleFolders>>(apiClient.get('sample/folders'));
      return payload.data;
    },
    retry: 1,
  });
}

interface StashListResponse {
  entries: StashEntry[];
}

export function studioStashQueryOptions() {
  return queryOptions({
    queryKey: [...SAMPLE_STUDIO_QUERY_KEY, 'stash'] as const,
    queryFn: async (): Promise<StashEntry[]> => {
      const payload = await readJson<Envelope<StashListResponse>>(apiClient.get('sample/stash'));
      return payload.data.entries;
    },
  });
}

export async function deleteStashEntry(entryId: number): Promise<void> {
  await readJson<Envelope<{ deleted: number }>>(apiClient.delete(`sample/stash/${entryId}`));
}

/** Serve a stash entry's rendered audio. */
export function stashAudioUrl(entryId: number): string {
  return `/api/sample/stash/${entryId}/audio`;
}

/** Download link for the stash ZIP export. */
export function stashExportUrl(): string {
  return '/api/sample/stash/export';
}

/** Direct streaming URL for one separated stem. */
export function stemAudioUrl(trackId: number, stem: StemName): string {
  return `/api/sample/stems/${trackId}/${stem}/audio`;
}

/** Enqueue stem separation for a track. Idempotent. */
export async function requestStems(trackId: number): Promise<StemsInfo> {
  const payload = await readJson<Envelope<StemsInfo>>(
    apiClient.post('sample/stems', { json: { track_id: trackId } }),
  );
  return payload.data;
}

export function studioStemsStatusQueryOptions(trackId: number | null, active: boolean) {
  return queryOptions({
    queryKey: [...SAMPLE_STUDIO_QUERY_KEY, 'stems', 'status', trackId] as const,
    enabled: trackId !== null,
    // Keep polling while the worker runs; TanStack stops when active is false.
    refetchInterval: (query) => {
      const data = query.state.data as StemsInfo | undefined;
      if (!active) return false;
      if (!data) return 1500;
      return data.status === 'done' || data.status.startsWith('error') ? false : 1500;
    },
    queryFn: async (): Promise<StemsInfo> => {
      if (trackId === null) throw new Error('trackId is required');
      const payload = await readJson<Envelope<StemsInfo>>(
        apiClient.get('sample/stems/status', {
          searchParams: { track_id: trackId },
        }),
      );
      return payload.data;
    },
    retry: 1,
  });
}
