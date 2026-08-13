/**
 * Artist Map chrome — the info panel, the island nav, the tooltip and the
 * right-click menu.
 *
 * Every export here is a MODEL, not markup: the numbers, labels, colours and
 * ordering the vanilla computed before it interpolated them into an innerHTML
 * template. React renders them (P8); the differential suite drives the real
 * vanilla against a real DOM and reads its output back to check these against it.
 *
 * Transcribed from `webui/static/discover.js` 6149-6216 (island nav + jump menu),
 * 6247-6521 (watch button, panel, artist card), 9304-9352 (tooltip),
 * 9370-9401 (shortcuts) and 10043-10081 (context menu).
 */

import {
  type ArtMapIsland,
  type ArtMapNode,
  type ArtMapNodeId,
  artMap,
  artMapConnCount,
  artMapIsWatched,
  artMapNodeBest,
} from './-discover.artist-map';

// ── Stat tiles ───────────────────────────────────────────────────────────────

export interface MiniStat {
  label: string;
  value: string | number;
  color: string;
}

/**
 * One stat tile (6391-6396).
 *
 * A hue of `null`/`undefined` means "no genre tint" and renders white — note
 * that a hue of 0 is a real hue (red) and must NOT fall back, which is why the
 * vanilla tests `!= null` rather than truthiness.
 */
export function miniStat(label: string, value: string | number, hue?: number | null): MiniStat {
  return { label, value, color: hue != null ? `hsl(${hue},80%,80%)` : '#fff' };
}

// ── The panel's dashboard + top-artists list ─────────────────────────────────

export interface ArtMapPanelModel {
  title: string;
  /** The focused island in one-island mode, else null (the whole-map overview). */
  island: ArtMapIsland | null;
  hue: number;
  scopeTotal: number;
  scopeWatch: number;
  /** Watchlist coverage of the scope, 0-100, rounded. */
  coveragePct: number;
  stats: [MiniStat, MiniStat, MiniStat];
  topArtists: ArtMapNode[];
}

/** Nodes counted as "yours" in the coverage bar (6409/6417). */
function isOwned(n: ArtMapNode): boolean {
  return n.type === 'watchlist' || n.type === 'center';
}

/**
 * The panel header + body model (6399-6455).
 *
 * The scope is the focused island in one-island mode and the whole map
 * otherwise. Note `scopeTotal` comes from the ISLAND's count, which is the
 * genre's true size — so on a capped island the bar reads "12/900" and the
 * coverage is honestly small, rather than flattering you against the 300 bubbles
 * that happened to fit.
 *
 * Only the top 14 artists are listed, by popularity descending.
 *
 * (The vanilla also computes a whole-map `watch` tally at 6409 and never uses
 * it — `scopeWatch` is what reaches the markup. Not transcribed.)
 */
export function artMapPanelModel(): ArtMapPanelModel {
  const nodes = (artMap.placed || []).filter((n) => !n._isLabel);
  const total = nodes.length;
  const islands = artMap._islands || [];
  const oneIsland = artMap._oneIsland;
  const isl = oneIsland && islands.length ? islands[artMap._focusIdx || 0] : null;

  const scope = isl ? nodes.filter((n) => n._island === isl.name) : nodes;
  const scopeTotal = isl ? isl.count : total;
  const scopeWatch = scope.filter(isOwned).length;
  const coveragePct = scopeTotal ? Math.round((scopeWatch / scopeTotal) * 100) : 0;
  const hue = isl ? isl.hue : 270;

  return {
    title: artMap._mapTitle || 'Artist Map',
    island: isl,
    hue,
    scopeTotal,
    scopeWatch,
    coveragePct,
    stats: [
      miniStat('Artists', scopeTotal, hue),
      miniStat('Watchlist', scopeWatch),
      miniStat(isl ? 'Genre' : 'Genres', isl ? '1' : islands.length || 1),
    ],
    topArtists: scope
      .slice()
      .sort((a, b) => (b.popularity || 0) - (a.popularity || 0))
      .slice(0, ARTMAP_TOP_ARTISTS),
  };
}

/** How many artists the panel lists (6441). */
export const ARTMAP_TOP_ARTISTS = 14;

/** The coverage bar's gradient (6435) — the island hue rotated 40° for the far end. */
export function artMapCoverageGradient(hue: number): [string, string] {
  return [`hsl(${hue},80%,60%)`, `hsl(${(hue + 40) % 360},80%,62%)`];
}

// ── The watchlist button ─────────────────────────────────────────────────────

export interface ArtMapWatchButton {
  watched: boolean;
  label: string;
  background: string;
  borderColor: string;
}

