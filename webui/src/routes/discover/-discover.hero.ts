/**
 * The hero billboard's logic, transcribed from `displayDiscoverHeroArtist`
 * and `loadDiscoverHero` in discover.js.
 *
 * These decisions live inline in the vanilla's DOM-writing function, so unlike
 * the helpers in -discover.helpers.ts they cannot be lifted out and compared
 * automatically. Every rule below therefore cites the line it came from, and
 * the tests assert the behaviour those lines describe rather than a
 * reimplementation of them.
 */

import type { DiscoverHeroArtist } from './-discover.types';

/** Slideshow interval. `setInterval(..., 8000)` — loadDiscoverHero:442. */
export const HERO_SLIDE_MS = 8000;

/**
 * The id to act on, with the vanilla's fallback chain (line 518):
 *
 *     artist.artist_id || artist.spotify_artist_id || artist.itunes_artist_id
 *
 * `artist_id` is what the backend already resolved for the ACTIVE source, so
 * it wins. The other two are the cross-source ids the buttons also carry, and
 * they only matter when the active source produced nothing.
 */
export function heroArtistId(artist: DiscoverHeroArtist | null | undefined): string | null {
  if (!artist) return null;
  return artist.artist_id || artist.spotify_artist_id || artist.itunes_artist_id || null;
}

/**
 * Popularity is shown only when it is present AND above zero (line 477).
 *
 * The `> 0` half matters: the server sends `popularity: 0` for artists it has
 * no data on, and a "0/100 Popularity" badge reads as a judgement rather than
 * an absence.
 */
export function heroShowsPopularity(artist: DiscoverHeroArtist | null | undefined): boolean {
  return artist?.popularity !== undefined && (artist.popularity as number) > 0;
}

/** high >= 80, medium >= 50, else low — lines 478-479. */
export function heroPopularityClass(popularity: number): 'high' | 'medium' | 'low' {
  return popularity >= 80 ? 'high' : popularity >= 50 ? 'medium' : 'low';
}

/** At most three genre tags — `artist.genres.slice(0, 3)`, line 492. */
export const HERO_MAX_GENRES = 3;

export function heroGenres(artist: DiscoverHeroArtist | null | undefined): string[] {
  const genres = artist?.genres;
  return Array.isArray(genres) ? genres.slice(0, HERO_MAX_GENRES) : [];
}

/**
 * The watchlist button's copy (lines 565-575).
 *
 * The icon is the SAME in both states in the vanilla — only the label and the
 * `watching` class change. Do not "improve" that into two icons; the button is
 * an eye either way, and the class is what the stylesheet keys off.
 */
export const HERO_WATCHLIST_ICON = '👁️';

export function heroWatchlistLabel(isWatching: boolean): string {
  return isWatching ? 'Watching...' : 'Add to Watchlist';
}

/**
 * Advance the slideshow index, wrapping in both directions.
 *
 * `navigateDiscoverHero` (line 1238):
 *     (index + direction + length) % length
 *
 * The `+ length` is what makes -1 wrap to the end instead of going negative.
 */
export function heroNextIndex(index: number, direction: number, length: number): number {
  if (length <= 0) return 0;
  return (index + direction + length) % length;
}

/**
 * Jump straight to a slide, ignoring out-of-range requests.
 *
 * `jumpToDiscoverHeroSlide` (line 1260) guards `index < 0 || index >= length`
 * and returns without changing anything — it does NOT clamp.
 */
export function heroJumpIndex(current: number, target: number, length: number): number {
  if (target < 0 || target >= length) return current;
  return target;
}

/**
 * Does the slideshow auto-advance?
 *
 * Only with more than one artist — `if (discoverHeroArtists.length > 1)`,
 * line 438. A single artist must not get a timer that re-renders it forever.
 */
export function heroAutoAdvances(count: number): boolean {
  return count > 1;
}

/** Copy for the "nothing to recommend" state — `showDiscoverHeroEmpty`, 1271-1272. */
export const HERO_EMPTY_TITLE = 'No Recommendations Yet';
export const HERO_EMPTY_SUBTITLE = 'Run a watchlist scan to generate personalized recommendations';

// ── Interactions ────────────────────────────────────────────────────────────

/** Watch-All button copy and states — watchAllHeroArtists (599), checkAll (1200). */
export const WATCH_ALL_IDLE = 'Watch All';
export const WATCH_ALL_BUSY = 'Adding...';
export const WATCH_ALL_DONE = 'All Watched';

export type WatchAllPhase = 'idle' | 'busy' | 'done';

export interface WatchAllState {
  label: string;
  disabled: boolean;
  /** The `all-watched` class the stylesheet keys off. */
  allWatched: boolean;
}

/**
 * The Watch-All button's three states.
 *
 * `done` stays DISABLED (626) — there is nothing left to add, and re-posting
 * the batch would be a no-op the user cannot tell from a failure. `busy`
 * disables too (607) so a double-click cannot fire two batch adds.
 */
export function watchAllState(phase: WatchAllPhase): WatchAllState {
  if (phase === 'done') return { label: WATCH_ALL_DONE, disabled: true, allWatched: true };
  if (phase === 'busy') return { label: WATCH_ALL_BUSY, disabled: true, allWatched: false };
  return { label: WATCH_ALL_IDLE, disabled: false, allWatched: false };
}

/**
 * Is the whole hero already on the watchlist?
 *
 * `checkAllHeroWatchlistStatus` (1205-1217) treats a FAILED check as
 * not-watching and stops at the first miss — one unwatched artist is enough to
 * leave the button actionable. A response missing `success` counts as a miss
 * too, so a broken check never claims everything is watched.
 */
export function allWatched(results: { success?: boolean; is_watching?: boolean }[]): boolean {
  for (const r of results) {
    if (!r?.success || !r.is_watching) return false;
  }
  return true;
}

/**
 * The batch payload for Watch All (611-614).
 *
 * Only `artist_id` and `artist_name` — deliberately not the whole artist
 * object, which carries per-source ids the endpoint does not read.
 */
export function watchAllPayload(
  artists: DiscoverHeroArtist[],
): { artist_id: string | null; artist_name: string }[] {
  return artists.map((a) => ({
    artist_id: a.artist_id ?? null,
    artist_name: a.artist_name,
  }));
}

export interface HeroIndicator {
  index: number;
  active: boolean;
  /** `Go to slide ${index + 1}` — 1-based for screen readers (1255). */
  ariaLabel: string;
}

/** Slideshow dots — updateDiscoverHeroIndicators (1247-1257). */
export function heroIndicators(count: number, currentIndex: number): HeroIndicator[] {
  return Array.from({ length: count }, (_, index) => ({
    index,
    active: index === currentIndex,
    ariaLabel: `Go to slide ${index + 1}`,
  }));
}
