/**
 * The five cache-backed discovery sections, plus the Genre Deep Dive modal.
 *
 * Transcribed from `_cacheDiscoverData` (10452), `_cacheDiscoverCard` (10454),
 * `openCacheDiscoverAlbum` (10477), `_clampGrid` (10619),
 * `_insertCacheSection` (10642), the four `loadCache*` loaders (10674-10743)
 * and `openGenreDeepDive` (10745) — read end to end.
 *
 * ── One store, six keys ─────────────────────────────────────────────────────
 *
 * `_cacheDiscoverData` is a single object keyed by section, and the card's
 * click handler carries `(sectionKey, index)` rather than the item — so the
 * index must stay aligned with the stored array. Two of the six keys are
 * written by the Genre Deep Dive modal, not by a section loader.
 *
 * ── Three different escapers ────────────────────────────────────────────────
 *
 * The vanilla defines `_esc` inline THREE times with three different character
 * sets, and the differences are deliberate:
 *
 *   _cacheDiscoverCard (10455)      & < > "        — output lands in alt="…"
 *   loadCacheGenreExplorer (10732)  & < > '        — output lands in onclick='…'
 *   openGenreDeepDive (10748)       & < > " '      — both contexts
 *
 * None of them are ported. React escapes text at render and there are no inline
 * `onclick` strings, so both hazards disappear; carrying the escapers across
 * would double-escape. This note exists so the omission reads as a decision.
 */

/** `_clampGrid(gridEl, limit = 12)` (10619). */
export const GRID_CLAMP_LIMIT = 12;

export const ALBUM_PLACEHOLDER = '/static/placeholder-album.png';

/** Section keys written into the shared store. */
export type CacheSectionKey =
  | 'undiscovered'
  | 'genre_releases'
  | 'label_explorer'
  | 'deep_cuts'
  | 'genre_dive_tracks'
  | 'genre_dive_albums';

export interface CacheSectionDef {
  /** DOM id in the vanilla; still the stable identity for the section. */
  id: string;
  key: CacheSectionKey;
  title: string;
  subtitle: string;
  endpoint: string;
  /** Which array the response carries. */
  field: 'albums' | 'tracks';
  itemType: 'album' | 'track';
}

/**
 * The four loader-backed sections, verbatim (10674-10724).
 *
 * Each loader is the same shape: fetch, bail on `!ok`, bail on
 * `!success || !array || !array.length`, store, insert. A failure is
 * `console.debug`'d — these are enrichment, and a missing one should not
 * surface an error to the user.
 */
export const CACHE_SECTIONS: CacheSectionDef[] = [
  {
    id: 'cache-undiscovered',
    key: 'undiscovered',
    title: "Albums You're Missing",
    subtitle: 'From artists you already love — one click to own them',
    endpoint: '/api/discover/undiscovered-albums',
    field: 'albums',
    itemType: 'album',
  },
  {
    id: 'cache-genre-releases',
    key: 'genre_releases',
    title: 'New In Your Genres',
    subtitle: 'Fresh releases from the sounds you collect',
    endpoint: '/api/discover/genre-new-releases',
    field: 'albums',
    itemType: 'album',
  },
  {
    id: 'cache-label-explorer',
    key: 'label_explorer',
    title: 'More From Your Labels',
    subtitle: 'Because you collect these labels',
    endpoint: '/api/discover/label-explorer',
    field: 'albums',
    itemType: 'album',
  },
  {
    id: 'cache-deep-cuts',
    key: 'deep_cuts',
    title: 'Deep Cuts',
    subtitle: 'B-sides and buried tracks from artists you know',
    endpoint: '/api/discover/deep-cuts',
    field: 'tracks',
    itemType: 'track',
  },
];

/** The Genre Explorer is the odd one out: pills, not cards, and inserted TOP. */
export const GENRE_EXPLORER_SECTION = {
  id: 'cache-genre-explorer',
  title: 'Browse Your Sound',
  subtitle: 'Every genre in your collection, one tap deep',
  endpoint: '/api/discover/genre-explorer',
  /** `_insertCacheSection(..., 'top', false)` — no `.discover-grid` wrapper. */
  position: 'top' as const,
  wrapGrid: false,
};

