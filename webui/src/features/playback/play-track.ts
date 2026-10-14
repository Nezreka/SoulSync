/**
 * "Play this song" — one implementation, for every surface that has only a
 * title and an artist to go on.
 *
 * Lifted out of the artist-detail page (which lifted it from library.js:1209)
 * when the dashboard's Recently Played band needed the same behaviour. Two
 * copies of a library-first/stream-second ladder would drift, and the drift
 * would be silent: the copy that fell behind would just quietly stream a
 * track the user owns.
 *
 * Callers: artist detail's top-tracks sidebar and track table, and the
 * dashboard's Recently Played rail.
 */

import type { ShellBridge } from '../../platform/shell/bridge';

/** the owned copy of a song, ready for the player, or null on a miss. */
export interface LibraryTrackPayload {
  id: string | number;
  title: string;
  file_path: string;
  bitrate?: string | number | null;
  artist_id?: string | number | null;
  album_id?: string | number | null;
  _stats_image: string | null;
  album: string;
  artist: string;
}

/**
 * Find the library's copy of a title + artist (/api/stats/resolve-track).
 * null on a miss AND on a failed request: callers fall through to their
 * no-library path either way.
 */
export async function resolveLibraryTrack(
  title: string,
  artist: string,
  album = '',
): Promise<LibraryTrackPayload | null> {
  try {
    const response = await fetch('/api/stats/resolve-track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, artist }),
    });
    const data = await response.json();
    if (!data?.success || !data.track) return null;
    const track = data.track;
    return {
      id: track.id,
      title: track.title,
      file_path: track.file_path,
      bitrate: track.bitrate,
      artist_id: track.artist_id,
      album_id: track.album_id,
      _stats_image: track.image_url || null,
      album: track.album_title || album || '',
      artist: track.artist_name || artist || '',
    };
  } catch {
    return null;
  }
}

/**
 * Play one row: library first, streaming second (playTrackByMetadata,
 * library.js:1209).
 *
 * The order is the point — an owned copy is faster and better quality than a
 * stream, so /api/stats/resolve-track is always tried first and the streaming
 * fallback only runs on a genuine library miss. A resolve FAILURE also falls
 * through to streaming rather than surfacing an error.
 */
export async function playTrackByMetadata(
  bridge: ShellBridge | null,
  title: string,
  artist: string,
  album = '',
): Promise<void> {
  const owned = await resolveLibraryTrack(title, artist, album);
  if (owned) {
    const { album: albumTitle, artist: artistName, ...track } = owned;
    bridge?.playLibraryTrack(track, albumTitle, artistName);
    return;
  }

  bridge?.showLoadingOverlay(`Searching for ${title}...`);
  try {
    const response = await fetch('/api/enhanced-search/stream-track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        track_name: title,
        artist_name: artist,
        album_name: album,
        duration_ms: 0,
      }),
    });
    const data = await response.json();
    bridge?.hideLoadingOverlay();

    if (data?.success && data.result) {
      if (bridge) await bridge.startStream(data.result);
      else window.showToast?.('Streaming not available', 'error');
      return;
    }
    window.showToast?.(data?.error || 'Track not found in library or any source', 'error');
  } catch {
    bridge?.hideLoadingOverlay();
    window.showToast?.('Failed to play track', 'error');
  }
}
