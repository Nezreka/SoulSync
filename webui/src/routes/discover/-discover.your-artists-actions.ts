/**
 * Your Artists — the interactions.
 *
 * Transcribed from `openYourArtistInfoModal` (5356), `toggleYourArtistWatchlist`
 * (5513), `_syncYaCardWatchlist` (5553), `refreshYourArtists` (5578),
 * `openYourArtistsSourcesModal` (5608) and its three helpers, and
 * `openYourArtistsModal` (5718) with `_yaFilterSource` / `_yaLoadModal` — each
 * read end to end before any of this was written.
 *
 * ── Three deliberate divergences ────────────────────────────────────────────
 *
 * This section is a near-copy of Your Albums that did not receive the same
 * fixes. Each divergence below is a bug in the vanilla, is fixed here, and is
 * pinned by a test that says so. Nothing else differs.
 *
 *   1. `refreshYourArtists` gives up after 60 attempts with a bare
 *      `clearInterval(poll); return;` (5590) and never re-enables the refresh
 *      button. Your Albums re-enables it from a `setTimeout` (1598). Here the
 *      timeout path re-enables too — see `REFRESH_TIMEOUT_REENABLES_BUTTON`.
 *
 *   2. The sources modal bails SILENTLY on a disconnected source (5673, 5680).
 *      That is the exact complaint the Your Albums hints were added to fix
 *      ("the toggle silently bailed and users saw no feedback — just a
 *      non-responsive switch", 1665-1667); the fix was never copied across.
 *      This port reuses the shared hint table.
 *
 *   3. The all-artists modal resets to page 1 when the SOURCE filter changes
 *      (5772) but not when the search or sort changes (5763, 5745) — so
 *      searching from page 3 requests page 3 of the new result set and usually
 *      renders an empty grid. Your Albums' search does reset (1342).
 */

import { disconnectedHint } from './-discover.your-albums-actions';

// ── The artist info modal (5356) ────────────────────────────────────────────

/** `setTimeout(() => controller.abort(), 8000)` (5413). */
export const INFO_FETCH_TIMEOUT_MS = 8000;

export const INFO_LOADING = 'Loading artist info...';
export const INFO_EMPTY = 'No additional info available';
export const INFO_ERROR = 'Could not load artist info';

export interface ArtistPool {
  id?: number | string;
  artist_name?: string;
  image_url?: string;
  active_source?: string;
  active_source_id?: string;
  on_watchlist?: boolean | number;
  source_services?: string[];
  spotify_artist_id?: string;
  itunes_artist_id?: string;
  deezer_artist_id?: string;
  discogs_artist_id?: string;
  _related?: RelatedArtist[];
  [key: string]: unknown;
}

export interface RelatedArtist {
  id?: string | number;
  name?: string;
  image_url?: string;
  type?: string;
  spotify_id?: string;
  itunes_id?: string;
  deezer_id?: string;
  discogs_id?: string;
}

/**
 * Which id the info endpoint is asked for (5414).
 *
 * Falls back to the NAME when the pool has no active source id — encoded,
 * because it goes in the path, not the query string.
 */
export function infoLookupId(artistId: string | undefined, artistName: string): string {
  return artistId || encodeURIComponent(artistName);
}

export interface MatchBadge {
  key: string;
  fallback: string;
  title: string;
}

/**
 * Matched-source badges (5376-5379).
 *
 * Same four sources and same order as the card badges, but the titles read
 * "Matched on X" rather than the bare service name — on a card the badge means
 * "we hold an id", in the modal it means "we resolved this artist there".
 */
export function infoMatchBadges(pool: ArtistPool): MatchBadge[] {
  const badges: MatchBadge[] = [];
  if (pool.spotify_artist_id)
    badges.push({ key: 'spotify', fallback: 'SP', title: 'Matched on Spotify' });
  if (pool.itunes_artist_id)
    badges.push({ key: 'itunes', fallback: 'IT', title: 'Matched on Apple Music' });
  if (pool.deezer_artist_id)
    badges.push({ key: 'deezer', fallback: 'Dz', title: 'Matched on Deezer' });
  if (pool.discogs_artist_id)
    badges.push({ key: 'discogs', fallback: 'DC', title: 'Matched on Discogs' });
  return badges;
}