export interface CacheItem {
  name?: string;
  artist_name?: string;
  album_name?: string;
  image_url?: string;
  entity_id?: string;
  album_id?: string;
  source?: string;
  in_library?: boolean;
  [key: string]: unknown;
}

export function cacheSectionItems(
  data: Record<string, unknown> | null | undefined,
  field: 'albums' | 'tracks',
): CacheItem[] {
  if (!data?.success) return [];
  const items = data[field];
  return Array.isArray(items) ? (items as CacheItem[]) : [];
}

/**
 * Whether the section is created at all.
 *
 * `!data.success || !data[field] || !data[field].length` returns EARLY in every
 * loader (10679 and friends), before `_insertCacheSection` — so an empty
 * response leaves no section element behind, not an empty one. That is the
 * difference between "you have no undiscovered albums" being silent and it
 * being a titled empty box.
 *
 * This is a separate predicate rather than a length check inside
 * `cacheSectionItems` because there both branches return `[]` and the
 * distinction is lost — a mutation removing the check survived until this
 * existed.
 */
export function cacheSectionShouldRender(
  data: Record<string, unknown> | null | undefined,
  field: 'albums' | 'tracks',
): boolean {
  return cacheSectionItems(data, field).length > 0;
}

export interface CacheCard {
  cover: string;
  title: string;
  subtitle: string;
  /** Only in-library items get the tick; there is no "missing" counterpart. */
  ownedBadge: boolean;
}

/** `_cacheDiscoverCard` (10454), minus the HTML. */
export function cacheDiscoverCard(item: CacheItem): CacheCard {
  return {
    cover: item.image_url || ALBUM_PLACEHOLDER,
    title: item.name || '',
    subtitle: item.artist_name || '',
    ownedBadge: Boolean(item.in_library),
  };
}

// ── The clamp toggle ────────────────────────────────────────────────────────

export interface GridClamp {
  /** No toggle at all when everything fits. */
  toggleVisible: boolean;
  visibleCount: number;
  label: string;
}

/**
 * `_clampGrid` (10619).
 *
 * At or below the limit there is no button and every card shows. Above it, the
 * button reads "Show all N" with N the FULL count (not the hidden remainder),
 * and flips to "Show less".
 */
export function gridClamp(total: number, expanded: boolean, limit = GRID_CLAMP_LIMIT): GridClamp {
  if (total <= limit) return { toggleVisible: false, visibleCount: total, label: '' };
  return {
    toggleVisible: true,
    visibleCount: expanded ? total : limit,
    label: expanded ? 'Show less' : `Show all ${total}`,
  };
}

// ── Genre Explorer pills ────────────────────────────────────────────────────

export interface GenrePill {
  genre: string;
  /** `explored` / `unexplored` drives the styling. */
  explored: boolean;
  countLabel: string;
  /** The "New" flag is the INVERSE of explored, not a separate field. */
  isNew: boolean;
}

export function genrePill(g: {
  genre?: string;
  explored?: boolean;
  artist_count?: number;
}): GenrePill {
  const count = g.artist_count ?? 0;
  return {
    genre: g.genre ?? '',
    explored: Boolean(g.explored),
    countLabel: `${count} artist${count !== 1 ? 's' : ''}`,
    isNew: !g.explored,
  };
}

// ── Genre Deep Dive ─────────────────────────────────────────────────────────

export const GENRE_DIVE_DEFAULT_SUBTITLE = 'Genre Deep Dive';
export const GENRE_DIVE_EMPTY = 'No cached data found for this genre yet';
export const GENRE_DIVE_EMPTY_HINT = 'Search for artists in this genre to build up the cache';
export const GENRE_DIVE_ERROR = 'Failed to load genre data';

export function genreDiveUrl(genre: string): string {
  return `/api/discover/genre-deep-dive?genre=${encodeURIComponent(genre)}`;
}

/**
 * The header subtitle counts whichever sections came back (10794-10798).
 *
 * Joined with ' · ', in artists → tracks → albums order, and falls back to the
 * literal "Genre Deep Dive" when the dive is empty — never to a bare separator.
 */
