/**
 * Sync page api layer.
 *
 * Two halves: config-driven calls that take a SourceVerticalConfig (the nine
 * verticals share one shape and differ only through -sync.sources.ts), and the
 * page-level endpoints (account playlists, mirror contract, M3U, wishlist
 * maintenance). Response shapes are duck-typed the way the vanilla read them —
 * the backend predates the port and is not being re-contracted here.
 */

import type { ExportJob, ExportMode, ExportStartResponse } from './-sync.export';
import type { SyncHistoryEntry, SyncHistoryResyncTrack } from './-sync.history';
import type { MirrorPayload } from './-sync.import';
import type { MirroredPipelineState } from './-sync.pipeline';
import type { SourceVerticalConfig } from './-sync.sources';
import type { RawDiscoveryResult } from './-sync.transform';

import { isServiceExport } from './-sync.export';
import {
  PIPELINE_RUN_FAILED,
  PIPELINE_STATUS_FAILED,
  SOURCE_REF_FAILED,
  pipelineResponseError,
} from './-sync.pipeline';

/* ── Shared response shapes (fields the vanilla actually read) ────────────── */

export interface DiscoveryStatusResponse {
  error?: string;
  phase?: string;
  progress?: number;
  spotify_matches?: number;
  spotify_total?: number;
  complete?: boolean;
  results?: RawDiscoveryResult[];
}

export interface SourceSyncStatusResponse {
  error?: string;
  status?: string;
  sync_status?: string;
  /** The HTTP poll's completion signal is this BOOLEAN (tidal poll, 1078). */
  complete?: boolean;
  progress?: {
    progress?: number;
    total_tracks?: number;
    matched_tracks?: number;
    failed_tracks?: number;
    current_step?: string;
    current_track?: string;
  };
  converted_spotify_playlist_id?: string;
}

export interface SyncStartResponse {
  error?: string;
  sync_id?: string;
  sync_playlist_id?: string;
}

async function readJson<T>(response: Response): Promise<T> {
  return (await response.json()) as T;
}

/* ── Config-driven vertical calls ─────────────────────────────────────────── */

/**
 * POST discovery/start. The body follows the source's startBody policy:
 * listenbrainz sends the whole playlist (sync-services.js 11302), beatport
 * sends {chart_data} (4648), everyone else sends nothing.
 */
export async function startSourceDiscovery(
  config: SourceVerticalConfig,
  id: string,
  body?: unknown,
): Promise<{ error?: string }> {
  const init: RequestInit = { method: 'POST' };
  if (config.discovery.startBody !== 'none' && body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(
      config.discovery.startBody === 'chart_data' ? { chart_data: body } : body,
    );
  }
  return readJson(await fetch(config.api.discoveryStart(id), init));
}

export async function fetchSourceDiscoveryStatus(
  config: SourceVerticalConfig,
  id: string,
): Promise<DiscoveryStatusResponse> {
  return readJson(await fetch(config.api.discoveryStatus(id)));
}

export async function startSourceSync(
  config: SourceVerticalConfig,
  id: string,
): Promise<SyncStartResponse> {
  return readJson(await fetch(config.api.syncStart(id), { method: 'POST' }));
}

export async function cancelSourceSync(
  config: SourceVerticalConfig,
  id: string,
): Promise<{ error?: string }> {
  return readJson(await fetch(config.api.syncCancel(id), { method: 'POST' }));
}

export async function fetchSourceSyncStatus(
  config: SourceVerticalConfig,
  id: string,
): Promise<SourceSyncStatusResponse> {
  return readJson(await fetch(config.api.syncStatus(id)));
}

/**
 * POST the phase write. Extra fields ride along (beatport persists
 * converted_spotify_playlist_id this way, 5592; its reset sends
 * {phase:'fresh', reset:true}, 10837).
 */
