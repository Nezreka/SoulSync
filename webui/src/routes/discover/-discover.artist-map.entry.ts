/**
 * Artist Map — the three ways in, and what each one feeds the layout engine.
 *
 * Watchlist, Genre and Explorer all end up in the same island layout; they
 * differ in where the nodes come from, whether one-island mode is on, and what
 * the toolbar says.
 *
 * Transcribed from `webui/static/discover.js` 8273-8366 (watchlist),
 * 9403-9624 (genre picker + genre map), 9626-9842 (explorer + its prompt),
 * 9241-9302 (toolbar search) and 10285-10332 (the artist info hand-off).
 */

import type { ArtMapEdge, ArtMapNode, ArtMapRawNode } from './-discover.artist-map';

// ── Endpoints ────────────────────────────────────────────────────────────────

export const ARTMAP_URL = '/api/discover/artist-map';
export const ARTMAP_GENRES_URL = '/api/discover/artist-map/genres';
export const ARTMAP_GENRE_LIST_URL = '/api/discover/artist-map/genre-list';
export const ARTMAP_EXPLORE_URL = '/api/discover/artist-map/explore';
export const ARTMAP_SEARCH_URL = '/api/discover/build-playlist/search-artists';

/** The toolbar search debounces this long; the explorer prompt uses 350 (9828). */
export const ARTMAP_SEARCH_DEBOUNCE_MS = 300;
export const ARTMAP_PROMPT_DEBOUNCE_MS = 350;
/** Below this many characters the dropdown closes rather than searching (9245). */
export const ARTMAP_SEARCH_MIN_CHARS = 2;
/** The dropdown shows at most this many hits (9261). */
export const ARTMAP_SEARCH_LIMIT = 8;

export const ARTMAP_EMPTY_WATCHLIST = 'No watchlist artists. Add artists to your watchlist first.';
export const ARTMAP_LOADING = 'Building artist map...';
export const ARTMAP_SEARCH_EMPTY = 'No artists found';
export const ARTMAP_SEARCH_FAILED = 'Search failed — try again';
export const ARTMAP_SEARCHING = 'Searching…';

// ── The watchlist map ────────────────────────────────────────────────────────

export interface ArtMapWatchlistPayload {
  success?: boolean;
  nodes?: ArtMapRawNode[];
  edges?: ArtMapEdge[];
  watchlist_count?: number;
  similar_count?: number;
}

/**
 * Mark which artists are FOCAL before grouping (8324).
 *
 * A focal node sorts to the centre of its island and is drawn 45% larger. For
 * the watchlist map that is anything you actually watch.
 */
export function artMapWatchlistNodes(payload: ArtMapWatchlistPayload): ArtMapRawNode[] {
  return (payload.nodes || []).map((n) => ({ ...n, _focal: n.type === 'watchlist' }));
}

/** `12 watchlist · 340 similar` (8317-8318). */
export function artMapWatchlistStats(payload: ArtMapWatchlistPayload): string {
  return `${payload.watchlist_count} watchlist · ${payload.similar_count} similar`;
}

/** A payload with no nodes is an empty watchlist, not an error (8312). */
export function artMapPayloadIsEmpty(payload: { success?: boolean; nodes?: unknown[] }): boolean {
  return !payload.success || !payload.nodes?.length;
}

// ── The genre map ────────────────────────────────────────────────────────────

export interface ArtMapGenre {
  name: string;
  count: number;
  artist_ids: (number | string)[];
}

export interface ArtMapGenrePayload {
  success?: boolean;
  nodes?: Record<string, ArtMapRawNode>;
  genres?: ArtMapGenre[];
}

/** At most this many related genres join the one you picked (9585). */
export const ARTMAP_RELATED_GENRES = 4;
/** A genre must share at least this fraction of the primary's artists (9583). */
export const ARTMAP_GENRE_OVERLAP = 0.1;

/**
 * The genres shown alongside the one you picked (9567-9587).
 *
 * Relatedness is measured by ARTIST OVERLAP rather than by name or tag: a genre
 * qualifies when more than 10% of the primary genre's artists also carry it, and
 * the top four by overlap join the map.
 *
 * Returns null when the picked genre is not in the payload at all — the caller
 * shows `Genre "x" not found.` rather than an empty map.
 */
export function artMapRelatedGenres(
  allGenres: ArtMapGenre[],
  selectedGenre: string,
): ArtMapGenre[] | null {
  const primary = allGenres.find((g) => g.name === selectedGenre);
  if (!primary) return null;
  const primarySet = new Set(primary.artist_ids);
  const related = allGenres
    .filter((g) => g.name !== selectedGenre)
    .map((g) => ({ ...g, overlap: g.artist_ids.filter((id) => primarySet.has(id)).length }))
    .filter((g) => g.overlap > primarySet.size * ARTMAP_GENRE_OVERLAP)
    .sort((a, b) => b.overlap - a.overlap)
    .slice(0, ARTMAP_RELATED_GENRES);
  return [primary, ...related];
}