export function genreDiveSubtitle(data: {
  artists?: unknown[];
  tracks?: unknown[];
  albums?: unknown[];
}): string {
  const parts: string[] = [];
  const n = (arr: unknown[] | undefined) => arr?.length ?? 0;
  if (n(data.artists)) parts.push(`${n(data.artists)} artist${n(data.artists) !== 1 ? 's' : ''}`);
  if (n(data.tracks)) parts.push(`${n(data.tracks)} track${n(data.tracks) !== 1 ? 's' : ''}`);
  if (n(data.albums)) parts.push(`${n(data.albums)} album${n(data.albums) !== 1 ? 's' : ''}`);
  return parts.length ? parts.join(' · ') : GENRE_DIVE_DEFAULT_SUBTITLE;
}

/**
 * Follower counts (10749).
 *
 * One decimal at millions, NONE at thousands — "1.2M" but "45K", not "45.0K".
 * Zero returns empty so the caller omits the whole line rather than printing
 * "0 followers".
 */
export function formatFollowers(n: number | undefined | null): string {
  if (!n) return '';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(0) + 'K';
  return n.toString();
}

/** Track duration (10755) — empty for a missing length, not "0:00". */
export function formatDuration(ms: number | undefined | null): string {
  if (!ms) return '';
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/** `genre-dive-src-${(source || '').toLowerCase()}` (10824, 10846). */
export function sourceDotClass(source: string | undefined): string {
  return `genre-dive-src-${(source || '').toLowerCase()}`;
}

/** The track row's second line: artist, then album if there is one (10855). */
export function diveTrackSubtitle(track: CacheItem): string {
  const artist = track.artist_name || '';
  return track.album_name ? `${artist} · ${track.album_name}` : artist;
}

/** A dive artist links out only with an entity_id (10823). */
export function diveArtistHasLink(artist: { entity_id?: string }): boolean {
  return Boolean(artist.entity_id);
}

// ── Opening an album from a card ────────────────────────────────────────────

/**
 * The two sections whose items are TRACKS, not albums (10485).
 *
 * They take a different open path: the album has to be found first, by
 * `album_id` if the row has one and otherwise by resolving name+artist through
 * the cache. Everything else opens its `entity_id` directly.
 */
export const TRACK_SECTIONS: CacheSectionKey[] = ['deep_cuts', 'genre_dive_tracks'];

export function isTrackSection(key: string): boolean {
  return TRACK_SECTIONS.includes(key as CacheSectionKey);
}

export const NO_ARTIST_DATA = 'No artist data available for this track';
export const NO_ALBUM_ID = 'No album ID available';
export const NO_TRACKS_FOUND = 'No tracks found';
export const ALBUM_UNAVAILABLE = 'Album not available — it may have been removed from the source';

export function albumFetchUrl(
  source: string,
  albumId: string,
  name: string,
  artist: string,
): string {
  const params = new URLSearchParams({ name, artist });
  return `/api/discover/album/${source}/${albumId}?${params}`;
}

export function resolveCacheAlbumUrl(name: string, artist: string): string {
  return `/api/discover/resolve-cache-album?name=${encodeURIComponent(name)}&artist=${encodeURIComponent(artist)}`;
}

/**
 * Whether to retry through the resolver after a direct album fetch (10562).
 *
 * The album path retries ONLY on 404 — a stale cache entry pointing at a
 * removed album. A 500 is the server being unwell and retrying would just fail
 * twice. (The track path differs: it retries on any non-ok response, because it
 * may have had no album_id to try in the first place — 10510.)
 */
export function shouldResolveAlbum(status: number): boolean {
  return status === 404;
}

export function shouldResolveTrackAlbum(hadAlbumId: boolean, responseOk: boolean): boolean {
  return !hadAlbumId || !responseOk;
}

/**
 * A resolved id is only worth re-fetching if it DIFFERS (10566).
 *
 * Re-requesting the same id that just 404'd would fail identically.
 */
export function resolvedIdIsUseful(
  resolved: { success?: boolean; entity_id?: string } | null | undefined,
  originalId: string,
): boolean {
  return Boolean(resolved?.success && resolved.entity_id && resolved.entity_id !== originalId);
}

/** `discover_cache_${albumId}` (10607). */
export function cacheVirtualAlbumId(albumId: string): string {
  return `discover_cache_${albumId}`;
}
