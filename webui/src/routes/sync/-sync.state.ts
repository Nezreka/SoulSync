/**
 * The per-playlist state layer, as pure reducers.
 *
 * The vanilla keeps this state in THREE mutable registries
 * (youtubePlaylistStates + per-source twins + listenbrainzPlaylistStates),
 * writes every field under BOTH snake and camel names, and resets it in seven
 * near-identical per-source blocks inside closeYouTubeDiscoveryModal
 * (10237-10455). Here it is one typed shape and four reducers; the controller
 * hook owns WHEN they run, the api layer owns the wire.
 *
 * Backend payloads still arrive dual-keyed — the hydration reducer accepts
 * both spellings and the store keeps exactly one.
 */

import type { SyncStartResponse, SourceSyncStatusResponse } from './-sync.api';
import type { SourceVerticalConfig } from './-sync.sources';
import type { DiscoveryRow, RawDiscoveryResult } from './-sync.transform';

import { toDiscoveryRows } from './-sync.transform';

export interface SyncProgressSnapshot {
  progress?: number;
  total_tracks?: number;
  matched_tracks?: number;
  failed_tracks?: number;
  current_step?: string;
  current_track?: string;
}

export interface SourcePlaylistState {
  /** The source's own id — numeric id / url_hash / chart hash / mbid. */
  sourceId: string;
  /** The registry key: fakeHashPrefix + sourceId ('' prefixes keep the id). */
  fakeHash: string;
  phase: string;
  /** The source playlist object as loaded (name/track count/tracks...). */
  playlist?: Record<string, unknown>;
  /** Raw backend results, kept verbatim for download hand-off rebuilds. */
  rawResults: RawDiscoveryResult[];
  /** Transformed modal rows. */
  rows: DiscoveryRow[];
  discoveryProgress: number;
  spotifyMatches: number;
  spotifyTotal: number;
  convertedSpotifyPlaylistId?: string;
  downloadProcessId?: string;
  syncPlaylistId?: string;
  lastSyncProgress?: SyncProgressSnapshot;
  wingIt?: boolean;
}

export function freshSourceState(
  config: SourceVerticalConfig,
  sourceId: string,
): SourcePlaylistState {
  return {
    sourceId,
    fakeHash: `${config.ids.fakeHashPrefix}${sourceId}`,
    phase: 'fresh',
    rawResults: [],
    rows: [],
    discoveryProgress: 0,
    spotifyMatches: 0,
    spotifyTotal: 0,
  };
}

/** The payload shape shared by discovery frames (socket) and status polls. */
export interface DiscoveryPayload {
  error?: string;
  phase?: string;
  progress?: number;
  spotify_matches?: number;
  spotify_total?: number;
  complete?: boolean;
  results?: RawDiscoveryResult[];
}

/**
 * Apply a discovery frame or status payload. Both transports carried the same
 * fields in the vanilla (the socket callback at 630-670 and the poll at
 * 694-770 differ only in their transforms — unified in -sync.transform).
 * Completion: `complete`, or phase 'discovered' (the LB poll also completes on
 * that, 11103ff) — the phase echoes the backend when present.
 */
export function applyDiscovery(
  state: SourcePlaylistState,
  config: SourceVerticalConfig,
  payload: DiscoveryPayload,
): SourcePlaylistState {
  const results = payload.results ?? state.rawResults;
  const rows =
    payload.results !== undefined
      ? toDiscoveryRows(config.id, config.ux.foundVariant, payload.results)
      : state.rows;
  const complete = Boolean(payload.complete || payload.phase === 'discovered');
  return {
    ...state,
    phase: payload.phase ?? (complete ? 'discovered' : state.phase),
    rawResults: results,
    rows,
    discoveryProgress: payload.progress ?? state.discoveryProgress,
    spotifyMatches: payload.spotify_matches ?? state.spotifyMatches,
    spotifyTotal: payload.spotify_total ?? state.spotifyTotal,
  };
}

/**
 * Record a successful sync start: capture the engine's sync playlist id
 * (beatport answers `sync_id`, everyone else `sync_playlist_id` — quirk at
 * 5371-5557) and flip to syncing.
 */
export function applySyncStarted(
  state: SourcePlaylistState,
  response: SyncStartResponse,
): SourcePlaylistState {
  return {
    ...state,
    phase: 'syncing',
    syncPlaylistId: response.sync_playlist_id || response.sync_id || state.syncPlaylistId,
  };
}