/**
 * Turn the chosen genres into layout groups (9595-9599).
 *
 * `data.nodes` is keyed by artist id, so a genre's `artist_ids` are looked up
 * and anything missing is dropped — the count on the label still reports the
 * genre's real size.
 */
export function artMapGenreGroups(
  genres: ArtMapGenre[],
  nodes: Record<string, ArtMapRawNode>,
): { name: string; count: number; nodes: ArtMapRawNode[] }[] {
  return genres.map((g) => ({
    name: g.name,
    count: g.count,
    nodes: (g.artist_ids || []).map((nid) => nodes[nid as string]).filter(Boolean),
  }));
}

/** `Rock ▾ · 3 genres · 812 artists` (9590-9591), minus the clickable wrapper. */
export function artMapGenreStats(genres: ArtMapGenre[]): { count: number; artists: number } {
  return {
    count: genres.length,
    artists: genres.reduce((sum, g) => sum + g.artist_ids.length, 0),
  };
}

// ── The explorer ─────────────────────────────────────────────────────────────

export interface ArtMapExplorePayload {
  success?: boolean;
  nodes?: (ArtMapRawNode & { ring?: number })[];
  edges?: ArtMapEdge[];
  center?: string;
}

/**
 * The explorer's focal test (9708) — ring 0 OR an explicit centre type.
 *
 * Both are checked because the endpoint has used each at different times, and a
 * centre artist that failed the test would be laid out as an ordinary bubble in
 * the middle of its genre island rather than as the thing you asked about.
 */
export function artMapExploreNodes(payload: ArtMapExplorePayload): ArtMapRawNode[] {
  return (payload.nodes || []).map((n) => ({ ...n, _focal: n.ring === 0 || n.type === 'center' }));
}

/** `Aphex Twin · 20 similar · 140 extended` (9699-9702). */
export function artMapExploreStats(payload: ArtMapExplorePayload): string {
  const nodes = payload.nodes || [];
  const ring1 = nodes.filter((n) => n.ring === 1).length;
  const ring2 = nodes.filter((n) => n.ring === 2).length;
  return `${payload.center} · ${ring1} similar · ${ring2} extended`;
}

/**
 * What to say when the explorer finds nothing (9687-9689).
 *
 * A 404 means the name is not an artist at all; anything else means it is, but
 * there is no similarity data for it. Two different fixes for the user, so two
 * different messages.
 */
export function artMapExploreEmptyMessage(status: number, name: string): string {
  return status === 404
    ? `"${name}" doesn't appear to be a real artist. Try a different name.`
    : `No data found for "${name}". Try a different artist.`;
}

/** How long the failure message stays up before the map closes itself (9695). */
export const ARTMAP_EXPLORE_FAIL_MS = 2500;

/** The toolbar brand text per entry point (9555 / 9679). */
export const ARTMAP_TITLES = {
  watchlist: 'Artist Map',
  genre: 'Genre Map',
  explorer: 'Artist Explorer',
};

/** The panel's own title, which is not the same string (8329 / 9603 / 9713). */
export function artMapPanelTitle(kind: 'watchlist' | 'genre' | 'explorer', center?: string) {
  if (kind === 'watchlist') return 'Watchlist Map';
  if (kind === 'genre') return 'Genre Map';
  return 'Explore: ' + (center || '');
}

/**
 * Which entry points focus ONE island at a time (8328 / 9602 / 9712).
 *
 * Explore stays multi-island because it is small — a couple of dozen artists,
 * where framing one genre at a time would hide the shape you came to see.
 */
export function artMapUsesOneIsland(kind: 'watchlist' | 'genre' | 'explorer'): boolean {
  return kind !== 'explorer';
}

// ── The toolbar search ───────────────────────────────────────────────────────

export function artMapSearchUrl(query: string): string {
  return `${ARTMAP_SEARCH_URL}?query=${encodeURIComponent(query)}`;
}

/** Whether a query is long enough to search on (9245). */
export function artMapSearchShouldRun(query: string | null | undefined): boolean {
  return (query || '').trim().length >= ARTMAP_SEARCH_MIN_CHARS;
}

/** The artists a response contributes, capped (9256-9261). */
export function artMapSearchResults(data: {
  success?: boolean;
  artists?: { name: string; image_url?: string }[];
}): { name: string; image_url?: string }[] {
  const artists = data && data.success && Array.isArray(data.artists) ? data.artists : [];
  return artists.slice(0, ARTMAP_SEARCH_LIMIT);
}

