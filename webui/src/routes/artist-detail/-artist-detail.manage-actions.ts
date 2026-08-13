/**
 * The Enhanced view's track/album actions — the API calls and message shaping
 * behind the smart-delete dialogs, the source-info popover, and the art
 * pickers. Transcribed from library.js (deleteLibraryTrack 3096, deleteLibraryAlbum
 * 4020, showTrackSourceInfo 3192, openAlbumArtPicker 1669, openArtistArtPicker 1836).
 */

export type DeleteTrackChoice = 'db_only' | 'delete_file';
export type DeleteAlbumChoice = 'db_only' | 'delete_files';

export interface ActionToast {
  message: string;
  tone: 'success' | 'warning' | 'error';
  /** A second, longer-lived toast the vanilla showed for file errors (3122). */
  extra?: string;
}

/** DELETE /api/library/track/<id>[?delete_file=true] + the vanilla's toast wording. */
export async function deleteLibraryTrackRequest(
  trackId: unknown,
  choice: DeleteTrackChoice,
): Promise<ActionToast> {
  const params = new URLSearchParams();
  if (choice === 'delete_file') params.set('delete_file', 'true');
  const response = await fetch(`/api/library/track/${trackId}?${params}`, { method: 'DELETE' });
  const result = await response.json();
  if (!result.success) throw new Error(result.error);

  let message = 'Track removed from library';
  let tone: ActionToast['tone'] = 'success';
  if (result.file_deleted) {
    message = 'Track deleted from library and disk';
  } else if (result.file_error) {
    message = 'Track removed from library but file could not be deleted';
    tone = 'warning';
  }
  if (result.blacklisted) message += ' (source blacklisted)';
  return { message, tone, extra: result.file_error || undefined };
}

/** DELETE /api/library/album/<id>[?delete_files=true] + the vanilla's toast wording. */
export async function deleteLibraryAlbumRequest(
  albumId: unknown,
  choice: DeleteAlbumChoice,
): Promise<ActionToast> {
  const params = choice === 'delete_files' ? '?delete_files=true' : '';
  const response = await fetch(`/api/library/album/${albumId}${params}`, { method: 'DELETE' });
  const result = await response.json();
  if (!result.success) throw new Error(result.error);

  let message = `Album removed from library (${result.tracks_deleted || 0} tracks)`;
  let tone: ActionToast['tone'] = 'success';
  if (choice === 'delete_files') {
    if (result.files_deleted > 0) {
      message = `Album deleted — ${result.files_deleted} files removed from disk`;
    }
    if (result.files_failed > 0) {
      message += ` (${result.files_failed} files could not be deleted)`;
      tone = 'warning';
    }
  }
  return { message, tone };
}

// ---- Track source info (showTrackSourceInfo, 3192) ----

/** Download-provenance services (3250-3251) — icons and labels stay paired. */
export const SOURCE_SERVICES: Record<string, { icon: string; label: string }> = {
  soulseek: { icon: '🔍', label: 'Soulseek' },
  youtube: { icon: '▶️', label: 'YouTube' },
  tidal: { icon: '🌊', label: 'Tidal' },
  qobuz: { icon: '🎵', label: 'Qobuz' },
  hifi: { icon: '🎧', label: 'HiFi' },
  deezer: { icon: '💜', label: 'Deezer' },
  lidarr: { icon: '📦', label: 'Lidarr' },
  amazon: { icon: '🛒', label: 'Amazon Music' },
  soundcloud: { icon: '☁️', label: 'SoundCloud' },
  auto_import: { icon: '📥', label: 'Auto-Import' },
  staging: { icon: '📥', label: 'Staging' },
  torrent: { icon: '🧲', label: 'Torrent' },
  usenet: { icon: '📰', label: 'Usenet' },
};

export interface SourceDownload {
  source_service?: string;
  source_username?: string;
  source_filename?: string;
  source_size?: number;
  audio_quality?: string;
  bit_depth?: number;
  sample_rate?: number;
  bitrate?: number;
  created_at?: string;
  status?: string;
  track_title?: string;
  track_artist?: string;
}

