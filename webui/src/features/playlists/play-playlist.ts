/**
 * play, shuffle or queue a whole playlist: yours or a mirror.
 *
 * a playlist row is artist + title, not a file. the same bridge the discover
 * mixes use (/api/discover/resolve-playable) turns the list into player rows:
 * the songs you own play straight away, the rest wait in the queue for their
 * download, the way any metadata row does.
 */

export interface PlaylistTrackLike {
  track_name?: string;
  artist_name?: string;
  album_name?: string;
  image_url?: string | null;
}

/** the server resolves at most this many per call (core/discovery/playable). */
const RESOLVE_CHUNK = 250;

/** the pairs the resolver reads, in playlist order, blanks dropped. */
export function toResolvePairs(tracks: readonly PlaylistTrackLike[]) {
  return tracks
    .map((t) => ({
      title: String(t.track_name ?? '').trim(),
      artist: String(t.artist_name ?? '').trim(),
      album: String(t.album_name ?? ''),
      image_url: t.image_url ?? null,
    }))
    .filter((t) => t.title && t.artist);
}

/** fisher-yates, on a copy. `random` is there for the tests. */
export function shuffled<T>(items: readonly T[], random: () => number = Math.random): T[] {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/** every row ready for the player, in chunks the resolver will take. null
 *  when the library check failed. */
export async function resolvePlaylist(
  tracks: readonly PlaylistTrackLike[],
): Promise<{ rows: Record<string, unknown>[]; owned: number } | null> {
  const pairs = toResolvePairs(tracks);
  const rows: Record<string, unknown>[] = [];
  let owned = 0;
  for (let i = 0; i < pairs.length; i += RESOLVE_CHUNK) {
    // straight to the endpoint, not resolveMixPlayable: that keeps only artist
    // + title, and a row still to download should carry its album and art
    try {
      const response = await fetch('/api/discover/resolve-playable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tracks: pairs.slice(i, i + RESOLVE_CHUNK) }),
      });
      const data = (await response.json()) as {
        success?: boolean;
        queue_tracks?: Record<string, unknown>[];
        matched?: number;
      };
      if (!response.ok || !data?.success || !Array.isArray(data.queue_tracks)) return null;
      rows.push(...data.queue_tracks);
      owned += data.matched ?? 0;
    } catch {
      return null;
    }
  }
  return { rows, owned };
}

export type PlaylistPlayMode = 'play' | 'shuffle' | 'queue';

/**
 * play / shuffle / queue a playlist's tracks. says what happened in a toast and
 * returns whether anything reached the player.
 */
export async function playPlaylist(
  tracks: readonly PlaylistTrackLike[],
  name: string,
  mode: PlaylistPlayMode = 'play',
): Promise<boolean> {
  if (mode !== 'queue' && typeof window.playTrackList !== 'function') {
    window.showToast?.('The player is not available on this page', 'error');
    return false;
  }
  if (!toResolvePairs(tracks).length) {
    window.showToast?.(`${name} has nothing to play yet`, 'info');
    return false;
  }
  const res = await resolvePlaylist(tracks);
  if (!res) {
    window.showToast?.('Could not check your library right now', 'error');
    return false;
  }
  const rows = mode === 'shuffle' ? shuffled(res.rows) : res.rows;
  const waiting = rows.length - res.owned;
  if (mode === 'queue') {
    if (typeof window.addTracksToQueue === 'function') window.addTracksToQueue(rows);
    else for (const row of rows) window.addToQueue?.(row);
    return true;
  }
  try {
    const result = await window.playTrackList?.(rows, name);
    if (result && typeof result === 'object' && result.status !== 'played') {
      if (result.status === 'superseded') return false;
      if (result.status !== 'skipped') {
        window.showToast?.('Playback could not start. Try again.', 'error');
        return false;
      }
    }
  } catch {
    window.showToast?.('Playback could not start', 'error');
    return false;
  }
  window.showToast?.(
    waiting > 0
      ? `Playing ${name}. ${waiting} song${waiting === 1 ? '' : 's'} will download first`
      : `Playing ${name}`,
    'success',
  );
  return true;
}
