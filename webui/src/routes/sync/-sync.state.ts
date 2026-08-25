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
import type { RetryDiscoveryBaseline } from './-sync.core';
import type { SourceVerticalConfig } from './-sync.sources';
import type { DiscoveryRow, RawDiscoveryResult } from './-sync.transform';

import { formatDuration } from './-sync.core';
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
  /**
   * The #815 Retry-Failed baseline. The vanilla stamps `_retryDiscovery` on
   * this same state object (retryFailedMirroredDiscovery, stats-automations.js
   * 2183-2186) and _discoveryCompleteToast consumes and DELETES it (9198), so
   * the next plain discovery reports plainly. Mirrored-only in practice.
   */
  retryDiscovery?: RetryDiscoveryBaseline;
  /**
   * The Auto-Sync pipeline overlay, mirrored-only (auto-sync.js 2451-2460).
   * Snake-cased because these ARE the vanilla's key names on the same state
   * object, and the mirrored phase line reads them back by those names.
   */
  pipeline_status?: string;
  pipeline_progress?: number;
  pipeline_phase?: string;
  pipeline_error?: string;
  pipeline_log?: string[];
  pipeline_result?: unknown;
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

/** A fixed-match track, as the fix modal hands it over. */
export interface FixedMatchTrack {
  id?: string;
  name?: string;
  artists?: (string | { name?: string })[] | string;
  album?: string | Record<string, unknown>;
  duration_ms?: number;
  image_url?: string | null;
}

/**
 * Apply a confirmed manual match — the frontend half of
 * selectDiscoveryFixTrack (wishlist-tools.js 476-541): the raw result becomes
 * found + manual_match with a spotify_data whose album is ALWAYS an object
 * (cover art for the download pipeline, 497-521); the matches counter
 * increments only when the row was previously unmatched, and the progress
 * repaints as matches/total (524-541 — the vanilla reuses the discovery bar
 * for match percentage after a fix).
 */
export function applyFixedMatch(
  state: SourcePlaylistState,
  config: SourceVerticalConfig,
  trackIndex: number,
  track: FixedMatchTrack,
): SourcePlaylistState {
  const previous = state.rawResults[trackIndex];
  if (!previous) return state;
  const wasNotFound = previous.status !== 'found' && previous.status_class !== 'found';

  const imageUrl = track.image_url || '';
  let albumObject: Record<string, unknown>;
  if (track.album && typeof track.album === 'object') {
    albumObject = { ...track.album };
    if (imageUrl && !albumObject.image_url) albumObject.image_url = imageUrl;
    if (imageUrl && !albumObject.images) albumObject.images = [{ url: imageUrl }];
  } else {
    albumObject = { name: track.album || '' };
    if (imageUrl) {
      albumObject.image_url = imageUrl;
      albumObject.images = [{ url: imageUrl }];
    }
  }

  const rawResults = state.rawResults.slice();
  rawResults[trackIndex] = {
    ...previous,
    status: '✅ Found',
    status_class: 'found',
    manual_match: true,
    wing_it_fallback: false,
    spotify_id: track.id,
    // LB rows display the raw result's duration (490-492).
    duration: formatDuration(track.duration_ms ?? 0),
    spotify_data: {
      id: track.id,
      name: track.name,
      artists: track.artists,
      album: albumObject,
      duration_ms: track.duration_ms,
      image_url: imageUrl,
    } as RawDiscoveryResult['spotify_data'],
  };

  const spotifyMatches = wasNotFound ? (state.spotifyMatches || 0) + 1 : state.spotifyMatches;
  const total =
    state.spotifyTotal ||
    (Array.isArray(state.playlist?.tracks) ? (state.playlist.tracks as unknown[]).length : 0);

  return {
    ...state,
    rawResults,
    rows: toDiscoveryRows(config.id, config.ux.foundVariant, rawResults),
    spotifyMatches,
    // discoveryProgress deliberately untouched: it is the SCAN percent. the
    // vanilla overwrote it with a match ratio here (unclamped - "105/100,
    // over 100%"), which the display now derives itself, clamped, in
    // matchLineNumbers. total stays computed above for parity with the
    // vanilla's guard even though nothing else reads it now.
    discoveryProgress: state.discoveryProgress,
  };
}

/**
 * Apply a confirmed unmatch — the frontend half of unmatchDiscoveryTrack
 * (wishlist-tools.js 676-688): the row reverts to not-found with everything
 * matched cleared. NOTE the vanilla does NOT decrement the matches counter —
 * transcribed as-is.
 */
export function applyUnmatched(
  state: SourcePlaylistState,
  config: SourceVerticalConfig,
  trackIndex: number,
): SourcePlaylistState {
  const previous = state.rawResults[trackIndex];
  if (!previous) return state;
  const rawResults = state.rawResults.slice();
  rawResults[trackIndex] = {
    ...previous,
    status: '❌ Not Found',
    status_class: 'not-found',
    spotify_track: undefined,
    spotify_artist: undefined,
    spotify_album: undefined,
    spotify_data: null,
    // Knowing improvement: the vanilla leaves spotify_id stale here.
    spotify_id: undefined,
    matched_data: null,
    confidence: 0,
    wing_it_fallback: false,
    manual_match: false,
  };
  return {
    ...state,
    rawResults,
    rows: toDiscoveryRows(config.id, config.ux.foundVariant, rawResults),
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
  // Beatport's status/list endpoints name it chart_data, not playlist
  // (web_server.py 37340, 38297) — the vanilla renames it at 5123-5127.
  const playlist = pick<Record<string, unknown>>('playlist', 'chart_data');
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
    // The states rows carry the live sync counters (endpoints.py 239) and the
    // card's sync line is painted from them (updateTidalCardSyncProgress
    // 1163 keeps them on the state for exactly this restore).
    lastSyncProgress: pick<SyncProgressSnapshot>('sync_progress', 'syncProgress'),
    wingIt: pick<boolean>('wing_it', 'wingIt'),
  };
}
