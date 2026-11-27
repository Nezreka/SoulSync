/**
 * volume levelling: how many dB to play each song (and video) at, from the
 * loudness the server measured. pure functions, so the rules are tested on
 * their own; the engine applies the answer through the web audio gain.
 *
 * the same rules as core/loudness.level_db on the server and the Android
 * app (scrap/MEDIA_ANALYSIS_CONTRACT.md, "Loudness"):
 * - gain = file gain + (target - reference) + pre-amp
 * - track / album / smart (album gain while an album plays in order)
 * - a missing album gain falls back to the track gain, and back again
 * - clipping prevention: never past the point the peak reaches full scale
 * - nothing known: turned down a little (unknown songs are usually loud
 *   modern masters), and never up
 */

import type { Queue } from './queue';
import type { PlayItem } from './types';

export type LevelMode = 'off' | 'track' | 'album' | 'smart';

/** a song file's loudness, as /api/player/playable sends it */
export interface Loudness {
  track_gain: number | null;
  track_peak: number | null;
  album_gain: number | null;
  album_peak: number | null;
  /** the loudness the gains reach, LUFS (-18 unless the library's target was changed) */
  reference_lufs: number;
  source?: string | null;
}

/** a video file's measured loudness */
export interface VideoLoudness {
  integrated_lufs: number;
  true_peak_db: number | null;
  lra?: number | null;
  /** the audio track measured (0: the first) */
  audio_stream?: number;
}

export interface LevelPrefs {
  mode: LevelMode;
  /** LUFS levelled music plays at */
  target: number;
  /** dB on top of levelling */
  preamp: number;
  preventClipping: boolean;
  /** level videos too */
  video: boolean;
}

export const DEFAULT_LEVEL_PREFS: LevelPrefs = {
  mode: 'smart',
  target: -18,
  preamp: 0,
  preventClipping: true,
  video: false,
};

/** a song with no measurement yet, below the target's reference (dB) */
export const UNKNOWN_SONG_DB = -6;
/** videos sit 5 dB under music (-23 LUFS at the -18 default, the broadcast standard) */
export const VIDEO_OFFSET_DB = -5;
/** a quiet film is lifted at most this much: past it, it's the mix, not the level */
export const VIDEO_MAX_BOOST_DB = 10;
/** the most any level reaches, either way */
const LIMIT_DB = 24;

export const dbToLinear = (db: number) => Math.pow(10, db / 20);

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** the most gain a peak allows before it distorts (dB), or null when unknown */
export function peakCeiling(peak: number | null | undefined): number | null {
  return finite(peak) && peak > 0 ? -20 * Math.log10(peak) + 0 : null;
}

/**
 * does this item play as part of its album? the queue isn't shuffled, and
 * a song beside it is from the same album, in album order when the track
 * numbers say. a lone song, a playlist mixing albums, a shuffle: no.
 */
export function inAlbumContext(queue: Pick<Queue, 'items' | 'index' | 'shuffle'>): boolean {
  if (queue.shuffle) return false;
  const cur = queue.items[queue.index];
  if (!cur || cur.type !== 'track' || !cur.parentId) return false;
  const prev = queue.items[queue.index - 1];
  const next = queue.items[queue.index + 1];
  return sameAlbumInOrder(prev, cur) || sameAlbumInOrder(cur, next);
}

function sameAlbumInOrder(a: PlayItem | undefined, b: PlayItem | undefined): boolean {
  if (!a || !b || a.type !== 'track' || b.type !== 'track') return false;
  if (!a.parentId || a.parentId !== b.parentId || a.id === b.id) return false;
  // positions known: the second must come after the first (disc, then track)
  if (finite(a.trackNumber) && finite(b.trackNumber)) {
    const da = a.discNumber ?? 1;
    const db = b.discNumber ?? 1;
    return db > da || (db === da && b.trackNumber > a.trackNumber);
  }
  return true;
}