/**
 * Apply a sync status payload. finished → sync_complete; error/cancelled →
 * REVERT to discovered (the Tidal-shaped machinery, 976-1157: results are
 * still good, only the sync failed). In-flight payloads land in
 * lastSyncProgress, which the card re-render restores (1159).
 * The converted-id ||-keep is defensive: no current status endpoint returns
 * converted_spotify_playlist_id (the vanilla's 5476 assigns undefined), but a
 * payload that does carry one wins over the stored value.
 */
export function applySyncStatus(
  state: SourcePlaylistState,
  payload: SourceSyncStatusResponse,
): SourcePlaylistState {
  const status = payload.status || payload.sync_status;
  // The two transports signal completion differently: the socket frame says
  // status 'finished' (1030); the HTTP poll sets the BOOLEAN complete (1078).
  // No transport emits a 'complete' STRING — deliberately not accepted.
  if (payload.complete || status === 'finished') {
    return {
      ...state,
      phase: 'sync_complete',
      convertedSpotifyPlaylistId:
        payload.converted_spotify_playlist_id || state.convertedSpotifyPlaylistId,
    };
  }
  if (status === 'error' || status === 'cancelled') {
    return { ...state, phase: 'discovered' };
  }
  if (payload.progress) {
    return { ...state, phase: 'syncing', lastSyncProgress: payload.progress };
  }
  return state;
}

/**
 * Modal/listing sync percent per the source's formula: 'processed' =
 * (matched+failed)/total (the shared painter); 'matched' = matched/total
 * (beatport 5481 + the LB listing 11313). Total 0 → 0.
 */
export function syncPercent(
  formula: 'processed' | 'matched',
  progress: SyncProgressSnapshot | undefined,
): number {
  const total = progress?.total_tracks ?? 0;
  if (!total) return 0;
  const matched = progress?.matched_tracks ?? 0;
  const failed = progress?.failed_tracks ?? 0;
  const numerator = formula === 'matched' ? matched : matched + failed;
  return Math.round((numerator / total) * 100);
}

/**
 * THE unified close-reset — one function for the seven per-source blocks in
 * closeYouTubeDiscoveryModal (10237-10455). Runs only from
 * sync_complete/download_complete (the caller gates, as the vanilla does):
 * preserve the discovery outcome (playlist, results, matches, progress,
 * converted id), drop the download linkage, land on 'discovered'. The
 * matching backend write is the caller's updateSourcePhase(config, id,
 * {phase:'discovered'}) — endpoint drift handled by the config.
 */
export function resetAfterModalClose(state: SourcePlaylistState): SourcePlaylistState {
  return {
    ...state,
    phase: 'discovered',
    downloadProcessId: undefined,
    syncPlaylistId: undefined,
    lastSyncProgress: undefined,
  };
}

/**
 * Hydrate from a backend state payload (loadTidalPlaylistStatesFromBackend
 * 775-949 and its clones): tolerant of the camel/snake dual keys the vanilla
 * stored side by side, results re-transformed through the unified mapper.
 */
export function fromBackendState(
  config: SourceVerticalConfig,
  sourceId: string,
  backend: Record<string, unknown>,
): SourcePlaylistState {
  const pick = <T>(...keys: string[]): T | undefined => {
    for (const key of keys) {
      if (backend[key] !== undefined && backend[key] !== null) return backend[key] as T;
    }
    return undefined;
  };

  const rawResults =
    pick<RawDiscoveryResult[]>('discovery_results', 'discoveryResults', 'results') ?? [];
  const playlist = pick<Record<string, unknown>>('playlist');
  const trackCount =
    pick<number>('track_count') ??
    (Array.isArray(playlist?.tracks) ? (playlist.tracks as unknown[]).length : undefined);

  return {
    sourceId,
    fakeHash: `${config.ids.fakeHashPrefix}${sourceId}`,
    phase: pick<string>('phase') ?? 'fresh',
    playlist,
    rawResults,
    rows: toDiscoveryRows(config.id, config.ux.foundVariant, rawResults),
    discoveryProgress: pick<number>('discovery_progress', 'discoveryProgress') ?? 0,
    spotifyMatches: pick<number>('spotify_matches', 'spotifyMatches') ?? 0,
    spotifyTotal: pick<number>('spotify_total', 'spotifyTotal') ?? trackCount ?? 0,
    convertedSpotifyPlaylistId: pick<string>(
      'converted_spotify_playlist_id',
      'convertedSpotifyPlaylistId',
    ),
    downloadProcessId: pick<string>('download_process_id', 'downloadProcessId'),
    syncPlaylistId: pick<string>('sync_playlist_id', 'syncPlaylistId'),
    wingIt: pick<boolean>('wing_it', 'wingIt'),
  };
}