/** Display names (5383). Note there is no `discogs` here — origins are follows. */
export const ORIGIN_NAMES: Record<string, string> = {
  spotify: 'Spotify',
  lastfm: 'Last.fm',
  tidal: 'Tidal',
  deezer: 'Deezer',
};

/**
 * "Followed on ..." (5384).
 *
 * Joined with ', ' — a COMMA. The section subtitle for the same data joins with
 * ' and ' (5250). Unifying them would be wrong in one place or the other: the
 * subtitle names two or three services in a sentence, this lists an artist's
 * origins.
 */
export function infoOriginText(sources: string[] | undefined): string {
  return (sources || []).map((s) => ORIGIN_NAMES[s] || s).join(', ');
}

export interface InfoStats {
  listeners: number;
  playcount: number;
  popularity: number;
  /** The stats block is omitted entirely when all three are zero (5430). */
  visible: boolean;
}

/**
 * Stats (5423-5425).
 *
 * `listeners` prefers Last.fm's count and falls back to the streaming follower
 * count — they measure different things, but the label says "listeners" either
 * way and only one is ever populated.
 */
export function infoStats(artist: {
  lastfm_listeners?: number;
  followers?: number;
  lastfm_playcount?: number;
  popularity?: number;
}): InfoStats {
  const listeners = artist.lastfm_listeners || artist.followers || 0;
  const playcount = artist.lastfm_playcount || 0;
  const popularity = artist.popularity || 0;
  return {
    listeners,
    playcount,
    popularity,
    visible: Boolean(listeners || playcount || popularity),
  };
}

/** `Number(n).toLocaleString()` (5432) — thousands separators on the counts. */
export function formatStatValue(n: number): string {
  return Number(n).toLocaleString();
}

/** `cleanBio.length > 600` (5451). */
export const BIO_MAX_LENGTH = 600;

/**
 * Strip and truncate the Last.fm bio (5447-5451).
 *
 * Anchors go first, WITH their text — a Last.fm summary ends in a
 * "Read more on Last.fm" link that is meaningless here. The second pass drops
 * any remaining tags. Order matters: a single `<[^>]+>` pass would leave the
 * anchor's text behind.
 */
export function cleanBio(bio: string): string {
  return bio
    .replace(/<a[^>]*>.*?<\/a>/gi, '')
    .replace(/<[^>]+>/g, '')
    .trim();
}

export function truncateBio(clean: string): string {
  return clean.length > BIO_MAX_LENGTH ? clean.substring(0, BIO_MAX_LENGTH) + '...' : clean;
}

/** `related.slice(0, 12)` with a "+N more" tail (5463, 5481). */
export const RELATED_MAX = 12;

/**
 * The related-artists heading (5459).
 *
 * A watchlisted artist has real similarity data behind it; anything else is
 * showing map adjacency, which is a weaker claim and gets a weaker label.
 */
export function relatedLabel(onWatchlist: boolean | number | undefined): string {
  return onWatchlist ? 'Similar Artists' : 'Connected To';
}

export function relatedVisible(related: RelatedArtist[]): RelatedArtist[] {
  return related.slice(0, RELATED_MAX);
}

export function relatedOverflow(related: RelatedArtist[]): number {
  return Math.max(0, related.length - RELATED_MAX);
}

/** `r.type === 'watchlist'` earns the ★ badge (5465, 5477). */
export function relatedIsWatchlist(r: RelatedArtist): boolean {
  return r.type === 'watchlist';
}

/** Footer button label (5491-5493). */
export function infoWatchButtonLabel(onWatchlist: boolean | number | undefined): string {
  return onWatchlist ? 'Remove from Watchlist' : 'Add to Watchlist';
}

/** What the button says AFTER it is clicked, before it disables (5492-5493). */
export function infoWatchButtonDone(onWatchlist: boolean | number | undefined): string {
  return onWatchlist ? 'Done' : 'Added!';
}

// ── The watchlist toggle (5513) ─────────────────────────────────────────────

export interface WatchlistRequest {
  url: string;
  body: Record<string, unknown>;
}