export async function fetchTrackSourceInfo(trackId: unknown): Promise<SourceDownload[]> {
  const res = await fetch(`/api/library/track/${trackId}/source-info`);
  const data = await res.json();
  if (!data.success) return [];
  return data.downloads || [];
}

/** The detail rows the popover shows for the most recent download (3260-3297). */
export function sourceInfoRows(
  dl: SourceDownload,
): { label: string; value: string; mono?: boolean; tone?: 'error' }[] {
  const svc = SOURCE_SERVICES[dl.source_service || ''] || {
    icon: '📦',
    label: dl.source_service || '',
  };
  const rows: { label: string; value: string; mono?: boolean; tone?: 'error' }[] = [
    { label: 'Service', value: `${svc.icon} ${svc.label}` },
  ];
  if (dl.source_service === 'soulseek' && dl.source_username) {
    rows.push({ label: 'User', value: dl.source_username, mono: true });
  }
  const displayFile = dl.source_filename
    ? dl.source_filename.replace(/\\/g, '/').split('/').pop() || 'Unknown'
    : 'Unknown';
  rows.push({ label: 'Original File', value: displayFile, mono: true });
  if (dl.source_size)
    rows.push({ label: 'Size', value: `${(dl.source_size / 1048576).toFixed(1)} MB` });
  if (dl.audio_quality) rows.push({ label: 'Quality', value: dl.audio_quality });
  const audio = [
    dl.bit_depth ? `${dl.bit_depth}-bit` : '',
    dl.sample_rate ? `${(dl.sample_rate / 1000).toFixed(1)}kHz` : '',
    dl.bitrate ? `${Math.round(dl.bitrate / 1000)}kbps` : '',
  ].filter(Boolean);
  if (audio.length) rows.push({ label: 'Audio', value: audio.join(' · ') });
  if (dl.status && dl.status !== 'completed') {
    rows.push({ label: 'Status', value: dl.status, tone: 'error' });
  }
  return rows;
}

/** POST /api/library/blacklist — reason is always user_rejected here (3313-3323). */
export async function blacklistSourceRequest(
  dl: SourceDownload,
  fallbackTitle: string,
): Promise<{ success: boolean; error?: string }> {
  const res = await fetch('/api/library/blacklist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      track_title: dl.track_title || fallbackTitle,
      track_artist: dl.track_artist || '',
      blocked_filename: dl.source_filename,
      blocked_username: dl.source_username,
      reason: 'user_rejected',
    }),
  });
  return res.json();
}

// ---- Art pickers (openAlbumArtPicker 1669 / openArtistArtPicker 1836) ----

export interface ArtCandidate {
  url: string;
  source: string;
}

export interface ArtPickerTarget {
  kind: 'album' | 'artist';
  id: unknown;
  /** Query context for the album variant (1718): artist + album title. */
  artistName?: string;
  albumTitle?: string;
}

export async function fetchArtOptions(target: ArtPickerTarget): Promise<ArtCandidate[]> {
  const url =
    target.kind === 'album'
      ? `/api/album/${encodeURIComponent(String(target.id))}/art-options` +
        `?artist=${encodeURIComponent(target.artistName || '')}` +
        `&album=${encodeURIComponent(target.albumTitle || '')}`
      : `/api/artist/${encodeURIComponent(String(target.id))}/art-options`;
  const res = await fetch(url);
  const data = await res.json();
  return (data && data.candidates) || [];
}

export interface ArtApplyResult {
  success: boolean;
  error?: string;
  server_updated?: boolean;
  disk_written?: boolean;
}

export async function applyArtRequest(
  target: ArtPickerTarget,
  url: string,
): Promise<ArtApplyResult> {
  const endpoint =
    target.kind === 'album'
      ? `/api/album/${encodeURIComponent(String(target.id))}/art`
      : `/api/artist/${encodeURIComponent(String(target.id))}/art`;
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  return res.json();
}

/** The artist apply-toast suffix listing what else got the new photo (1972-1976). */
export function artistArtAppliedMessage(result: ArtApplyResult): string {
  const parts = [];
  if (result.server_updated) parts.push('server');
  if (result.disk_written) parts.push('artist.jpg');
  return 'Artist photo updated' + (parts.length ? ' (also updated: ' + parts.join(', ') + ')' : '');
}