export async function updateSourcePhase(
  config: SourceVerticalConfig,
  id: string,
  payload: { phase: string } & Record<string, unknown>,
): Promise<Response> {
  return fetch(config.api.updatePhase(id), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** Full-state fetch (discovery results, phase, converted id...). */
export async function fetchSourceState(
  config: SourceVerticalConfig,
  id: string,
): Promise<Record<string, unknown>> {
  if (!config.api.state) return {};
  return readJson(await fetch(config.api.state(id)));
}

/* ── Page-level endpoints ─────────────────────────────────────────────────── */

/** GET /api/active-processes (checkForActiveProcesses, sync-spotify.js 77). */
export async function fetchActiveProcesses(): Promise<{
  active_processes: { type?: string; playlist_id?: string; batch_id?: string }[];
}> {
  const response = await fetch('/api/active-processes');
  if (!response.ok) return { active_processes: [] };
  return readJson(response);
}

/** GET /api/spotify/playlists (loadSpotifyPlaylists, sync-spotify.js 1598). */
export async function fetchSpotifyPlaylists(): Promise<unknown[]> {
  const data = await readJson<unknown>(await fetch('/api/spotify/playlists'));
  return Array.isArray(data) ? data : ((data as { playlists?: unknown[] })?.playlists ?? []);
}

/** GET /api/deezer/arl-status (initializeSyncPage deezer tab, 3698). */
export async function fetchDeezerArlStatus(): Promise<{ authenticated?: boolean }> {
  return readJson(await fetch('/api/deezer/arl-status'));
}

/** GET /api/deezer/arl-playlists (loadDeezerArlPlaylists, sync-services.js 2437). */
export async function fetchDeezerArlPlaylists(): Promise<unknown[]> {
  const data = await readJson<unknown>(await fetch('/api/deezer/arl-playlists'));
  return Array.isArray(data) ? data : ((data as { playlists?: unknown[] })?.playlists ?? []);
}

/** A playlist's tracks, as both account tabs' details modals read them. */
export interface AccountPlaylistTracks {
  error?: string;
  name?: string;
  owner?: string;
  description?: string;
  image_url?: string;
  track_count?: number;
  tracks?: {
    id?: string;
    name?: string;
    artists?: unknown;
    duration_ms?: number;
  }[];
}

/**
 * GET /api/spotify/playlist/<id> (openPlaylistDetailsModal's inline fallback,
 * sync-spotify.js 1861). The vanilla prefers the optional global
 * fetchAndCacheSpotifyPlaylistTracks when wishlist-tools has defined it; the
 * port owns its own cache, so it always takes this path.
 */
export async function fetchSpotifyPlaylistTracks(
  playlistId: string,
): Promise<AccountPlaylistTracks> {
  return readJson(await fetch(`/api/spotify/playlist/${playlistId}`));
}

/**
 * GET /api/deezer/arl-playlist/<id> (2557). NOTE the path takes the RAW deezer
 * id, not the `deezer_arl_` prefixed one — the prefix is a client-side id space
 * only (2540, 2557).
 */
export async function fetchDeezerArlPlaylistTracks(
  playlistId: string,
): Promise<AccountPlaylistTracks> {
  return readJson(await fetch(`/api/deezer/arl-playlist/${playlistId}`));
}

/** GET /api/sync/status/<id> — the ACCOUNT sync engine's status, not a vertical's. */
export async function fetchAccountSyncStatus(
  playlistId: string,
): Promise<SourceSyncStatusResponse> {
  return readJson(await fetch(`/api/sync/status/${playlistId}`));
}

/** POST /api/mirror-playlist — the payload comes from buildMirrorPayload. */
export async function postMirrorPlaylist(
  payload: MirrorPayload,
): Promise<{ success?: boolean; playlist_id?: number; error?: string }> {
  return readJson(
    await fetch('/api/mirror-playlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

/**
 * POST /api/generate-playlist-m3u (autoSavePlaylistM3U / exportPlaylistAsM3U,
 * sync-spotify.js 2441 + 2560). The server enforces m3u_export.enabled for
 * auto-saves; exports pass force:true.
 */
export async function generatePlaylistM3u(body: {
  playlist_name: string;
  tracks: { name: string; artist: string; duration_ms: number }[];
  context_type: 'playlist' | 'album';
  artist_name?: string;
  album_name?: string;
  year?: string;
  save_to_disk?: boolean;
  force?: boolean;
}): Promise<Response> {
  return fetch('/api/generate-playlist-m3u', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** POST /api/wishlist/cleanup (cleanupWishlist, sync-services.js 4140). */
export async function postWishlistCleanup(): Promise<{
  success?: boolean;
  removed_count?: number;
  processed_count?: number;
  error?: string;
}> {
  return readJson(await fetch('/api/wishlist/cleanup', { method: 'POST' }));
}

/** POST /api/wishlist/clear (clearWishlist, sync-services.js 4202). */
export async function postWishlistClear(): Promise<{ success?: boolean; error?: string }> {
  return readJson(await fetch('/api/wishlist/clear', { method: 'POST' }));
}

/** GET /api/listenbrainz/series-detect (rotating-series collapse, 11009). */
export async function detectLbSeries(
  title: string,
): Promise<{ matched?: boolean; source?: string; series_id?: string; canonical_name?: string }> {
  // `source` is read at 10991 and was missing from this type — the vanilla lets
  // a series match rewrite the mirror's source, not just its id and name.
  return readJson(
    await fetch(`/api/listenbrainz/series-detect?title=${encodeURIComponent(title)}`),
  );
}

/* ── URL-import parse endpoints ───────────────────────────────────────────── */

/** POST /api/youtube/parse (parseYouTubePlaylist, sync-services.js 8832). */
export async function parseYouTubeUrl(
  url: string,
): Promise<{ error?: string; url_hash?: string; playlist_name?: string; track_count?: number }> {
  return readJson(
    await fetch('/api/youtube/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    }),
  );
}

/** POST /api/spotify/parse-public (parseSpotifyPublicUrl, sync-services.js 6637). */
export async function parseSpotifyPublicUrl(
  url: string,
): Promise<{ error?: string; url_hash?: string; type?: string; name?: string; subtitle?: string }> {
  return readJson(
    await fetch('/api/spotify/parse-public', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    }),
  );
}

/** POST /api/itunes-link/parse (parseITunesLinkUrl, sync-services.js 7659). */
export async function parseITunesLinkUrl(
  url: string,
): Promise<{ error?: string; url_hash?: string; type?: string; name?: string }> {
  return readJson(
    await fetch('/api/itunes-link/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    }),
  );
}

/**
 * GET /api/deezer/playlist/<id> (the Deezer-link parse head, 2706). Throws
 * the backend's error message on !ok — the vanilla's 2746-2749 throw, which
 * lands in the tab's catch toast.
 */
export async function fetchDeezerLinkPlaylist(id: string): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/deezer/playlist/${id}`);
  if (!response.ok) {
    const error = await readJson<{ error?: string }>(response);
    throw new Error(error.error || 'Failed to fetch Deezer playlist');
  }
  return readJson(response);
}

/** GET /api/youtube/playlists (loadYouTubePlaylistsFromBackend, sync-spotify.js 695). */
export async function fetchYouTubePlaylists(): Promise<Record<string, unknown>[]> {
  const response = await fetch('/api/youtube/playlists');
  if (!response.ok) {
    const error = await readJson<{ error?: string }>(response);
    throw new Error(error.error || 'Failed to fetch YouTube playlists');
  }
  const data = await readJson<{ playlists?: Record<string, unknown>[] }>(response);
  return data.playlists ?? [];
}

/* ── Source playlist lists ────────────────────────────────────────────────── */

/**
 * GET /api/tidal/playlists | /api/qobuz/playlists (vertical heads). Throws
 * the backend error on !ok (the vanilla's sync-services.js 14-17 throw,
 * which lands in the tab's ❌ placeholder + toast).
 */
export async function fetchSourcePlaylists(base: 'tidal' | 'qobuz'): Promise<unknown[]> {
  const response = await fetch(`/api/${base}/playlists`);
  if (!response.ok) {
    const error = await readJson<{ error?: string }>(response);
    throw new Error(
      error.error || `Failed to fetch ${base === 'tidal' ? 'Tidal' : 'Qobuz'} playlists`,
    );
  }
  const data = await readJson<unknown>(response);
  return Array.isArray(data) ? data : ((data as { playlists?: unknown[] })?.playlists ?? []);
}

/** GET /api/tidal/playlist/<id> | /api/qobuz/playlist/<id> — the on-demand
 * track fetch the account verticals run per playlist (sync-services.js 39,
 * 1550, 1653). */
export async function fetchAccountPlaylist(
  base: 'tidal' | 'qobuz',
  id: string,
): Promise<Record<string, unknown>> {
  return readJson(await fetch(`/api/${base}/playlist/${id}`));
}

/** GET the bulk state hydration for a vertical, when it has one. */
export async function fetchSourcePlaylistsStates(
  config: SourceVerticalConfig,
): Promise<Record<string, unknown> | unknown[]> {
  if (!config.api.playlistsStates) return {};
  return readJson(await fetch(config.api.playlistsStates));
}

/* ── Mirrored playlists (stats-automations.js) ────────────────────────────── */

/** GET /api/mirrored-playlists (loadMirroredPlaylists, 500-524). */
export async function fetchMirroredPlaylists(): Promise<Record<string, unknown>[]> {
  const data = await readJson<unknown>(await fetch('/api/mirrored-playlists'));
  if (!Array.isArray(data)) {
    const err = (data as { error?: string } | null)?.error;
    throw new Error(err || 'Failed to load mirrored playlists');
  }
  return data as Record<string, unknown>[];
}

/** POST .../clear-discovery (clearMirroredDiscovery, 1175). */
export async function clearMirroredDiscovery(
  playlistId: number | string,
): Promise<{ success?: boolean; cleared?: number; error?: string }> {
  return readJson(
    await fetch(`/api/mirrored-playlists/${playlistId}/clear-discovery`, { method: 'POST' }),
  );
}

/** One mirrored playlist with its tracks (openMirroredPlaylistModal, 1069). */
export interface MirroredPlaylistDetail {
  error?: string;
  name?: string;
  source?: string;
  source_playlist_id?: string;
  source_ref?: string;
  description?: string;
  owner?: string;
  image_url?: string;
  updated_at?: string;
  mirrored_at?: string;
  tracks?: {
    id?: number;
    position?: number;
    track_name?: string;
    artist_name?: string;
    album_name?: string;
    duration_ms?: number;
    image_url?: string;
    source_track_id?: string;
  }[];
}

/** GET /api/mirrored-playlists/<id> (1069). */
export async function fetchMirroredPlaylist(
  playlistId: number | string,
): Promise<MirroredPlaylistDetail> {
  return readJson(await fetch(`/api/mirrored-playlists/${playlistId}`));
}

/**
 * POST /api/mirrored-playlists/<id>/prepare-discovery (2062).
 *
 * Registers the mirrored playlist with the backend so the discovery pipeline
 * can find it. The vanilla POSTs this BEFORE every fresh mirrored discovery;
 * skipping it is why the port's mirrored discovery could not work.
 */
export async function prepareMirroredDiscovery(playlistId: number | string): Promise<{
  error?: string;
  from_cache?: boolean;
  cached_matches?: number;
  total_tracks?: number;
}> {
  return readJson(
    await fetch(`/api/mirrored-playlists/${playlistId}/prepare-discovery`, { method: 'POST' }),
  );
}

/** POST /api/mirrored-playlists/<id>/retry-failed-discovery (2159). */
export async function postRetryFailedDiscovery(
  playlistId: number | string,
): Promise<{ error?: string; retry_count?: number }> {
  return readJson(
    await fetch(`/api/mirrored-playlists/${playlistId}/retry-failed-discovery`, { method: 'POST' }),
  );
}

/** DELETE /api/mirrored-playlists/<id> (deleteMirroredPlaylist, 2023). */
export async function deleteMirroredPlaylist(
  playlistId: number | string,
): Promise<{ success?: boolean; error?: string }> {
  return readJson(await fetch(`/api/mirrored-playlists/${playlistId}`, { method: 'DELETE' }));
}

/** PATCH .../custom-name (editMirroredCustomName, auto-sync.js 2389). */
export async function patchMirroredCustomName(
  playlistId: number | string,
  customName: string,
): Promise<{ error?: string }> {
  const response = await fetch(`/api/mirrored-playlists/${playlistId}/custom-name`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ custom_name: customName }),
  });
  const data = await readJson<{ error?: string }>(response);
  if (!response.ok || data.error) throw new Error(data.error || 'Failed to update name');
  return data;
}

/* ── Auto-Sync pipeline + source ref (auto-sync.js 2358-2525) ─────────────── */

/**
 * The pipeline endpoints' shared reader (parseMirroredPipelineResponse, 2358).
 * The body is read as TEXT first so an empty one can pass and an unparseable
 * one can be blamed on a stale server; the branch logic is pure and pinned
 * differentially in -sync.pipeline.test.ts.
 */
async function readPipelineResponse(
  response: Response,
  fallback: string,
): Promise<Record<string, unknown>> {
  const text = await response.text();
  const error = pipelineResponseError(response.ok, response.status, text, fallback);
  if (error) throw new Error(error);
  return text ? (JSON.parse(text) as Record<string, unknown>) : {};
}

/**
 * POST .../server-link — record which server playlist this mirror is.
 *
 * The relationship is a NAME match made fresh on every visit today, which is
 * why the disambiguation modal exists. This stores the answer once the server
 * tab has resolved it.
 *
 * WRITE ONLY for now: nothing reads the columns yet. Fire-and-forget, and
 * deliberately swallowing its own failure — a link that does not land must
 * never disturb the tab that called it.
 */
export function recordServerLink(
  playlistId: number,
  serverPlaylistId: string,
  serverType: string,
): void {
  void fetch(`/api/mirrored-playlists/${playlistId}/server-link`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ server_playlist_id: serverPlaylistId, server_type: serverType }),
  }).catch(() => undefined);
}

/** POST .../pipeline/run — an empty JSON body, as the vanilla sends (2468-2472). */
export async function runMirroredPipeline(
  playlistId: number | string,
): Promise<{ state?: MirroredPipelineState }> {
  return readPipelineResponse(
    await fetch(`/api/mirrored-playlists/${playlistId}/pipeline/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    }),
    PIPELINE_RUN_FAILED,
  );
}

/** GET .../pipeline/status — the whole body IS the state (2495-2497). */
export async function fetchMirroredPipelineStatus(
  playlistId: number | string,
): Promise<MirroredPipelineState> {
  return readPipelineResponse(
    await fetch(`/api/mirrored-playlists/${playlistId}/pipeline/status`),
    PIPELINE_STATUS_FAILED,
  );
}

/** PATCH .../source-ref (editMirroredSourceRef, 2423-2432). */
export async function patchMirroredSourceRef(
  playlistId: number | string,
  sourceRef: string,
): Promise<void> {
  const response = await fetch(`/api/mirrored-playlists/${playlistId}/source-ref`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_ref: sourceRef }),
  });
  const data = await readJson<{ error?: string }>(response);
  if (!response.ok || data.error) throw new Error(data.error || SOURCE_REF_FAILED);
}

/* ── Playlist export (#903, stats-automations.js 662-819) ─────────────────── */

/**
 * GET /api/discover/your-albums/sources — the export modal's connection probe
 * (715). The vanilla swallows every failure with `.catch(() => {})`, gating
 * nothing; null is that outcome.
 */
export async function fetchExportConnectedSources(): Promise<string[] | null> {
  try {
    const data = await readJson<{ connected?: string[] }>(
      await fetch('/api/discover/your-albums/sources'),
    );
    return data?.connected || [];
  } catch {
    return null;
  }
}

/**
 * POST the export job. Spotify/Deezer take .../export/service/<mode> with a
 * {backfill} body; ListenBrainz and .jspf take .../export/listenbrainz with a
 * {mode} body (736-741).
 */
export async function startPlaylistExport(
  playlistId: number | string,
  mode: ExportMode,
  backfill: boolean,
): Promise<ExportStartResponse> {
  const isService = isServiceExport(mode);
  const url = isService
    ? `/api/playlists/${playlistId}/export/service/${mode}`
    : `/api/playlists/${playlistId}/export/listenbrainz`;
  return readJson(
    await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(isService ? { backfill: !!backfill } : { mode }),
    }),
  );
}