// ── The artist info hand-off ─────────────────────────────────────────────────

/**
 * The source priority for the info modal (10289-10295).
 *
 * A THIRD ordering, distinct from `artMapNodeBest` (always spotify-first) and
 * from the context menu (active source, then a fixed spotify/itunes/deezer
 * tail). Here the active source leads and the REST of the chain reorders behind
 * it. Transcribed as-is: which one a caller uses decides which provider's page
 * opens.
 */
export function artMapInfoSourceOrder(activeSource: string): string[] {
  if (activeSource === 'spotify') {
    return ['spotify_id', 'itunes_id', 'deezer_id', 'discogs_id', 'musicbrainz_id'];
  }
  if (activeSource === 'itunes') {
    return ['itunes_id', 'spotify_id', 'deezer_id', 'discogs_id', 'musicbrainz_id'];
  }
  if (activeSource === 'deezer') {
    return ['deezer_id', 'spotify_id', 'itunes_id', 'discogs_id', 'musicbrainz_id'];
  }
  if (activeSource === 'musicbrainz') {
    return ['musicbrainz_id', 'spotify_id', 'itunes_id', 'deezer_id', 'discogs_id'];
  }
  return ['spotify_id', 'itunes_id', 'deezer_id', 'discogs_id', 'musicbrainz_id'];
}

const INFO_SOURCE_NAMES: Record<string, string> = {
  spotify_id: 'spotify',
  itunes_id: 'itunes',
  deezer_id: 'deezer',
  discogs_id: 'discogs',
  musicbrainz_id: 'musicbrainz',
};

/** The first populated id in the active source's ordering (10296-10298). */
export function artMapInfoBest(
  node: ArtMapNode,
  activeSource: string,
): { id: string; source: string } {
  const rec = node as unknown as Record<string, string | undefined>;
  for (const key of artMapInfoSourceOrder(activeSource)) {
    if (rec[key]) return { id: rec[key] as string, source: INFO_SOURCE_NAMES[key] };
  }
  return { id: '', source: '' };
}

/**
 * Every artist connected to this one, in EITHER direction (10300-10313).
 *
 * Deliberately broader than the hover constellation, which walks watchlist →
 * similar in one direction. The modal is a reference view, so it lists everyone
 * the map wired to this artist however the edge was oriented, de-duplicated and
 * in edge order.
 */
export function artMapRelatedNodes(
  node: ArtMapNode,
  edges: ArtMapEdge[],
  nodeById: Record<string, ArtMapNode>,
): ArtMapNode[] {
  const related: ArtMapNode[] = [];
  const relatedIds = new Set<unknown>();
  edges.forEach((e) => {
    if (e.source === node.id && nodeById[e.target as string] && !relatedIds.has(e.target)) {
      related.push(nodeById[e.target as string]);
      relatedIds.add(e.target);
    }
    if (e.target === node.id && nodeById[e.source as string] && !relatedIds.has(e.source)) {
      related.push(nodeById[e.source as string]);
      relatedIds.add(e.source);
    }
  });
  return related;
}

export interface ArtMapPoolEntry {
  id: ArtMapNode['id'];
  artist_name: string;
  active_source_id: string;
  active_source: string;
  image_url: string;
  spotify_artist_id: string;
  itunes_artist_id: string;
  deezer_artist_id: string;
  discogs_artist_id: string;
  source_services: string[];
  on_watchlist: number;
  _related: ArtMapNode[];
}

/**
 * The pool entry the info modal reads (10315-10331).
 *
 * The modal is the Your Artists one, which looks its subject up in
 * `window._yaArtists` by id — so a map node is translated into that shape and
 * parked there before the modal opens. `_related` is the only field the map adds
 * that a real Your Artists row never has, which is why the modal's "Connected
 * To" section only ever appears when it was opened from the map.
 */
export function artMapPoolEntry(
  node: ArtMapNode,
  activeSource: string,
  related: ArtMapNode[],
): ArtMapPoolEntry {
  const best = artMapInfoBest(node, activeSource);
  return {
    id: node.id,
    artist_name: node.name,
    active_source_id: best.id,
    active_source: best.source,
    image_url: node.image_url || '',
    spotify_artist_id: node.spotify_id || '',
    itunes_artist_id: node.itunes_id || '',
    deezer_artist_id: node.deezer_id || '',
    discogs_artist_id: node.discogs_id || '',
    source_services: [],
    on_watchlist: node.type === 'watchlist' ? 1 : 0,
    _related: related,
  };
}
