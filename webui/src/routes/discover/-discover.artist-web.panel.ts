/**
 * Artist Web — the legend and the side panel's four cards.
 *
 * Models, not markup, for the same reason the map's chrome is: React renders
 * them, and the numbers/labels/colours/ordering are what has to match.
 *
 * Transcribed from `webui/static/discover.js` 6899-6925 (legend),
 * 7770-7793 (the path panel), 7919-7972 (preview + radio) and 7974-8121
 * (the artist, genre and discovery cards).
 */

import {
  WEB_DISCOVERY_COLOR,
  WEB_GENRE_FALLBACK,
  WEB_OWNED_COLOR,
  type WebGraph,
  type WebLens,
} from './-discover.artist-web';

// ── The legend ───────────────────────────────────────────────────────────────

export interface WebLegendItem {
  color: string;
  label: string;
  count?: number;
}

/** How many groups the legend lists before it stops (6913). */
export const WEB_LEGEND_LIMIT = 8;

/**
 * What the node colours mean for the active lens (6899-6925).
 *
 * Discovery is a fixed two-item key; Genre and Community list their biggest
 * groups. Without this the palette is meaningless — sixteen colours with nothing
 * saying which is which.
 *
 * An empty result hides the box rather than showing an empty one.
 */
export function webLegendItems(
  lens: WebLens,
  counts: Record<string, number>,
  colorOf: (g: string) => string,
): WebLegendItem[] {
  if (lens === 'discovery') {
    return [
      { color: WEB_OWNED_COLOR, label: 'Your library' },
      { color: WEB_DISCOVERY_COLOR, label: 'To discover' },
    ];
  }
  const of = colorOf || (() => WEB_GENRE_FALLBACK);
  return Object.keys(counts || {})
    .sort((a, b) => counts[b] - counts[a])
    .slice(0, WEB_LEGEND_LIMIT)
    .map((g) => ({ color: of(g), label: g, count: counts[g] }));
}

// ── The artist card ──────────────────────────────────────────────────────────

export interface WebArtistCard {
  key: string;
  label: string;
  color: string;
  /** Total degree, INCLUDING membership edges — see the note below. */
  connections: number;
  popularity: number;
  primaryGenre: string;
  artistId: unknown;
  /** `/artist-detail/library/<db id>`, or null when the node has no library id. */
  detailPath: string | null;
  canPlayRadio: boolean;
  canExpand: boolean;
  expanded: boolean;
}

/**
 * An owned artist's card (7974-8028).
 *
 * Two things here look like bugs and are not:
 *
 * 1. `connections` is the raw DEGREE, so on the Genre lens it counts the
 *    membership edge to the artist's genre hub as well. The tooltip's count is
 *    similarity-only. The two genuinely disagree by one.
 * 2. the detail link is always the LIBRARY route. These are all owned artists,
 *    and `a.source` is the server name ('plex'), not a detail source — using it
 *    is what produced the broken `/artist-detail/plex/…` link.
 */
export function webArtistCard(
  graph: WebGraph,
  node: string,
  lens: WebLens,
  buildDetailPath: (id: unknown, source: string) => string,
): WebArtistCard {
  const a = graph.getNodeAttributes(node);
  return {
    key: node,
    label: (a.label as string) || '',
    color: (a.baseColor as string) || '#1db954',
    connections: graph.degree(node),
    popularity: Math.max(0, Math.min(100, Math.round((a.popularity as number) || 0))),
    primaryGenre: (a.primaryGenre as string) || '',
    artistId: a.artistId,
    detailPath: a.artistId != null ? buildDetailPath(a.artistId, 'library') : null,
    canPlayRadio: a.artistId != null,
    canExpand: lens === 'discovery',
    expanded: a.expanded === true,
  };
}

// ── The genre card ───────────────────────────────────────────────────────────

export interface WebGenreCard {
  genre: string;
  color: string;
  members: { key: string; label: string; pop: number }[];
  total: number;
}

/** How many members the genre card lists (8046). */
export const WEB_GENRE_MEMBERS = 30;

/**
 * A genre hub's card (8030-8053).
 *
 * Members are its ARTIST neighbours, most popular first, capped at thirty — the
 * count above the list is the true total, so a big genre reads honestly even
 * though only thirty are shown.
 */