/** GET /api/playlists/export/status/<jobId> (759). */
export async function fetchPlaylistExportStatus(jobId: string): Promise<{ job?: ExportJob }> {
  return readJson(await fetch(`/api/playlists/export/status/${jobId}`));
}

/**
 * The hard reset (resetYouTubePlaylist 10793, resetBeatportChart 10851).
 *
 * Both vanilla bodies read the error off a NON-ok response and throw its
 * `error` field, falling back to their own message — so a 500 with a body is
 * reported by the backend's words, and one without gets the generic line.
 */
export async function resetSourceDiscovery(
  config: SourceVerticalConfig,
  id: string,
): Promise<void> {
  const url = config.api.reset?.(id);
  if (!url) return;
  const init: RequestInit = { method: 'POST' };
  if (config.api.resetBody === 'fresh-reset') {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify({ phase: 'fresh', reset: true });
  }
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(
      body.error ||
        (config.api.resetBody === 'fresh-reset'
          ? 'Failed to reset Beatport chart'
          : 'Failed to reset playlist'),
    );
  }
}

/** DELETE /api/youtube/delete/<hash> (removeYouTubePlaylistFromBackend, 1541). */
export async function deleteYouTubePlaylist(urlHash: string): Promise<Response> {
  return fetch(`/api/youtube/delete/${urlHash}`, { method: 'DELETE' });
}