/** the dB a song plays at. ``albumContext``: inAlbumContext for its place in the queue */
export function songLevelDb(
  loud: Loudness | null | undefined,
  prefs: LevelPrefs,
  albumContext: boolean,
): number {
  if (prefs.mode === 'off') return 0;
  const shift = prefs.target - (finite(loud?.reference_lufs) ? loud.reference_lufs : -18);
  const useAlbum = prefs.mode === 'album' || (prefs.mode === 'smart' && albumContext);
  let gain = useAlbum ? loud?.album_gain : loud?.track_gain;
  let peak = useAlbum ? loud?.album_peak : loud?.track_peak;
  if (!finite(gain)) {
    gain = useAlbum ? loud?.track_gain : loud?.album_gain;
    peak = useAlbum ? loud?.track_peak : loud?.album_peak;
  }
  let db: number;
  if (finite(gain)) {
    db = gain + shift + prefs.preamp;
  } else {
    // not measured yet: a little under the target, never louder than it was
    db = Math.min(0, UNKNOWN_SONG_DB + shift + prefs.preamp);
    peak = null;
  }
  if (prefs.preventClipping) {
    const ceiling = peakCeiling(peak);
    // no peak known: never turned up (a boost could clip), only down
    db = Math.min(db, ceiling ?? 0);
  }
  return clampDb(db);
}

/** the dB a video plays at (0: not levelled, or not measured yet) */
export function videoLevelDb(
  loud: VideoLoudness | null | undefined,
  prefs: LevelPrefs,
  audioTrack = 0,
): number {
  if (!prefs.video || !loud || !finite(loud.integrated_lufs)) return 0;
  // another audio track (a dub, commentary) wasn't measured: leave it be
  if ((loud.audio_stream ?? 0) !== audioTrack) return 0;
  let db = prefs.target + VIDEO_OFFSET_DB - loud.integrated_lufs + prefs.preamp;
  db = Math.min(db, VIDEO_MAX_BOOST_DB);
  if (prefs.preventClipping) {
    const ceiling = finite(loud.true_peak_db) ? -loud.true_peak_db : 0;
    db = Math.min(db, ceiling);
  }
  return clampDb(db);
}

function clampDb(db: number): number {
  const v = Math.max(-LIMIT_DB, Math.min(LIMIT_DB, db));
  return Math.round(v * 100) / 100 + 0;
}

/** profile settings (the server's names) as levelling prefs; junk reads as the default */
export function levelPrefsFrom(
  s:
    | {
        loudness_mode?: string;
        loudness_target?: number;
        loudness_preamp?: number;
        loudness_prevent_clipping?: boolean;
        video_loudness?: boolean;
      }
    | null
    | undefined,
): LevelPrefs {
  const d = DEFAULT_LEVEL_PREFS;
  const mode = (['off', 'track', 'album', 'smart'] as const).find((m) => m === s?.loudness_mode);
  return {
    mode: mode ?? d.mode,
    target: finite(s?.loudness_target) ? s.loudness_target : d.target,
    preamp: finite(s?.loudness_preamp) ? s.loudness_preamp : d.preamp,
    preventClipping: s?.loudness_prevent_clipping ?? d.preventClipping,
    video: s?.video_loudness ?? d.video,
  };
}

/** loudness from a pre-cached song's response headers (the stream route sends them) */
export function loudnessFromHeaders(h: Headers): Loudness | null {
  const num = (name: string) => {
    const raw = h.get(name);
    if (raw === null || raw === '') return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  };
  const ref = num('X-Loudness-Reference');
  const trackGain = num('X-ReplayGain-Track-Gain');
  const albumGain = num('X-ReplayGain-Album-Gain');
  if (ref === null || (trackGain === null && albumGain === null)) return null;
  return {
    track_gain: trackGain,
    track_peak: num('X-ReplayGain-Track-Peak'),
    album_gain: albumGain,
    album_peak: num('X-ReplayGain-Album-Peak'),
    reference_lufs: ref,
  };
}