export function webGenreCard(graph: WebGraph, node: string): WebGenreCard {
  const genre = (graph.getNodeAttribute(node, 'genre') as string) || 'Genre';
  const color = (graph.getNodeAttribute(node, 'baseColor') as string) || '#1db954';
  const members: { key: string; label: string; pop: number }[] = [];
  graph.forEachNeighbor(node, (nb, attrs) => {
    if (attrs.kind === 'artist') {
      members.push({
        key: nb,
        label: attrs.label as string,
        pop: (attrs.popularity as number) || 0,
      });
    }
  });
  members.sort((x, y) => y.pop - x.pop);
  return { genre, color, members: members.slice(0, WEB_GENRE_MEMBERS), total: members.length };
}

// ── The discovery card ───────────────────────────────────────────────────────

export interface WebDiscoveryCard {
  key: string;
  label: string;
  imageUrl: string | null;
  genres: string[];
  detailPath: string | null;
  canPreview: boolean;
}

/** How many genre pills a candidate shows (8067). */
export const WEB_DISCOVERY_GENRES = 5;

/**
 * An unowned candidate's card (8059-8093).
 *
 * `genresList` may arrive as a JSON STRING rather than an array, so it is parsed
 * defensively and a bare string falls back to a single-item list rather than
 * being spread into characters.
 *
 * There is deliberately NO expand button here: `similar_artists` only has rows
 * for artists whose similars SoulSync fetched (owned or watchlisted), so
 * expanding an unowned candidate always returns empty — validated 0 of 176 on
 * real data. The trail opens up once the candidate is watchlisted and scanned.
 *
 * The detail link uses the FIRST id pair, which the payload orders
 * spotify > deezer > itunes — an unowned artist still gets a detail page,
 * synthesised from the source.
 */
export function webDiscoveryCard(
  graph: WebGraph,
  node: string,
  buildDetailPath: (id: string, source: string) => string,
): WebDiscoveryCard {
  const a = graph.getNodeAttributes(node);
  let genres = a.genresList as unknown;
  if (typeof genres === 'string') {
    try {
      genres = JSON.parse(genres);
    } catch {
      genres = [genres];
    }
  }
  const list = Array.isArray(genres) ? genres.slice(0, WEB_DISCOVERY_GENRES) : [];
  const ids = (a.ids as [string, string][]) || [];
  const pair = ids[0];
  return {
    key: node,
    label: (a.label as string) || '',
    imageUrl: (a.image_url as string) || null,
    genres: list.map((g) => String(g)),
    detailPath: pair ? buildDetailPath(pair[1], pair[0]) : null,
    canPreview: ids.some((p) => p && p[0] === 'deezer'),
  };
}

// ── The path panel ───────────────────────────────────────────────────────────

export interface WebPathRow {
  key: string;
  label: string;
  color: string;
  /** 'start', 'end' or '' — the two ends are bolder and ringed. */
  tag: string;
}

/** One row per hop, with the ends marked (7776-7785). */
export function webPathRows(graph: WebGraph, path: string[]): WebPathRow[] {
  return path.map((k, i) => ({
    key: k,
    label: (graph.getNodeAttribute(k, 'label') as string) || k,
    color: (graph.getNodeAttribute(k, 'baseColor') as string) || '#1db954',
    tag: i === 0 ? 'start' : i === path.length - 1 ? 'end' : '',
  }));
}

// ── The preview button ───────────────────────────────────────────────────────

export const WEB_PREVIEW_IDLE = '▶ Preview top track';
export const WEB_PREVIEW_LOADING = 'Loading preview…';
export const WEB_PREVIEW_NONE = 'No preview available';
export const WEB_PREVIEW_UNAVAILABLE = 'Preview unavailable';
/** Loud enough to hear over nothing, quiet enough not to startle (7949). */
export const WEB_PREVIEW_VOLUME = 0.9;

/** `⏸ Windowlicker` while playing, falling back when the track is unnamed (7955). */
export function webPreviewPlayingLabel(track: string | undefined): string {
  return `⏸ ${track || 'Playing preview'}`;
}

// ── The help modal ───────────────────────────────────────────────────────────

/** The guide's sections (7862-7879) and its shortcut rows (7881-7884). */
export const WEB_HELP_SECTIONS = [
  { heading: 'Three lenses' },
  { heading: 'Explore' },
  { heading: 'Tools' },
];

export const WEB_SHORTCUTS: { keys: string[]; action: string }[] = [
  { keys: ['S'], action: 'Focus search' },
  { keys: ['F', '0'], action: 'Fit to view' },
  { keys: ['+', '-'], action: 'Zoom in / out' },
  { keys: ['Esc'], action: 'Back / close' },
];
