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
    if (!response.ok || !data?.success) return null;
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
 * what actually happened when something asked for playback.
 *
 * 'unsupported' is not 'failed'. there is just no player bridge on this page,
 * which is a "not here" the user can't retry. the old code awaited nothing and
 * toasted "Playing all N tracks" whether or not a bridge existed.
 */
export type PlayOutcome = 'played' | 'empty' | 'failed' | 'unsupported' | 'superseded';

let latestIntent = 0;
export function beginPlayIntent() {
  const id = ++latestIntent;
  window.cancelPendingPlayback?.();
  return { isCurrent: () => id === latestIntent };
}
export type PlayIntent = ReturnType<typeof beginPlayIntent>;

/** is the shared media player reachable from here at all? */
export function playerBridgeAvailable(): boolean {
  return typeof window.playTrackList === 'function';
}

/**
 * resolve a tracklist against the library and hand it to the player, awaiting
 * the handoff before claiming anything.
 *
 * the caller writes the toast from what the resolution actually said. nothing
 * here reports a success it didn't see.
 */
async function resolveAndPlay(
  tracks: unknown[],
  contextName: string,
  say: (res: PlayableResolution) => [string, 'success' | 'info'],
  intent: PlayIntent,
): Promise<PlayOutcome> {
  if (!intent.isCurrent()) return 'superseded';
  if (!playerBridgeAvailable()) {
    window.showToast?.('The player is not available on this page', 'error');
    return 'unsupported';
  }
  const res = await resolveMixPlayable(tracks);
  if (!intent.isCurrent()) return 'superseded';
  if (res === null) {
    window.showToast?.('Could not check your library right now', 'error');
    return 'failed';
  }
  if (res.queueRows.length === 0) {
    window.showToast?.('This mix has no playable track metadata', 'info');
    return 'empty';
  }
  try {
    const result = await window.playTrackList?.(res.queueRows, contextName, intent);
    if (!intent.isCurrent() || result?.status === 'superseded') return 'superseded';
    if (result?.status === 'skipped') {
      // The first track could not play and the queue moved on. That is not a
      // failure, and the player has already said which track it skipped.
      window.showToast?.('Skipped a track that would not play. Playing the next one.', 'info');
      return 'played';
    }
    if (result?.status !== 'played') {
      window.showToast?.('Playback could not start. Try again.', 'error');
      return 'failed';
    }
  } catch {
    if (!intent.isCurrent()) return 'superseded';
    window.showToast?.('Playback could not start', 'error');
    return 'failed';
  }
  const [message, level] = say(res);
  window.showToast?.(message, level);
  return 'played';
}

/**
 * resolve and play a whole mix. the shared behaviour behind every play button
 * on the page; the outcome tells the caller whether to close the modal.
 */
export async function playMixNow(
  tracks: unknown[],
  contextName: string,
  intent = beginPlayIntent(),
): Promise<PlayOutcome> {
  return resolveAndPlay(
    tracks,
    contextName,
    (res) => {
      const missing = Math.max(0, res.total - res.matched);
      return res.matched === res.total
        ? [`Playing all ${res.matched} tracks`, 'success']
        : [`Queued ${res.total} tracks, ${missing} will download first`, 'success'];
    },
    intent,
  );
}

/**
 * play ONE row out of an open mix.
 *
 * this row button used to say Preview and do nothing. it is full playback
 * through the same bridge, so it says Play, and a row we don't own says that
 * instead of pretending it started.
 */
export async function playTrackNow(
  track: unknown,
  label: string,
  intent = beginPlayIntent(),
): Promise<PlayOutcome> {
  return resolveAndPlay([track], label, () => [`Playing ${label}`, 'success'], intent);
}
