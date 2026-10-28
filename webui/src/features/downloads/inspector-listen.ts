import type { InspectorCandidate } from './inspector';

/**
 * Hear an inspector row before taking it (#1911: "doesn't allow the user to
 * listen to the songs it returned").
 *
 * The same path the search page's play button takes: the stream start pulls
 * the file in the background and the mini player plays it, for a soulseek
 * peer and every streaming source alike. A torrent, usenet or lidarr hit is a
 * whole release, so there is no one song to play.
 */

const RELEASE_LEVEL = new Set(['torrent', 'usenet', 'lidarr']);

/** Sources that hand back audio, not a file name with an extension to check. */
const SERVICE_SOURCES = new Set([
  'youtube',
  'tidal',
  'qobuz',
  'hifi',
  'deezer_dl',
  'soundcloud',
  'amazon',
]);

export function canListen(row: InspectorCandidate): boolean {
  const username = String(row.username || '').toLowerCase();
  return Boolean(username && row.filename) && !RELEASE_LEVEL.has(username);
}

/** A peer's file the browser can't decode (wma, ape...), by extension; null when it plays. */
export function unplayableFormat(row: InspectorCandidate): string | null {
  const username = String(row.username || '').toLowerCase();
  const filename = String(row.filename || '');
  if (SERVICE_SOURCES.has(username) || !filename) return null;
  if (window.isAudioFormatSupported?.(filename) !== false) return null;
  return (window.getFileExtension?.(filename) ?? '').toUpperCase() || 'This format';
}

export async function listenToCandidate(
  row: InspectorCandidate,
  wanted: { name?: string; artist?: string; album?: string },
): Promise<void> {
  const format = unplayableFormat(row);
  if (format) {
    window.showToast?.(
      `${format} won't play in the browser. Pick it to download instead.`,
      'error',
    );
    return;
  }
  const start = window.SoulSyncWebShellBridge?.startStream ?? window.startStream;
  if (!start) {
    window.showToast?.("The player isn't ready yet", 'error');
    return;
  }
  try {
    await start({
      username: row.username,
      filename: row.filename,
      size: row.size ?? 0,
      bitrate: row.bitrate ?? 0,
      duration: row.duration ?? 0,
      quality: row.quality ?? '',
      title: row.title || row.display_name || wanted.name || '',
      artist: row.artist || wanted.artist || '',
      album: wanted.album || '',
    });
  } catch (error) {
    window.showToast?.(
      `Couldn't play it: ${error instanceof Error ? error.message : 'unknown error'}`,
      'error',
    );
  }
}
