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
import type { MirrorPayload } from './-sync.import';
import type { SourceVerticalConfig } from './-sync.sources';
import type { RawDiscoveryResult } from './-sync.transform';

import { isServiceExport } from './-sync.export';

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
 * listenbrainz sends the whole playlist (sync-services.js 11276), beatport
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

/** POST /api/wishlist/cleanup (cleanupWishlist, sync-services.js 4114). */
export async function postWishlistCleanup(): Promise<{
  success?: boolean;
  removed_count?: number;
  processed_count?: number;
  error?: string;
}> {
  return readJson(await fetch('/api/wishlist/cleanup', { method: 'POST' }));
}

/** POST /api/wishlist/clear (clearWishlist, sync-services.js 4176). */
export async function postWishlistClear(): Promise<{ success?: boolean; error?: string }> {
  return readJson(await fetch('/api/wishlist/clear', { method: 'POST' }));
}

/** GET /api/listenbrainz/series-detect (rotating-series collapse, 11009). */
export async function detectLbSeries(
  title: string,
): Promise<{ matched?: boolean; series_id?: string; canonical_name?: string }> {
  return readJson(
    await fetch(`/api/listenbrainz/series-detect?title=${encodeURIComponent(title)}`),
  );
}

/* ── URL-import parse endpoints ───────────────────────────────────────────── */

/** POST /api/youtube/parse (parseYouTubePlaylist, sync-services.js 8806). */
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

/** POST /api/spotify/parse-public (parseSpotifyPublicUrl, sync-services.js 6611). */
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

/** POST /api/itunes-link/parse (parseITunesLinkUrl, sync-services.js 7633). */
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

/** DELETE /api/youtube/delete/<hash> (removeYouTubePlaylistFromBackend, 1541). */
export async function deleteYouTubePlaylist(urlHash: string): Promise<Response> {
  return fetch(`/api/youtube/delete/${urlHash}`, { method: 'DELETE' });
}

/** DELETE /api/beatport/charts/delete/<hash> (clearBeatportPlaylists, 4275). */
export async function deleteBeatportChart(chartHash: string): Promise<Response> {
  return fetch(`/api/beatport/charts/delete/${chartHash}`, { method: 'DELETE' });
}
