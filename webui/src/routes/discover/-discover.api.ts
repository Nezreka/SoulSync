/**
 * The discover page's server surface.
 *
 * 74 endpoints are reachable from live discover.js code. This file covers the
 * CORE page — hero, your-albums, your-artists, personalized shelves, seasonal,
 * the cache-backed browse shelves, the blacklist and the adventurousness dial.
 * The two graph subsystems own their own clients (`artist-map` / `graph/*`),
 * because they are effectively separate apps and land in their own phases.
 *
 * Three endpoints are deliberately ABSENT — they are only reachable from code
 * that Discover 2.0 orphaned, so porting them would resurrect dead surface:
 *   /api/discover/genre/<name>              <- openGenrePlaylist (dead)
 *   /api/discover/genres/available          <- _getGenreBrowserTabsCtrl (dead)
 *   /api/discover/personalized/daily-mixes  <- loadPersonalizedDailyMixes (dead)
 *
 * Response shapes were traced from the handlers in web_server.py, not guessed
 * from the consuming JS.
 */

import { apiClient, readJson } from '@/app/api-client';

import type { SectionOutcome } from './-discover.section-state';
import type {
  DiscoverAlbum,
  DiscoverArtist,
  DiscoverHeroResponse,
  DiscoverTrack,
  SeasonalResponse,
  SourcesResponse,
  YourAlbumsResponse,
} from './-discover.types';

import { DISCOVERY_SHUFFLE_LIMIT, YOUR_ALBUMS_PAGE_SIZE } from './-discover.types';

/** The `{success, error}` envelope every discover endpoint answers with. */
export interface DiscoverResult {
  success?: boolean;
  error?: string;
}

/**
 * Read helper for the SHELF endpoints.
 *
 * Returns the OUTCOME, not just the data. An earlier version swallowed every
 * failure into an empty array, which collapsed two distinct user-visible states
 * into one: the vanilla renders its EMPTY copy for `success: false` but its
 * ERROR copy (plus a toast on most sections) when the request actually throws.
 * `resolveSection` in -discover.section-state.ts needs to tell those apart, so
 * this hands back the raw payload or the error and decides nothing itself.
 *
 * Still never throws — one dead external service must not take the page down.
 */
async function section<T>(path: string): Promise<SectionOutcome<T>> {
  try {
    const data = await readJson<T>(apiClient.get(path));
    return { kind: 'ok', data };
  } catch (error) {
    return { kind: 'error', error };
  }
}

/**
 * Did this response succeed?
 *
 * Mirrors `_isSuccess` in discover-section-controller.js exactly, and the last
 * line is the part that matters: a payload with NO `success` key counts as a
 * SUCCESS, not a failure. Treating absent-as-failure would silently blank any
 * endpoint that returns a bare `{albums: [...]}`.
 */
export function isSuccess(data: Record<string, unknown> | null | undefined): boolean {
  if (!data) return false;
  if (Object.prototype.hasOwnProperty.call(data, 'success')) return Boolean(data.success);
  return true;
}

// ── Hero ──────────────────────────────────────────────────────────────────

/**
 * The cinematic billboard at the top of the page.
 *
 * `fallback: 'watchlist'` comes back when the active metadata source had
 * nothing to offer and the server filled from the watchlist instead — the UI
 * uses it to explain why the hero looks different, so it must not be dropped.
 */
export async function fetchHero(): Promise<DiscoverHeroResponse> {
  try {
    const data = await readJson<DiscoverHeroResponse>(apiClient.get('discover/hero'));
    return data?.success ? data : { success: false, artists: [] };
  } catch {
    return { success: false, artists: [] };
  }
}

// ── Your Albums / Your Artists ────────────────────────────────────────────

export interface YourAlbumsQuery {
  page?: number;
  per_page?: number;
  status?: 'all' | 'missing' | 'owned';
  search?: string;
  sort?: string;
}

/**
 * The owned/missing album grid — the one genuinely paginated shelf.
 *
 * `stale` tells the UI the cache is being rebuilt in the background, which is
 * why the grid can show rows AND a refreshing indicator at the same time.
 */
export function fetchYourAlbums(query: YourAlbumsQuery = {}): Promise<YourAlbumsResponse> {
  const searchParams: Record<string, string | number> = {
    page: query.page ?? 1,
    per_page: query.per_page ?? YOUR_ALBUMS_PAGE_SIZE,
  };
  if (query.status) searchParams.status = query.status;
  if (query.search) searchParams.search = query.search;
  if (query.sort) searchParams.sort = query.sort;
  return readJson<YourAlbumsResponse>(apiClient.get('discover/your-albums', { searchParams }));
}

