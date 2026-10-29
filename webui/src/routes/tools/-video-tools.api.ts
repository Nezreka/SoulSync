/**
 * the video tools tab's endpoints. the maintenance hero goes through
 * -tools.api.ts (repairUrl picks the side); these are the cards that only the
 * video side has: library scan, server scan, backups, mass rename.
 *
 * raw fetch like the rest of the tools page. a status poll that fails leaves
 * the card as it was instead of throwing it into an error state.
 */

const JSON_HEADERS = { 'Content-Type': 'application/json', Accept: 'application/json' } as const;

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const response = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

async function sendJson<T>(url: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    data = { success: false, error: `HTTP ${response.status}` };
  }
  return data as T;
}

// ── library ──────────────────────────────────────────────────────────────────

export interface VideoLibraryStats {
  movies: number;
  shows: number;
  episodes: number;
  sizeBytes: number;
  /** 'plex' | 'jellyfin' | null. names the scan cards like music's. */
  server: string | null;
}

export async function fetchVideoLibraryStats(): Promise<VideoLibraryStats | null> {
  const data = await getJson<{
    library?: { movies?: number; shows?: number; episodes?: number; size_bytes?: number };
    server?: string | null;
    error?: string;
  }>('/api/video/dashboard');
  if (!data || data.error) return null;
  const lib = data.library || {};
  return {
    movies: lib.movies || 0,
    shows: lib.shows || 0,
    episodes: lib.episodes || 0,
    sizeBytes: lib.size_bytes || 0,
    server: data.server || null,
  };
}

// ── library scan (soulsync reads the server) ─────────────────────────────────

export type VideoScanMode = 'incremental' | 'full' | 'deep';
export type VideoMediaTarget = 'all' | 'movie' | 'show';

export interface VideoScanStatus {
  state: string;
  phase?: string | null;
  mode?: string | null;
  media_type?: string | null;
  movies?: number;
  shows?: number;
  episodes?: number;
  percent?: number | null;
  removed?: number;
  error?: string | null;
  last_scan_at?: string | null;
}

export function fetchVideoScanStatus(): Promise<VideoScanStatus | null> {
  return getJson<VideoScanStatus>('/api/video/scan/status');
}

export async function stopVideoScan(): Promise<void> {
  try {
    await fetch('/api/video/scan/stop', {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });
  } catch {
    // the status poll reconciles
  }
}

// ── server scan (the server rescans its own folders) ─────────────────────────

export async function requestServerScan(
  mediaType: VideoMediaTarget,
): Promise<{ ok?: boolean; error?: string }> {
  try {
    return await sendJson('/api/video/scan/server', 'POST', { media_type: mediaType });
  } catch {
    return { ok: false, error: 'Could not reach server' };
  }
}

/** {scanning: true|false|null}. null = the server can't say. */
export async function fetchServerScanStatus(
  mediaType: VideoMediaTarget,
): Promise<{ scanning: boolean | null } | null> {
  return getJson(`/api/video/scan/server/status?media_type=${encodeURIComponent(mediaType)}`);
}

// ── backups ──────────────────────────────────────────────────────────────────

export interface VideoBackup {
  name: string;
  created_at?: string | null;
  size_bytes: number;
}

export interface VideoBackupList {
  success: boolean;
  backups: VideoBackup[];
  pending_restore: boolean;
}

export async function fetchVideoBackups(): Promise<VideoBackupList | null> {
  const data = await getJson<VideoBackupList>('/api/video/backups');
  if (!data || !data.success) return null;
  return { success: true, backups: data.backups || [], pending_restore: !!data.pending_restore };
}

export function createVideoBackup(): Promise<{ success?: boolean; error?: string }> {
  return sendJson('/api/video/backups', 'POST');
}

export function stageVideoRestore(name: string): Promise<{ success?: boolean; error?: string }> {
  return sendJson('/api/video/backups/restore', 'POST', { name });
}

export async function cancelVideoRestore(): Promise<void> {
  try {
    await fetch('/api/video/backups/restore', { method: 'DELETE' });
  } catch {
    // the list reload shows whatever is true
  }
}

export function videoBackupDownloadUrl(name: string): string {
  return `/api/video/backups/${encodeURIComponent(name)}/download`;
}

// ── mass rename ──────────────────────────────────────────────────────────────

export interface RenameEntry {
  title: string;
  current: string;
  proposed: string;
}

export interface RenamePreview {
  success: boolean;
  /** false while the server worker is still resolving paths. */
  ready?: boolean;
  done?: number;
  total?: number;
  entries?: RenameEntry[];
  unresolved?: number;
  error?: string;
}

export async function startRenamePreview(): Promise<RenamePreview> {
  try {
    return await sendJson<RenamePreview>('/api/video/organization/rename/preview', 'GET');
  } catch {
    return { success: false, error: 'could not reach the server' };
  }
}

export async function fetchRenamePreviewStatus(): Promise<RenamePreview> {
  try {
    return await sendJson<RenamePreview>('/api/video/organization/rename/preview/status', 'GET');
  } catch {
    return { success: false, error: 'could not reach the server' };
  }
}

export function applyRenames(): Promise<{
  success?: boolean;
  renamed?: number;
  skipped?: number;
  error?: string;
}> {
  return sendJson('/api/video/organization/rename/apply', 'POST', {});
}

// ── formatting ───────────────────────────────────────────────────────────────

/** bytes -> "1.2 TB" / "340.0 GB" / "12.5 MB", the vanilla card's units. */
export function formatLibrarySize(bytes: number): string {
  const mb = (Number(bytes) || 0) / 1048576;
  if (mb >= 1048576) return `${(mb / 1048576).toFixed(2)} TB`;
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  return `${mb.toFixed(1)} MB`;
}

/** what a library scan read, for the progress line. a movies-only scan
 *  doesn't print "0 shows". */
export function scanCountsLabel(status: VideoScanStatus): string {
  const target = status.media_type || 'all';
  if (target === 'movie') return `${status.movies || 0} movies`;
  if (target === 'show') return `${status.shows || 0} shows, ${status.episodes || 0} episodes`;
  return `${status.movies || 0} movies, ${status.shows || 0} shows`;
}

export function serverLabel(server: string | null | undefined): string {
  if (!server) return '';
  return server.charAt(0).toUpperCase() + server.slice(1);
}

export function backupSizeLabel(bytes: number): string {
  return bytes >= 1e6 ? `${Math.round(bytes / 1e5) / 10} MB` : `${Math.round(bytes / 1e3)} KB`;
}

export function mediaTargetLabel(target: VideoMediaTarget): string {
  return target === 'movie' ? 'Movies' : target === 'show' ? 'TV' : 'Movies + TV';
}
