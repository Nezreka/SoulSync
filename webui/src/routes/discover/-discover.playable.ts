/**
 * The play-now bridge: resolve any mix tracklist against the library and
 * hand the owned rows to the media player.
 *
 * Every discover mix is artist/title pairs from metadata sources. The
 * player's `window.playTrackList` wants library rows with a `file_path` —
 * `/api/discover/resolve-playable` maps one to the other. Playing what you
 * own INSTANTLY, with the missing remainder one click from download, is the
 * page's structural edge over every discovery tool that only downloads.
 */

import { normalizeTrack } from './-discover.helpers';

export interface PlayableResolution {
  rows: Record<string, unknown>[];
  queueRows: Record<string, unknown>[];
  matched: number;
  total: number;
}

export function toPlayablePairs(tracks: unknown[]): { artist: string; title: string }[] {
  return (tracks || []).map((t) => {
    const n = normalizeTrack(t as never);
    // normalizeTrack speaks the pool/spotify shapes (name/artists/track_name);
    // some rows (lastfm radio, plain lists) carry bare title/artist instead -
    // fall back to those before settling for Unknown
    const raw = (t ?? {}) as { title?: string; artist?: string };
    return {
      artist: n.artist !== 'Unknown Artist' ? n.artist : raw.artist || n.artist,
      title: n.name !== 'Unknown Track' ? n.name : raw.title || n.name,
    };
  });
}

export async function resolveMixPlayable(tracks: unknown[]): Promise<PlayableResolution | null> {
  const pairs = toPlayablePairs(tracks);
  if (!pairs.length) return { rows: [], queueRows: [], matched: 0, total: 0 };
  try {
    const response = await fetch('/api/discover/resolve-playable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tracks: pairs }),
    });
    const data = (await response.json()) as {
      success?: boolean;
      tracks?: Record<string, unknown>[];
      queue_tracks?: Record<string, unknown>[];
      matched?: number;
      total?: number;
    };
    if (!data?.success) return null;
    return {
      rows: Array.isArray(data.tracks) ? data.tracks : [],
      queueRows: Array.isArray(data.queue_tracks)
        ? data.queue_tracks
        : Array.isArray(data.tracks)
          ? data.tracks
          : [],
      matched: data.matched ?? 0,
      total: data.total ?? 0,
    };
  } catch {
    return null;
  }
}

/**
 * Resolve and play; the shared behavior behind every ▶ on the page.
 * Returns what happened so callers can toast/close appropriately.
 */
export async function playMixNow(
  tracks: unknown[],
  contextName: string,
): Promise<'played' | 'empty' | 'failed'> {
  const res = await resolveMixPlayable(tracks);
  if (res === null) {
    window.showToast?.('Could not check your library right now', 'error');
    return 'failed';
  }
  if (res.queueRows.length === 0) {
    window.showToast?.('This mix has no playable track metadata', 'info');
    return 'empty';
  }
  void window.playTrackList?.(res.queueRows as never, contextName);
  const missing = Math.max(0, res.total - res.matched);
  window.showToast?.(
    res.matched === res.total
      ? `Playing all ${res.matched} tracks`
      : `Queued ${res.total} tracks — preloading ${missing} missing`,
    'success',
  );
  return 'played';
}