/**
 * Add and remove are different endpoints with different bodies (5517-5537).
 *
 * Remove sends only the id — the backend matches on it alone. Add also sends
 * the name and source, because a watchlist row that arrives without them shows
 * up as an un-named entry.
 */
export function watchlistRequest(
  isWatched: boolean,
  args: { sourceId: string; artistName: string; source: string },
): WatchlistRequest {
  if (isWatched) {
    return { url: '/api/watchlist/remove', body: { artist_id: args.sourceId } };
  }
  return {
    url: '/api/watchlist/add',
    body: { artist_id: args.sourceId, artist_name: args.artistName, source: args.source },
  };
}

export const WATCHLIST_TOGGLE_FAILED = 'Failed to update watchlist';

/** Toasts (5528, 5544) — different levels, deliberately. */
export function watchlistToast(
  isWatched: boolean,
  artistName: string,
): { message: string; level: 'info' | 'success' } {
  return isWatched
    ? { message: `Removed ${artistName} from watchlist`, level: 'info' }
    : { message: `Added ${artistName} to watchlist`, level: 'success' };
}

/** The eye icon's fill follows the state (5526, 5542). */
export function watchlistIconFill(watched: boolean): string {
  return watched ? 'currentColor' : 'none';
}

/**
 * The pool flag is written as 1/0, not true/false (5574).
 *
 * It arrives from SQLite as an integer and other code compares it loosely, so
 * the port keeps the numeric form rather than "tidying" it to a boolean.
 */
export function poolWatchlistValue(watched: boolean): number {
  return watched ? 1 : 0;
}

// ── Refresh (5578) ──────────────────────────────────────────────────────────

export const ARTISTS_REFRESH_POLL_MS = 5000;
export const ARTISTS_REFRESH_MAX_ATTEMPTS = 60;
export const ARTISTS_REFRESH_SUBTITLE = 'Refreshing from your services...';
export const ARTISTS_REFRESH_FAILED = 'Failed to start refresh';

/**
 * DIVERGENCE 1 — see the file header.
 *
 * The vanilla's give-up path (5590) is `clearInterval(poll); return;` with no
 * `btn.disabled = false`, so a refresh that never settles leaves the button
 * dead until the page is reloaded. Your Albums re-enables from its timeout.
 */
export const REFRESH_TIMEOUT_REENABLES_BUTTON = true;

/** `!data.stale && data.artists && data.artists.length > 0` (5594). */
export function artistsRefreshSettled(
  data: { stale?: boolean; artists?: unknown[] } | null | undefined,
): boolean {
  return Boolean(!data?.stale && data?.artists && data.artists.length > 0);
}

export function artistsRefreshToast(total: number): string {
  return `Found ${total} artists from your services`;
}

// ── The sources modal (5608) ────────────────────────────────────────────────

/** All FOUR are on by default here — unlike Your Albums, which excludes one. */
export const ARTISTS_DEFAULT_SOURCES = ['spotify', 'tidal', 'lastfm', 'deezer'];

export interface ArtistSourceInfo {
  id: string;
  label: string;
  icon: string;
}

/** 5624-5629. Last.fm replaces Discogs: you follow artists, you collect albums. */
export const ARTISTS_SOURCE_INFO: ArtistSourceInfo[] = [
  { id: 'spotify', label: 'Spotify', icon: '🎵' },
  { id: 'tidal', label: 'Tidal', icon: '🌊' },
  { id: 'lastfm', label: 'Last.fm', icon: '📻' },
  { id: 'deezer', label: 'Deezer', icon: '🎶' },
];

export function initialArtistSourcesState(enabled: string[]): Record<string, boolean> {
  const state: Record<string, boolean> = {};
  for (const s of ARTISTS_SOURCE_INFO) state[s.id] = enabled.includes(s.id);
  return state;
}

/**
 * DIVERGENCE 2 — see the file header.
 *
 * The vanilla returns silently here (5673, 5680). This returns the same hint
 * the Your Albums modal shows, from the shared table.
 */
export function toggleArtistSource(
  state: Record<string, boolean>,
  id: string,
  connected: string[],
): { state: Record<string, boolean>; hint: string | null } {
  if (!connected.includes(id)) return { state, hint: disconnectedHint(id) };
  return { state: { ...state, [id]: !state[id] }, hint: null };
}