/** Force a rebuild of the your-albums cache. `clear=true` drops it first. */
export function refreshYourAlbums(clear = true): Promise<DiscoverResult & { message?: string }> {
  return readJson(
    apiClient.post('discover/your-albums/refresh', { searchParams: { clear: String(clear) } }),
  );
}

export function fetchYourAlbumsSources(): Promise<SourcesResponse> {
  return readJson<SourcesResponse>(apiClient.get('discover/your-albums/sources'));
}

export function fetchYourArtists(): Promise<DiscoverResult & { artists?: DiscoverArtist[] }> {
  return readJson(apiClient.get('discover/your-artists'));
}

/** The full artist list behind the "see all" modal — separate from the shelf. */
export function fetchAllYourArtists(
  query: Record<string, string | number> = {},
): Promise<DiscoverResult & { artists?: DiscoverArtist[] }> {
  return readJson(apiClient.get('discover/your-artists/all', { searchParams: query }));
}

export function refreshYourArtists(clear = true): Promise<DiscoverResult & { message?: string }> {
  return readJson(
    apiClient.post('discover/your-artists/refresh', { searchParams: { clear: String(clear) } }),
  );
}

export function fetchYourArtistsSources(): Promise<SourcesResponse> {
  return readJson<SourcesResponse>(apiClient.get('discover/your-artists/sources'));
}

/**
 * One artist's detail for the hover/expand card.
 *
 * `name` rides along as a query param even though the id is in the path —
 * the server falls back to a name lookup when the id is from a source that is
 * no longer the active one.
 */
export function fetchArtistInfo(
  artistId: string,
  name: string,
): Promise<DiscoverResult & Record<string, unknown>> {
  return readJson(
    apiClient.get(`discover/your-artists/info/${encodeURIComponent(artistId)}`, {
      searchParams: { name },
    }),
  );
}

// ── Recommendation shelves ────────────────────────────────────────────────

export function fetchSimilarArtists(): Promise<
  DiscoverResult & { artists?: DiscoverArtist[]; count?: number; source?: string }
> {
  return readJson(apiClient.get('discover/similar-artists'));
}

/** Fill in images/genres for artists already on screen — fire-and-forget. */
export function enrichSimilarArtists(
  artists: unknown[],
): Promise<DiscoverResult & { artists?: DiscoverArtist[] }> {
  return readJson(apiClient.post('discover/similar-artists/enrich', { json: { artists } }));
}

/** #913 — play-weighted, consensus-ranked picks. */
export function fetchListeningRecommendations(): Promise<
  DiscoverResult & { artists?: DiscoverArtist[]; count?: number; source?: string }
> {
  return readJson(apiClient.get('discover/listening-recommendations'));
}

export const fetchListeningMix = () =>
  section<Record<string, unknown>>('discover/personalized/listening-mix');
export const fetchPopularPicks = () =>
  section<Record<string, unknown>>('discover/personalized/popular-picks');
export const fetchHiddenGems = () =>
  section<Record<string, unknown>>('discover/personalized/hidden-gems');

/** The shuffle shelf asks for a fixed limit — the vanilla hard-coded it inline. */
export const fetchDiscoveryShuffle = () =>
  section<Record<string, unknown>>(
    `discover/personalized/discovery-shuffle?limit=${DISCOVERY_SHUFFLE_LIMIT}`,
  );

export const fetchBecauseYouListenTo = () =>
  section<Record<string, unknown>>('discover/because-you-listen-to');

// ── Cache-backed browse shelves ───────────────────────────────────────────

export const fetchUndiscoveredAlbums = () =>
  section<Record<string, unknown>>('discover/undiscovered-albums');
export const fetchGenreNewReleases = () =>
  section<Record<string, unknown>>('discover/genre-new-releases');
export const fetchDeepCuts = () => section<Record<string, unknown>>('discover/deep-cuts');
export const fetchGenreExplorer = () => section<Record<string, unknown>>('discover/genre-explorer');
export const fetchRecentReleases = () =>
  section<Record<string, unknown>>('discover/recent-releases');

/**
 * Label explorer returns two lists from one call.
 *
 * Still an outcome like every other section — an earlier version swallowed
 * failures into two empty arrays, which is the exact bug this rework exists to
 * remove. The caller picks `albums` / `labels` off the payload.
 */
export function fetchLabelExplorer(): Promise<SectionOutcome<Record<string, unknown>>> {
  return section<Record<string, unknown>>('discover/label-explorer');
}

export function fetchGenreDeepDive(
  genre: string,
): Promise<DiscoverResult & { albums?: DiscoverAlbum[] }> {
  return readJson(apiClient.get('discover/genre-deep-dive', { searchParams: { genre } }));
}