/**
 * The panel card's watchlist button (6256-6261).
 *
 * Both states share the same shape and differ only in fill, border strength and
 * the star glyph — filled ★ when watched, hollow ☆ when not.
 */
export function artMapWatchButton(n: ArtMapNode): ArtMapWatchButton {
  const watched = artMapIsWatched(n);
  return {
    watched,
    label: watched ? '★ On watchlist' : '☆ Watchlist',
    background: watched ? 'rgba(192,132,252,0.3)' : 'rgba(192,132,252,0.12)',
    borderColor: `rgba(192,132,252,${watched ? '0.55' : '0.35'})`,
  };
}

// ── The artist card ──────────────────────────────────────────────────────────

export interface ArtMapArtistCard {
  id: ArtMapNodeId;
  name: string;
  hue: number;
  /** Live connections from the map's edges, not a stored count. */
  connections: number;
  /** Popularity clamped into 0-100 — it also drives the bar width as a percent. */
  popularity: number;
  genres: string[];
  best: { id: string; source: string };
  typeLabel: string;
  imageUrl: string;
  /** True when a decoded, circle-masked bitmap is already cached for this node. */
  hasBitmap: boolean;
  watch: ArtMapWatchButton;
}

/**
 * The rich artist card shown on hover/click (6458-6513).
 *
 * A cached bitmap beats the raw URL: it is already decoded and circle-masked, so
 * it paints instantly and cannot churn-blank the way a fresh `<img src>` does
 * while sweeping across dense bubbles.
 *
 * At most five genres are shown, and `typeLabel` reads "On watchlist" for both
 * watchlist and centre nodes — the explorer's centre artist is one of yours by
 * construction.
 */
export function artMapArtistCard(node: ArtMapNode): ArtMapArtistCard {
  return {
    id: node.id,
    name: node.name,
    hue: node._hue == null ? 270 : node._hue,
    connections: artMapConnCount(node),
    popularity: Math.max(0, Math.min(100, Math.round(node.popularity || 0))),
    genres: (node.genres || []).slice(0, 5),
    best: artMapNodeBest(node),
    typeLabel: isOwned(node) ? 'On watchlist' : 'Discovered',
    imageUrl: node.image_url || '',
    hasBitmap: !!artMap.images[node.id as string],
    watch: artMapWatchButton(node),
  };
}

// ── The island nav bar + its jump menu ───────────────────────────────────────

export interface ArtMapIslandNav {
  name: string;
  /** The genre name as the bar shows it — uppercased. */
  display: string;
  hue: number;
  count: number;
  /** 1-based, for "3 / 12". */
  position: number;
  total: number;
}

/**
 * The bottom-nav model (6149-6181), or null when the bar should not exist.
 *
 * The bar only appears in one-island mode; the vanilla removes the element
 * outright otherwise, which is what closing a map or switching to the explorer
 * relies on to tear down a stale nav.
 */
export function artMapIslandNav(): ArtMapIslandNav | null {
  const islands = artMap._islands || [];
  if (!artMap._oneIsland || islands.length < 1) return null;
  const idx = artMap._focusIdx || 0;
  const isl = islands[idx];
  if (!isl) return null;
  return {
    name: isl.name,
    display: (isl.name || '').toUpperCase(),
    hue: isl.hue,
    count: isl.count,
    position: idx + 1,
    total: islands.length,
  };
}

export interface ArtMapIslandMenuRow {
  index: number;
  name: string;
  hue: number;
  count: number;
  active: boolean;
}

/** Every island as a jump-menu row (6195-6203); the current one is marked. */
export function artMapIslandMenu(): ArtMapIslandMenuRow[] {
  const islands = artMap._islands || [];
  const cur = artMap._focusIdx || 0;
  return islands.map((isl, i) => ({
    index: i,
    name: isl.name,
    hue: isl.hue,
    count: isl.count,
    active: i === cur,
  }));
}

/**
 * Step to the prev/next island, wrapping at both ends (6138-6145), or null when
 * there is nothing to step between.
 */
export function artMapIslandNavStep(dir: number): number | null {
  const islands = artMap._islands || [];
  if (islands.length < 2) return null;
  let idx = (artMap._focusIdx || 0) + dir;
  if (idx < 0) idx = islands.length - 1;
  if (idx >= islands.length) idx = 0;
  return idx;
}

/** Clamp an island index into range, as focusing does (6085). */
export function artMapClampIsland(idx: number): number | null {
  const islands = artMap._islands || [];
  if (!islands.length) return null;
  return Math.max(0, Math.min(islands.length - 1, idx));
}

