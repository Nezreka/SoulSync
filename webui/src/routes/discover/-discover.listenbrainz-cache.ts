/**
 * The ListenBrainz caches — a cross-file contract, not page state.
 *
 * `listenbrainzPlaylistsCache` and `listenbrainzTracksCache` are declared in
 * discover.js (3230-3231) and reached by two other files. Both consumers are
 * `typeof`-guarded, which is worse than being unguarded: an unguarded read
 * throws and `test_vanilla_globals_resolve` catches it, while these degrade
 * SILENTLY into two different wrong behaviours.
 *
 * ── Consumer 1: init.js `_invalidateListenBrainzCache` (1964) ───────────────
 *
 *   Object.keys(listenbrainzPlaylistsCache).forEach(k => delete cache[k])
 *
 * It clears the caches IN PLACE and never reassigns. So the published object's
 * IDENTITY must be stable and the object must be mutable — handing out a fresh
 * object per render, or a frozen one, means disconnecting a ListenBrainz
 * account leaves its playlists cached until reload. It also sets
 * `listenbrainzPlaylistsLoaded = false`, so that flag is part of the contract.
 *
 * ── Consumer 2: sync-listenbrainz.js (175-194) ──────────────────────────────
 *
 *   if (typeof listenbrainzTracksCache === 'undefined') {
 *     window.listenbrainzTracksCache = {};
 *   }
 *
 * If the tracks cache is missing it CREATES ITS OWN. Nothing throws, nothing
 * warns — the Sync tab and the Discover page simply stop sharing, so tracks
 * fetched on one are invisible to the other and both re-fetch. This is the
 * silent-degradation case the port has to prevent by publishing eagerly.
 */

/** The three names the other files reference. */
export const LB_CACHE_GLOBALS = [
  'listenbrainzPlaylistsCache',
  'listenbrainzTracksCache',
  'listenbrainzPlaylistsLoaded',
] as const;

/** Playlists are keyed by TAB (3429, 3440, 3451); tracks by playlist mbid (3621). */
export type LbPlaylistsCache = Record<string, unknown[]>;
export type LbTracksCache = Record<string, unknown[]>;

export interface LbCacheGlobals {
  listenbrainzPlaylistsCache: LbPlaylistsCache;
  listenbrainzTracksCache: LbTracksCache;
  listenbrainzPlaylistsLoaded: boolean;
}

/**
 * Publish both caches on `window`, at MODULE LOAD.
 *
 * Eagerly, for the same reason as the download bar: sync-listenbrainz.js can
 * run before any discover component mounts, and if it wins the race it forks
 * the cache permanently for that page load.
 *
 * Existing objects are REUSED rather than replaced, so a second call cannot
 * orphan the reference init.js is clearing through.
 */
export function publishLbCaches(target: Record<string, unknown>): LbCacheGlobals {
  if (!target.listenbrainzPlaylistsCache) target.listenbrainzPlaylistsCache = {};
  if (!target.listenbrainzTracksCache) target.listenbrainzTracksCache = {};
  if (typeof target.listenbrainzPlaylistsLoaded !== 'boolean') {
    target.listenbrainzPlaylistsLoaded = false;
  }
  return {
    listenbrainzPlaylistsCache: target.listenbrainzPlaylistsCache as LbPlaylistsCache,
    listenbrainzTracksCache: target.listenbrainzTracksCache as LbTracksCache,
    listenbrainzPlaylistsLoaded: target.listenbrainzPlaylistsLoaded as boolean,
  };
}

/**
 * Clear in place, exactly as init.js does.
 *
 * Provided so the React side has one implementation of the semantics rather
 * than reaching for `= {}` — which would look equivalent and would break the
 * other file's reference.
 */
export function clearLbCacheInPlace(cache: Record<string, unknown>): void {
  for (const key of Object.keys(cache)) delete cache[key];
}

/**
 * ── The two writers disagree on shape ───────────────────────────────────────
 *
 * discover.js writes the API payload RAW: `listenbrainzTracksCache[id] =
 * data.tracks` (3778). sync-listenbrainz.js writes a NORMALISED row (185-193)
 * with defaults applied and `recording_mbid` renamed to `mbid`.
 *
 * So the same cache key can hold either shape depending on which page filled
 * it, and a reader that assumes one gets the other roughly half the time. The
 * fields overlap enough that nothing has visibly broken, and `mbid` is the one
 * that genuinely differs.
 *
 * This is recorded rather than "fixed": normalising the discover writer would
 * change what every existing discover reader sees, which is a behaviour change
 * that belongs in its own change with its own testing — not smuggled into a
 * port. `normalizeLbTrack` below matches the SYNC shape so a future unification
 * has a single definition to adopt.
 */
export interface LbTrack {
  track_name: string;
  artist_name: string;
  album_name: string;
  duration_ms: number;
  mbid: string;
  release_mbid: string;
  album_cover_url: string;
}

export function normalizeLbTrack(t: Record<string, unknown>): LbTrack {
  return {
    track_name: (t.track_name as string) || '',
    artist_name: (t.artist_name as string) || '',
    album_name: (t.album_name as string) || '',
    duration_ms: (t.duration_ms as number) || 0,
    mbid: ((t.recording_mbid || t.mbid) as string) || '',
    release_mbid: (t.release_mbid as string) || '',
    album_cover_url: (t.album_cover_url as string) || '',
  };
}

/** The tracks endpoint both files hit. */
export function lbPlaylistTracksUrl(mbid: string): string {
  return `/api/discover/listenbrainz/playlist/${encodeURIComponent(mbid)}`;
}

/** A cached entry is only reused when it is non-empty (178, 3753). */
export function lbCacheHit(cache: LbTracksCache, mbid: string): boolean {
  const tracks = cache[mbid];
  return Array.isArray(tracks) && tracks.length > 0;
}