/** The settings key differs from the albums one by a single word (5698). */
export function artistSourcesSavePayload(enabled: string[]): {
  discover: { your_artists_sources: string };
} {
  return { discover: { your_artists_sources: enabled.join(',') } };
}

/** `Artists you follow on ${names}` (5708) — ' and ', matching the subtitle. */
export function savedArtistSourcesSubtitle(enabled: string[]): string {
  const names = enabled.map((s) => ORIGIN_NAMES[s] || s).join(' and ');
  return `Artists you follow on ${names}`;
}

// ── The all-artists modal (5718) ────────────────────────────────────────────

/** 300ms here, against Your Albums' 400 (5763). Kept as-is: it is not a bug. */
export const ARTISTS_MODAL_SEARCH_DEBOUNCE_MS = 300;
export const ARTISTS_MODAL_PAGE_SIZE = 60;

export const ARTISTS_MODAL_EMPTY = 'No artists found';
export const ARTISTS_MODAL_ERROR = 'Failed to load';

export type ArtistsModalSort = 'name' | 'recent' | 'source';

/** The sort options in their rendered order (5746-5748). */
export const ARTISTS_MODAL_SORTS: { value: ArtistsModalSort; label: string }[] = [
  { value: 'name', label: 'A-Z' },
  { value: 'recent', label: 'Recently Added' },
  { value: 'source', label: 'By Source' },
];

/** The filter pills (5739-5743). The empty id is "All". */
export const ARTISTS_MODAL_FILTERS: { source: string; label: string }[] = [
  { source: '', label: 'All' },
  { source: 'spotify', label: 'Spotify' },
  { source: 'tidal', label: 'Tidal' },
  { source: 'lastfm', label: 'Last.fm' },
  { source: 'deezer', label: 'Deezer' },
];

export interface ArtistsModalState {
  page: number;
  source: string;
  sort: ArtistsModalSort;
  search: string;
}

export const INITIAL_ARTISTS_MODAL_STATE: ArtistsModalState = {
  page: 1,
  source: '',
  sort: 'name',
  search: '',
};

/**
 * DIVERGENCE 3 — see the file header.
 *
 * Every filter change resets to page 1. The vanilla only does this for the
 * source pills (5772); changing the search or the sort keeps the old page, so
 * searching from page 3 asks for page 3 of a smaller result set and renders an
 * empty grid with a "Prev" button as the only way out.
 */
export function applyArtistsModalFilter(
  state: ArtistsModalState,
  change: Partial<Pick<ArtistsModalState, 'source' | 'sort' | 'search'>>,
): ArtistsModalState {
  return { ...state, ...change, page: 1 };
}

/** Paging is the one change that does NOT reset the page. */
export function setArtistsModalPage(state: ArtistsModalState, page: number): ArtistsModalState {
  return { ...state, page };
}

/**
 * The query (5788-5790).
 *
 * `source` and `search` are omitted when empty — an empty `source=` would make
 * the server filter on the empty string instead of skipping the filter, which
 * is the same trap the albums grid guards against.
 */
export function artistsModalQuery(state: ArtistsModalState): Record<string, string> {
  const params: Record<string, string> = {
    page: String(state.page),
    per_page: String(ARTISTS_MODAL_PAGE_SIZE),
    sort: state.sort,
  };
  if (state.source) params.source = state.source;
  if (state.search) params.search = state.search;
  return params;
}

export function artistsModalSubtitle(total: number): string {
  return `${total} artists matched`;
}

export interface ArtistsModalPager {
  visible: boolean;
  totalPages: number;
  prevDisabled: boolean;
  nextDisabled: boolean;
  label: string;
}

/** Footer pager (5810-5821) — hidden entirely at one page, like the grid's. */
export function artistsModalPager(total: number, page: number): ArtistsModalPager {
  const totalPages = Math.ceil(total / ARTISTS_MODAL_PAGE_SIZE);
  return {
    visible: totalPages > 1,
    totalPages,
    prevDisabled: page <= 1,
    nextDisabled: page >= totalPages,
    label: `Page ${page} of ${totalPages}`,
  };
}