/** DELETE /api/beatport/charts/delete/<hash> (clearBeatportPlaylists, 4275). */
export async function deleteBeatportChart(chartHash: string): Promise<Response> {
  return fetch(`/api/beatport/charts/delete/${chartHash}`, { method: 'DELETE' });
}

/* ── Auto-Sync schedule board (auto-sync.js 602-650, 1315-1392, 2051-2331) ── */

/** GET /api/automations (606). */
export async function fetchAutomations(): Promise<Response> {
  return fetch('/api/automations');
}

/**
 * GET /api/playlist-pipeline/history?limit=<n> (609).
 *
 * The limit is a QUERY PARAM, not a client-side slice — 'Load more' raises it
 * and REFETCHES (1251-1254), so the board never holds more than it asked for.
 */
export async function fetchPipelineHistory(limit: number): Promise<Response> {
  return fetch(`/api/playlist-pipeline/history?limit=${limit}`);
}

/**
 * 610-611. Both personalized endpoints are BEST-EFFORT: the vanilla catches
 * their rejection to null and never lets either block the board. Callers must
 * preserve that — a kinds outage must not cost the user their schedule board.
 */
export async function fetchPersonalizedKinds(): Promise<Response | null> {
  return fetch('/api/personalized/kinds').catch(() => null);
}