// ── The hover tooltip ────────────────────────────────────────────────────────

export interface ArtMapTooltip {
  name: string;
  /** Up to three genres — the tooltip is a glance, not the card. */
  genres: string[];
  /** '★ Watchlist' for a watched node, else empty. */
  badge: string;
  /** '4 connections' / '1 connection', or empty when there are none. */
  connectionText: string;
  connections: number;
  imageUrl: string;
  hasBitmap: boolean;
}

/**
 * The tooltip model (9304-9345).
 *
 * Note the badge here tests `type === 'watchlist'` ONLY — a centre node in the
 * explorer gets no star, unlike the card's `typeLabel`. Transcribed as-is.
 */
export function artMapTooltip(node: ArtMapNode): ArtMapTooltip {
  let conn = 0;
  for (const ed of artMap.edges || []) {
    if (ed.source === node.id || ed.target === node.id) conn++;
  }
  return {
    name: node.name,
    genres: (node.genres || []).slice(0, 3),
    badge: node.type === 'watchlist' ? '★ Watchlist' : '',
    connectionText: conn ? `${conn} connection${conn === 1 ? '' : 's'}` : '',
    connections: conn,
    imageUrl: node.image_url || '',
    hasBitmap: !!artMap.images[node.id as string],
  };
}

/**
 * Keep the tooltip on screen (9348-9351). It trails the pointer by 16px to the
 * right and 10px up, but is pulled back so its far edge stays 10px inside the
 * viewport.
 */
export function artMapTooltipPosition(
  clientX: number,
  clientY: number,
  tipW: number,
  tipH: number,
  viewW: number,
  viewH: number,
): { left: number; top: number } {
  return {
    left: Math.min(clientX + 16, viewW - tipW - 10),
    top: Math.min(clientY - 10, viewH - tipH - 10),
  };
}

// ── The right-click menu ─────────────────────────────────────────────────────

export interface ArtMapContextMenu {
  /** Whether "Artist Info" does anything — the modal needs one of three ids. */
  hasId: boolean;
  bestId: string;
  bestSource: string;
  watchLabel: string;
}

/**
 * The context menu's ids (10057-10071).
 *
 * This picker prefers the user's ACTIVE metadata source, unlike
 * `artMapNodeBest` (which always leads with spotify) and unlike the info
 * modal's (which reorders the whole chain per source). Three pickers, three
 * answers — transcribed rather than unified, because changing which one a menu
 * item uses changes which provider's page it opens.
 *
 * `bestSource` also has no "none" branch: with no ids at all it still says
 * 'deezer', paired with an empty id. The caller disables the link on the empty
 * id rather than on the source, so that is harmless — but it is not a bug to
 * "fix" here.
 */
export function artMapContextMenu(
  node: ArtMapNode,
  activeSource: string = (window as { _yaActiveSource?: string })._yaActiveSource || 'spotify',
): ArtMapContextMenu {
  const n = node as unknown as Record<string, string | undefined>;
  const hasId = !!(node.spotify_id || node.itunes_id || node.deezer_id);
  const bestId =
    n[activeSource + '_id'] || node.spotify_id || node.itunes_id || node.deezer_id || '';
  const bestSource = n[activeSource + '_id']
    ? activeSource
    : node.spotify_id
      ? 'spotify'
      : node.itunes_id
        ? 'itunes'
        : 'deezer';
  return {
    hasId,
    bestId,
    bestSource,
    watchLabel: node.type === 'watchlist' ? 'On Watchlist' : 'Add to Watchlist',
  };
}

// ── The shortcuts overlay ────────────────────────────────────────────────────

/** The shortcuts modal's rows, in order (9387-9396). */
export const ARTMAP_SHORTCUTS: { keys: string[]; action: string }[] = [
  { keys: ['Esc'], action: 'Close map' },
  { keys: ['+', '-'], action: 'Zoom in / out' },
  { keys: ['F'], action: 'Fit to view' },
  { keys: ['S'], action: 'Focus search' },
  { keys: ['H'], action: 'Toggle similar artists' },
  { keys: ['Scroll'], action: 'Zoom at cursor' },
  { keys: ['Click'], action: 'Artist info' },
  { keys: ['Right-click'], action: 'Context menu' },
  { keys: ['Drag'], action: 'Pan around' },
  { keys: ['Hover 1s'], action: 'Show connections' },
];

/** The mobile bottom sheet's transform (6334/6357). */
export function artMapSheetTransform(open: boolean): string {
  return `translateY(${open ? '0' : '100%'})`;
}