/**
 * Map a cached shelf card back to a real album id so it can be opened.
 *
 * The cache stores name+artist only, so opening a card needs this round-trip
 * first.
 *
 * BOTH arguments are required, and that is the server's rule, not a choice
 * here — the handler does `if not name or not artist: return 400`. An earlier
 * draft of this client typed `artist` as optional and skipped the param when
 * falsy; that advertised an optionality the endpoint does not have. Missing and
 * empty behave identically (both 400), so this is a truthful signature rather
 * than a behaviour change.
 *
 * Worth knowing: one vanilla call site passes `item.artist_name || ''`, so a
 * cached card with no artist name always 400s there. Pre-existing, and the
 * caller treats a failed resolve as "cannot open" either way — but do not
 * mistake it for something the port broke.
 */
export function resolveCacheAlbum(
  name: string,
  artist: string,
): Promise<DiscoverResult & { entity_id?: string; source?: string }> {
  return readJson(
    apiClient.get('discover/resolve-cache-album', { searchParams: { name, artist } }),
  );
}

// ── Seasonal ──────────────────────────────────────────────────────────────

export function fetchSeasonalCurrent(): Promise<SeasonalResponse> {
  return readJson<SeasonalResponse>(apiClient.get('discover/seasonal/current'));
}

// ── Decades (the Time Machine shelf) ──────────────────────────────────────

export const fetchAvailableDecades = () =>
  section<Record<string, unknown>>('discover/decades/available');

export function fetchDecadeTracks(
  decade: number,
): Promise<DiscoverResult & { tracks?: DiscoverTrack[]; decade?: number; message?: string }> {
  return readJson(apiClient.get(`discover/decade/${decade}`));
}

// ── Adventurousness dial ──────────────────────────────────────────────────

export function fetchAdventurousness(): Promise<DiscoverResult & { value?: number }> {
  return readJson(apiClient.get('discover/adventurousness'));
}

export function setAdventurousness(value: number): Promise<DiscoverResult & { value?: number }> {
  return readJson(apiClient.post('discover/adventurousness', { json: { value } }));
}

// ── Artist blacklist ──────────────────────────────────────────────────────

export function fetchArtistBlacklist(): Promise<DiscoverResult & { entries?: unknown[] }> {
  return readJson(apiClient.get('discover/artist-blacklist'));
}

export function blacklistArtist(payload: Record<string, unknown>): Promise<DiscoverResult> {
  return readJson(apiClient.post('discover/artist-blacklist', { json: payload }));
}

export function unblacklistArtist(blacklistId: number): Promise<DiscoverResult> {
  return readJson(apiClient.delete(`discover/artist-blacklist/${blacklistId}`));
}

// ── Build-a-playlist ──────────────────────────────────────────────────────

export function searchBuildPlaylistArtists(
  query: string,
): Promise<DiscoverResult & { artists?: DiscoverArtist[] }> {
  return readJson(
    apiClient.get('discover/build-playlist/search-artists', { searchParams: { query } }),
  );
}

export function generateBuildPlaylist(
  payload: Record<string, unknown>,
): Promise<DiscoverResult & { playlist?: unknown }> {
  return readJson(apiClient.post('discover/build-playlist/generate', { json: payload }));
}

// ── ListenBrainz ──────────────────────────────────────────────────────────

export const fetchLbCreatedFor = () =>
  section<Record<string, unknown>>('discover/listenbrainz/created-for');
export const fetchLbUserPlaylists = () =>
  section<Record<string, unknown>>('discover/listenbrainz/user-playlists');
export const fetchLbCollaborative = () =>
  section<Record<string, unknown>>('discover/listenbrainz/collaborative');

export function fetchLbPlaylist(
  mbid: string,
): Promise<DiscoverResult & { tracks?: DiscoverTrack[]; track_count?: number }> {
  return readJson(apiClient.get(`discover/listenbrainz/playlist/${encodeURIComponent(mbid)}`));
}

export function refreshListenBrainz(): Promise<DiscoverResult & Record<string, unknown>> {
  return readJson(apiClient.post('discover/listenbrainz/refresh'));
}

// ── Download-bar snapshot (shared with downloads.js) ──────────────────────

/**
 * Persist / restore the discover download bar across reloads.
 *
 * This pair is the seam with the vanilla download plumbing: `discoverDownloads`
 * is read by downloads.js, shell-bridge.js and wishlist-tools.js, so the React
 * page has to keep feeding the same snapshot endpoints.
 */
export function snapshotDiscoverDownloads(payload: unknown): Promise<DiscoverResult> {
  return readJson(apiClient.post('discover_downloads/snapshot', { json: payload }));
}

export function hydrateDiscoverDownloads(): Promise<DiscoverResult & Record<string, unknown>> {
  return readJson(apiClient.get('discover_downloads/hydrate'));
}