export async function fetchPersonalizedPlaylists(): Promise<Response | null> {
  return fetch('/api/personalized/playlists').catch(() => null);
}

/** POST /api/automations (2080, create arm). */
export async function createAutomation(payload: unknown): Promise<Response> {
  return fetch('/api/automations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** PUT /api/automations/<id> (2080, update arm — reuses the existing row). */
export async function updateAutomation(
  automationId: number | string,
  payload: unknown,
): Promise<Response> {
  return fetch(`/api/automations/${automationId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** DELETE /api/automations/<id> (2100, 2297, and the two best-effort cleanups). */
export async function deleteAutomation(automationId: number | string): Promise<Response> {
  return fetch(`/api/automations/${automationId}`, { method: 'DELETE' });
}

/**
 * POST /api/automations/<id>/run (2321) — the Run-now path for a SYNTHETIC
 * personalized row, which has no mirrored pipeline to run.
 */
export async function runAutomation(automationId: number | string): Promise<Response> {
  return fetch(`/api/automations/${automationId}/run`, { method: 'POST' });
}

/** PATCH /api/mirrored-playlists/<id>/preferences (1935) — organize-by-playlist. */
export async function patchMirroredPreferences(
  playlistId: number | string,
  preferences: Record<string, unknown>,
): Promise<Response> {
  return fetch(`/api/mirrored-playlists/${playlistId}/preferences`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(preferences),
  });
}

/**
 * GET /api/logs — the sidebar log area's HTTP half (`loadLogs`, api-monitor.js
 * 1114-1127). Returns null on any failure so the caller keeps the text it has,
 * matching the vanilla, which only overwrites the textarea on error when it is
 * still showing its own placeholder.
 */
export async function fetchSyncLogs(): Promise<{ logs?: unknown } | null> {
  try {
    const response = await fetch('/api/logs');
    if (!response.ok) return null;
    return (await response.json()) as { logs?: unknown };
  } catch {
    return null;
  }
}

/**
 * Sync history — the endpoints behind the Activity modal's first tab.
 *
 * `/api/sync/start` and `/api/sync/cancel` are the SAME engine
 * fetchAccountSyncStatus polls, which is why a re-sync reuses that status call
 * rather than adding a third one.
 */
export async function fetchSyncHistory(
  page: number,
  limit: number,
  source: string | null,
): Promise<{
  entries?: SyncHistoryEntry[];
  stats?: Record<string, number>;
  total?: number;
  error?: string;
}> {
  const params = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (source) params.set('source', source);
  return readJson(await fetch(`/api/sync/history?${params}`));
}

/** GET one entry — the only call that returns its stored `tracks`. */
export async function fetchSyncHistoryEntry(
  entryId: number,
): Promise<{ success?: boolean; entry?: SyncHistoryEntry; error?: string }> {
  return readJson(await fetch(`/api/sync/history/${entryId}`));
}

export async function deleteSyncHistoryEntry(
  entryId: number,
): Promise<{ success?: boolean; error?: string }> {
  return readJson(await fetch(`/api/sync/history/${entryId}`, { method: 'DELETE' }));
}

export async function startSync(body: {
  playlist_id: string;
  playlist_name: string;
  tracks: SyncHistoryResyncTrack[];
}): Promise<{ success?: boolean; error?: string }> {
  return readJson(
    await fetch('/api/sync/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  );
}

export async function cancelSync(playlistId: string): Promise<{ success?: boolean }> {
  return readJson(
    await fetch('/api/sync/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_id: playlistId }),
    }),
  );
}
