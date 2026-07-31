/**
 * Artist Web — the sigma.js similarity graph, and the sibling of the canvas
 * Artist Map.
 *
 * Interaction is driven by REDUCERS: a little UI state lives on the singleton,
 * and the node/edge reducers read it to recolour and dim per frame. The graph
 * data is never mutated for visual state — which is why hovering, searching and
 * filtering are all instant and none of them can corrupt the graph.
 *
 * Transcribed from `webui/static/discover.js` 6527-6631 (state, palette, label
 * renderer, colour maps), 7014-7211 (the three lens builders + layout),
 * 7249-7317 (the spread effect), 7356-7457 (the reducers) and 7684-7753
 * (pathfinding).
 *
 * graphology and sigma stay CDN/UMD globals (index.html 10769-10771). The port
 * reads the same `window.graphology` / `window.Sigma` the vanilla did, so the
 * library versions and code paths are unchanged — bundling an ESM sigma would
 * add a variable to a port whose whole point is that nothing moves.
 */

// ── The graph shapes we depend on ────────────────────────────────────────────
// Structural types rather than an import: graphology is a UMD global here, and
// these are exactly the members the port touches.

export interface WebGraph {
  order: number;
  size: number;
  addNode(key: string, attrs: Record<string, unknown>): void;
  addEdge(source: string, target: string, attrs: Record<string, unknown>): void;
  hasNode(key: string): boolean;
  hasEdge(source: string, target: string): boolean;
  degree(key: string): number;
  source(edge: string): string;
  target(edge: string): string;
  getNodeAttribute(key: string, name: string): unknown;
  getNodeAttributes(key: string): Record<string, unknown>;
  setNodeAttribute(key: string, name: string, value: unknown): void;
  mergeNodeAttributes(key: string, attrs: Record<string, unknown>): void;
  mergeEdgeAttributes(edge: string, attrs: Record<string, unknown>): void;
  forEachNode(cb: (key: string, attrs: Record<string, unknown>) => void): void;
  forEachEdge(cb: (edge: string, attrs: Record<string, unknown>, source: string) => void): void;
  /** graphology's node-scoped overload — only the edges touching `key`. */
  forEachEdge(
    key: string,
    cb: (edge: string, attrs: Record<string, unknown>, source: string) => void,
  ): void;
  forEachNeighbor(key: string, cb: (nb: string, attrs: Record<string, unknown>) => void): void;
}

export type WebGraphCtor = new (opts?: { type?: string }) => WebGraph;

export interface WebRawNode {
  key: string;
  label: string;
  kind?: string;
  genre?: string;
  cluster?: string;
  primary_genre?: string;
  popularity?: number;
  thumb?: string | null;
  image_url?: string | null;
  genres?: string[] | null;
  ids?: [string, string][];
  id?: number | string | null;
  source?: string | null;
}

export interface WebRawEdge {
  source: string;
  target: string;
  weight: number;
  kind?: string;
}

export interface WebPayload {
  nodes?: WebRawNode[];
  edges?: WebRawEdge[];
  counts?: Record<string, number>;
}

export interface WebBuilt {
  graph: WebGraph;
  colorOf: (group: string) => string;
  counts: Record<string, number>;
  stats: string;
}

export type WebLens = 'genre' | 'community' | 'discovery';
export type WebSizeBy = 'popularity' | 'connections' | 'influence';

export interface ArtistWebState {
  sigma: unknown;
  graph: WebGraph | null;
  onKey: ((e: KeyboardEvent) => void) | null;
  /** open/close generation — in-flight fetches bail if it changed. */
  gen: number;
  lens: WebLens;
  data: WebPayload | null;
  discoveryData: WebPayload | null;
  genreColor: ((g: string) => string) | null;
  /** [{key,label}] artist nodes, for client-side search. */
  index: { key: string; label: string }[];
  searchMatch: Set<string> | null;
  focusSet: Set<string> | null;
  focusRoot: string | null;
  selectedKey: string | null;
  selectedFocus: Set<string> | null;
  genreFilter: Set<string> | null;
  genreCounts: Record<string, number> | null;
  sizeBy: WebSizeBy;
  betweenCache: Record<string, number> | null;
  edgeDeclutter: boolean;
  edgeThreshold: number;
  pathMode: boolean;
  pathSource: string | null;
  pathTarget: string | null;
  pathNodes: Set<string> | null;
  pathPairs: Set<string> | null;
  pathResult: string[] | null;
  simGraph: WebGraph | null;
  cursorFX: boolean;
  fxRAF: number | null;
  home: Record<string, { x: number; y: number }> | null;
  spreadRoot: string | null;
  spreadSet: Set<string> | null;
  spreadPush: number;
  spreadActive: Set<string> | null;
  fa2: { kill(): void } | null;
  fa2Timer: ReturnType<typeof setTimeout> | null;
  previewAudio: HTMLAudioElement | null;
  previewKey: string | null;
  _hoverNode: string | null;
  _mouse: { x: number; y: number } | null;
  _mouseBound: boolean;
  [key: string]: unknown;
}

/** The web's singleton (6527-6562), field for field. */
export const artistWeb: ArtistWebState = {
  sigma: null,
  graph: null,
  onKey: null,
  gen: 0,
  lens: 'genre',
  data: null,
  discoveryData: null,
  genreColor: null,
  index: [],
  searchMatch: null,
  focusSet: null,
  focusRoot: null,
  selectedKey: null,
  selectedFocus: null,
  genreFilter: null,
  genreCounts: null,
  sizeBy: 'popularity',
  betweenCache: null,
  edgeDeclutter: false,
  edgeThreshold: 2,
  pathMode: false,
  pathSource: null,
  pathTarget: null,
  pathNodes: null,
  pathPairs: null,
  pathResult: null,
  simGraph: null,
  cursorFX: true,
  fxRAF: null,
  home: null,
  spreadRoot: null,
  spreadSet: null,
  spreadPush: 0,
  spreadActive: null,
  fa2: null,
  fa2Timer: null,
  previewAudio: null,
  previewKey: null,
  _hoverNode: null,
  _mouse: null,
  _mouseBound: false,
};

// ── Palette ──────────────────────────────────────────────────────────────────

/** Distinct colours for the most-common genres (6587-6588). */
export const WEB_PALETTE = [
  '#1db954',
  '#e91e63',
  '#3f8cff',
  '#ff9800',
  '#9c27b0',
  '#00bcd4',
  '#ffd54f',
  '#f44336',
  '#8bc34a',
  '#ff5722',
  '#7c4dff',
  '#26c6da',
  '#cddc39',
  '#ff4081',
  '#009688',
  '#c0846b',
];
/** Slate-periwinkle for "Other" — a real colour, not dead gray (6589). */
export const WEB_GENRE_FALLBACK = '#6b7aa8';
/** Discovery lens: your library artists (cool blue). */
export const WEB_OWNED_COLOR = '#5b8def';
/** Discovery lens: unowned candidates to discover (warm amber). */
export const WEB_DISCOVERY_COLOR = '#ffb74d';
/** Near-black charcoal, so the cluster colours glow. */
export const WEB_CANVAS_BG = '#111016';
/** Dimmed nodes fade to a DARK gray — light + WebGL additive blending reads as white (7356). */
export const WEB_DIM_NODE = '#2b2b34';

/**
 * Edge opacity scales with weight, i.e. with consensus (6596-6598).
 *
 * Weak links stay faint so they do not clutter; strong, high-agreement links
 * come forward. Capped at 0.4 so nothing is ever fully opaque at rest — with
 * ~35k edges, opaque overlaps accumulate into a white haze.
 */
export function webEdgeAlpha(weight: number | undefined): number {
  return Math.min(0.4, 0.08 + (weight || 1) * 0.025);
}

/** Edge thickness, square-rooted so a weight-16 link is not 16x a weight-1 (6599-6601). */
export function webEdgeSize(weight: number | undefined): number {
  return 0.35 + Math.min(1.3, Math.sqrt(weight || 1) * 0.3);
}

/**
 * '#rrggbb' → 'rgba(r,g,b,a)' (6604-6609), so an edge can inherit its cluster's
 * colour at low alpha — that is what makes the web glow rather than look like
 * gray wiring.
 *
 * Anything that is not exactly six hex digits falls back to a neutral gray at
 * the requested alpha rather than throwing.
 */
export function webHexToRgba(hex: string | null | undefined, alpha: number): string {
  const h = (hex || '').replace('#', '');
  if (h.length !== 6) return `rgba(140,140,150,${alpha})`;
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/**
 * Map each genre CLUSTER to a palette colour (6613-6620).
 *
 * Clusters are ranked by member count and assigned palette entries in order,
 * cycling once the palette runs out — so no genre is ever left gray by accident.
 * "Other" is the single deliberate exception.
 */
export function webGenreColorMap(nodes: WebRawNode[]): {
  color: (g: string) => string;
  counts: Record<string, number>;
} {
  const counts: Record<string, number> = {};
  nodes.forEach((n) => {
    if (n.kind === 'artist' && n.cluster) counts[n.cluster] = (counts[n.cluster] || 0) + 1;
  });
  const ranked = Object.keys(counts)
    .filter((g) => g !== 'Other')
    .sort((a, b) => counts[b] - counts[a]);
  const map: Record<string, string> = { Other: WEB_GENRE_FALLBACK };
  ranked.forEach((g, i) => {
    map[g] = WEB_PALETTE[i % WEB_PALETTE.length];
  });
  return { color: (g: string) => map[g] || WEB_GENRE_FALLBACK, counts };
}

/** The top-N most popular artists become always-labelled landmarks (6623-6631). */
export const WEB_STAR_COUNT = 20;
export const WEB_STAR_SIZE = 8;

/**
 * The star set (6625-6631).
 *
 * Artists with zero (or missing) popularity are excluded outright, so a library
 * with no popularity data gets NO stars rather than twenty arbitrary ones.
 */
export function webTopArtists(artistNodes: WebRawNode[], n: number): Set<string> {
  return new Set(
    artistNodes
      .filter((a) => (a.popularity || 0) > 0)
      .sort((x, y) => (y.popularity || 0) - (x.popularity || 0))
      .slice(0, n)
      .map((a) => a.key),
  );
}

// ── The label renderer ───────────────────────────────────────────────────────

/**
 * The white pill label with black text (6566-6584) — a custom sigma
 * labelRenderer.
 *
 * Font and padding scale with the node's RENDERED size, so a small artist gets a
 * small tag and a big genre hub gets a big one, clamped to 8..18px so neither
 * end becomes unreadable or overwhelming.
 *
 * `roundRect` is feature-detected because it is not available everywhere; the
 * square fallback is a plain fillRect.
 */
export function webDrawLabel(
  context: CanvasRenderingContext2D,
  data: { label?: string; x: number; y: number; size?: number },
  settings: { labelFont?: string; labelWeight?: string },
): void {
  if (!data.label) return;
  const font = settings.labelFont || 'Arial';
  const weight = settings.labelWeight || 'normal';
  const fontSize = Math.max(8, Math.min(18, (data.size || 6) * 0.85));
  const pad = Math.max(3, Math.round(fontSize * 0.45));
  context.font = `${weight} ${fontSize}px ${font}`;
  const tw = context.measureText(data.label).width;
  const boxW = tw + pad * 2;
  const boxH = fontSize + pad * 2;
  const x = Math.round(data.x + (data.size as number) + 4); // sits just right of the node
  const y = Math.round(data.y - boxH / 2);
  context.fillStyle = '#ffffff';
  if (context.roundRect) {
    context.beginPath();
    context.roundRect(x, y, boxW, boxH, 5);
    context.fill();
  } else context.fillRect(x, y, boxW, boxH);
  context.fillStyle = '#000000';
  context.textBaseline = 'middle';
  context.textAlign = 'left';
  context.fillText(data.label, x + pad, data.y);
}

// ── Lens A: GENRE ────────────────────────────────────────────────────────────

/**
 * Every artist, grouped by genre-anchor hubs (7015-7061).
 *
 * The membership edges (artist → genre hub) exist for the LAYOUT only and are
 * never drawn — see the edge reducer. Clustering is conveyed by position and
 * colour instead, because rendering a thousand faint spokes out of one hub
 * accumulates into solid white.
 *
 * A duplicate edge is skipped rather than merged, so the first weight wins.
 */
export function artWebBuildGenre(data: WebPayload, Graph: WebGraphCtor): WebBuilt {
  const nodes = data.nodes || [];
  const edges = data.edges || [];
  const { color: colorOf, counts } = webGenreColorMap(nodes);
  const stars = webTopArtists(
    nodes.filter((n) => n.kind === 'artist'),
    WEB_STAR_COUNT,
  );
  const graph = new Graph();
  nodes.forEach((n) => {
    if (n.kind === 'genre') {
      const members = counts[n.genre as string] || 1;
      graph.addNode(n.key, {
        label: n.label,
        x: Math.random(),
        y: Math.random(),
        size: 6 + Math.sqrt(members) * 1.5,
        color: colorOf(n.genre as string),
        baseColor: colorOf(n.genre as string),
        forceLabel: true,
        kind: 'genre',
        genre: n.genre,
      });
    } else {
      const color = colorOf(n.cluster as string);
      const star = stars.has(n.key);
      graph.addNode(n.key, {
        label: n.label,
        x: Math.random(),
        y: Math.random(),
        size: star ? WEB_STAR_SIZE : 2 + Math.sqrt(n.popularity || 0) / 3,
        color: color,
        baseColor: color,
        kind: 'artist',
        genre: n.cluster,
        primaryGenre: n.primary_genre,
        popularity: n.popularity || 0,
        thumb: n.thumb || null,
        artistId: n.id != null ? n.id : null,
        source: n.source || null,
        isStar: star,
        forceLabel: star, // top artists are always-labeled landmarks
      });
      artistWeb.index.push({ key: n.key, label: n.label });
    }
  });
  edges.forEach((e) => {
    if (graph.hasNode(e.source) && graph.hasNode(e.target) && !graph.hasEdge(e.source, e.target)) {
      const membership = e.kind === 'membership';
      const base = (graph.getNodeAttribute(e.source, 'baseColor') as string) || WEB_GENRE_FALLBACK;
      graph.addEdge(e.source, e.target, {
        weight: e.weight,
        size: membership ? 0.35 : webEdgeSize(e.weight),
        color: webHexToRgba(base, webEdgeAlpha(e.weight)),
        baseColor: base, // the hex is kept so the reducer can brighten it on focus
        kind: e.kind,
      });
    }
  });
  const c = data.counts || {};
  const simCount = edges.filter((e) => e.kind === 'similarity').length;
  const stats = `${c.artists ?? nodes.length} artists · ${c.genres ?? '?'} genres · ${simCount} similarity links`;
  return { graph, colorOf, counts, stats };
}

// ── Lens B: COMMUNITIES ──────────────────────────────────────────────────────

/**
 * The similarity-connected core, clustered by Louvain and named by hub artist
 * (7064-7137).
 *
 * Only artists with at least one similarity link make it in — this is the
 * discoverable "taste" core, not the whole library. Each community is named
 * after its highest-degree member, with a collision guard appending the raw
 * community id if two communities would end up with the same name.
 *
 * Communities cycle the palette by size rank, so none goes gray.
 */
export function artWebBuildCommunity(
  data: WebPayload,
  Graph: WebGraphCtor,
  louvain?: ((g: WebGraph, opts: unknown) => Record<string, string | number>) | null,
): WebBuilt {
  const nodes = data.nodes || [];
  const edges = data.edges || [];
  const simEdges = edges.filter((e) => e.kind === 'similarity');
  const artistByKey: Record<string, WebRawNode> = {};
  nodes.forEach((n) => {
    if (n.kind === 'artist') artistByKey[n.key] = n;
  });
  const stars = webTopArtists(
    nodes.filter((n) => n.kind === 'artist'),
    WEB_STAR_COUNT,
  );

  const graph = new Graph();
  simEdges.forEach((e) =>
    [e.source, e.target].forEach((k) => {
      if (!graph.hasNode(k) && artistByKey[k]) {
        const n = artistByKey[k];
        const star = stars.has(k);
        graph.addNode(k, {
          label: n.label,
          x: Math.random(),
          y: Math.random(),
          size: star ? WEB_STAR_SIZE : 2 + Math.sqrt(n.popularity || 0) / 3,
          kind: 'artist',
          primaryGenre: n.primary_genre,
          popularity: n.popularity || 0,
          thumb: n.thumb || null,
          artistId: n.id != null ? n.id : null,
          source: n.source || null,
          isStar: star,
        });
      }
    }),
  );
  simEdges.forEach((e) => {
    if (graph.hasNode(e.source) && graph.hasNode(e.target) && !graph.hasEdge(e.source, e.target)) {
      graph.addEdge(e.source, e.target, { weight: e.weight, kind: 'similarity', size: 0.7 });
    }
  });

  let comm: Record<string, string | number> = {};
  if (louvain) {
    try {
      comm = louvain(graph, { getEdgeWeight: 'weight' });
    } catch {
      comm = {};
    }
  }

  // Group members; name each community by its highest-degree (most central) artist.
  const members: Record<string, string[]> = {};
  graph.forEachNode((k) => {
    const cid = String(comm[k] != null ? comm[k] : 'x');
    (members[cid] = members[cid] || []).push(k);
  });
  const commIds = Object.keys(members).sort((a, b) => members[b].length - members[a].length);
  const repOf: Record<string, string> = {};
  const colorByRep: Record<string, string> = {};
  const countsByRep: Record<string, number> = {};
  commIds.forEach((cid, i) => {
    let best: string | null = null;
    let bestDeg = -1;
    members[cid].forEach((k) => {
      const d = graph.degree(k);
      if (d > bestDeg) {
        bestDeg = d;
        best = k;
      }
    });
    let rep = best ? (graph.getNodeAttribute(best, 'label') as string) : 'Group ' + cid;
    if (countsByRep[rep] != null) rep = rep + ' · ' + cid; // guard a rare rep-name collision
    repOf[cid] = rep;
    colorByRep[rep] = WEB_PALETTE[i % WEB_PALETTE.length];
    countsByRep[rep] = members[cid].length;
    if (best) graph.setNodeAttribute(best, '_rep', true);
  });
  graph.forEachNode((k) => {
    const cid = String(comm[k] != null ? comm[k] : 'x');
    const rep = repOf[cid];
    const color = colorByRep[rep] || WEB_GENRE_FALLBACK;
    const isRep = graph.getNodeAttribute(k, '_rep') === true;
    const isStar = graph.getNodeAttribute(k, 'isStar') === true;
    graph.mergeNodeAttributes(k, {
      color,
      baseColor: color,
      genre: rep,
      forceLabel: isRep || isStar, // community leaders AND top artists are landmarks
      size: isRep
        ? Math.max(8, graph.getNodeAttribute(k, 'size') as number)
        : graph.getNodeAttribute(k, 'size'),
    });
    artistWeb.index.push({ key: k, label: graph.getNodeAttribute(k, 'label') as string });
  });
  graph.forEachEdge((edge, attrs, s) => {
    const base = (graph.getNodeAttribute(s, 'baseColor') as string) || WEB_GENRE_FALLBACK;
    const w = (attrs.weight as number) || 1;
    graph.mergeEdgeAttributes(edge, {
      color: webHexToRgba(base, webEdgeAlpha(w)),
      baseColor: base,
      size: webEdgeSize(w),
    });
  });

  const stats = `${graph.order} connected artists · ${commIds.length} communities · ${graph.size} links`;
  return {
    graph,
    colorOf: (rep: string) => colorByRep[rep] || WEB_GENRE_FALLBACK,
    counts: countsByRep,
    stats,
  };
}

// ── Lens C: DISCOVERY ────────────────────────────────────────────────────────

/** How many anchors get an always-on label (7147). */
export const WEB_STAR_ANCHORS = 25;

/**
 * Owned artists (cool) wired to their unowned similar candidates (warm)
 * (7140-7194).
 *
 * Anchor size scales with the size of its frontier, and only the biggest ~25 get
 * a permanent label — the full frontier has ~300 anchors, and labelling them all
 * would wall the view with pills.
 *
 * Each candidate is then sized by its STRONGEST similarity link: its best single
 * reason to go and listen to it.
 */
export function artWebBuildDiscovery(data: WebPayload, Graph: WebGraphCtor): WebBuilt {
  const nodes = data.nodes || [];
  const edges = data.edges || [];
  const anchorDeg: Record<string, number> = {};
  edges.forEach((e) => {
    anchorDeg[e.source] = (anchorDeg[e.source] || 0) + 1;
  });
  const starAnchors = new Set(
    Object.keys(anchorDeg)
      .sort((a, b) => anchorDeg[b] - anchorDeg[a])
      .slice(0, WEB_STAR_ANCHORS),
  );

  const graph = new Graph();
  nodes.forEach((n) => {
    if (n.kind === 'owned') {
      const deg = anchorDeg[n.key] || 1;
      graph.addNode(n.key, {
        label: n.label,
        x: Math.random(),
        y: Math.random(),
        size: 5 + Math.sqrt(deg) * 0.9,
        color: WEB_OWNED_COLOR,
        baseColor: WEB_OWNED_COLOR,
        forceLabel: starAnchors.has(n.key),
        kind: 'owned',
        genre: 'Your library',
        artistId: n.id != null ? n.id : null,
        thumb: n.thumb || null,
      });
    } else {
      graph.addNode(n.key, {
        label: n.label,
        x: Math.random(),
        y: Math.random(),
        size: 3,
        color: WEB_DISCOVERY_COLOR,
        baseColor: WEB_DISCOVERY_COLOR,
        kind: 'discovery',
        genre: 'Discovery',
        image_url: n.image_url || null,
        genresList: n.genres || null,
        ids: n.ids || [],
        popularity: n.popularity || 0,
      });
    }
    artistWeb.index.push({ key: n.key, label: n.label });
  });
  let maxW = 1;
  edges.forEach((e) => {
    if (graph.hasNode(e.source) && graph.hasNode(e.target) && !graph.hasEdge(e.source, e.target)) {
      if (e.weight > maxW) maxW = e.weight;
      graph.addEdge(e.source, e.target, {
        weight: e.weight,
        size: webEdgeSize(e.weight),
        // Weight-scaled alpha, like the other lenses — a fixed alpha washed out
        // around dense anchors once the full frontier's ~4.4k edges rendered.
        color: webHexToRgba(WEB_DISCOVERY_COLOR, webEdgeAlpha(e.weight)),
        baseColor: WEB_DISCOVERY_COLOR,
        kind: 'discovery',
      });
    }
  });
  graph.forEachNode((k, a) => {
    if (a.kind !== 'discovery') return;
    let w = 1;
    graph.forEachEdge(k, (_e, ea) => {
      if (((ea.weight as number) || 1) > w) w = ea.weight as number;
    });
    graph.setNodeAttribute(k, 'size', 2.5 + Math.sqrt(w / maxW) * 5);
  });
  const c = data.counts || {};
  const stats = `${c.owned ?? 0} of your artists · ${c.discovery ?? 0} to discover`;
  return { graph, colorOf: () => WEB_DISCOVERY_COLOR, counts: {}, stats };
}

// ── Post-layout bookkeeping ──────────────────────────────────────────────────

/**
 * Capture resting ("home") positions and scale interaction distances to the
 * settled coordinate range (6999-7012).
 *
 * FA2's output scale is not known up front, so the spread distance is derived
 * from the settled span rather than hard-coded — 3.5% of the longer side.
 */
export function artWebFinishLayout(graph: WebGraph): void {
  const home: Record<string, { x: number; y: number }> = {};
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  graph.forEachNode((k, a) => {
    home[k] = { x: a.x as number, y: a.y as number };
    if ((a.x as number) < minX) minX = a.x as number;
    if ((a.x as number) > maxX) maxX = a.x as number;
    if ((a.y as number) < minY) minY = a.y as number;
    if ((a.y as number) > maxY) maxY = a.y as number;
  });
  const span = Math.max(maxX - minX, maxY - minY) || 1;
  artistWeb.home = home;
  artistWeb.spreadPush = span * 0.035;
  artistWeb.spreadRoot = null;
  artistWeb.spreadSet = null;
}

/**
 * The declutter cutoff (6867-6874).
 *
 * The median SIMILARITY weight — but most edges are weight-1 (single-source), so
 * when the median IS the minimum it is bumped one above, which is what actually
 * hides that weakest tier instead of hiding nothing.
 */
export function artWebEdgeThreshold(graph: WebGraph): number {
  const w: number[] = [];
  graph.forEachEdge((_e, a) => {
    if (a.kind === 'similarity') w.push((a.weight as number) || 1);
  });
  w.sort((x, y) => x - y);
  let thr = w.length ? w[Math.floor(w.length * 0.5)] : 2;
  if (w.length && thr <= w[0]) thr = w[0] + 1;
  return thr;
}

// ── Node sizing ──────────────────────────────────────────────────────────────

/**
 * The metric behind the "Size by" toggle (6810-6822).
 *
 * Sizes run 2..5.5, matching the original build scale, and a star never shrinks
 * below 6 so landmarks stay landmarks whichever metric is picked. Genre hubs are
 * skipped entirely — they stay sized by member count.
 */
export function artWebNodeSize(metric: number, max: number, isStar: boolean): number {
  const size = 2 + Math.sqrt(metric / (max || 1)) * 3.5;
  return isStar ? Math.max(size, 6) : size;
}

/** Apply a size metric across the graph (6803-6824). */
export function artWebApplySize(
  graph: WebGraph,
  mode: WebSizeBy,
  simGraph: WebGraph | null,
  betweenness: Record<string, number> | null,
): void {
  const metric = (k: string, a: Record<string, unknown>): number => {
    if (mode === 'connections') return simGraph && simGraph.hasNode(k) ? simGraph.degree(k) : 0;
    if (mode === 'influence') return (betweenness || {})[k] || 0;
    return (a.popularity as number) || 0; // popularity
  };
  let max = 0;
  graph.forEachNode((k, a) => {
    if (a.kind === 'artist') {
      const v = metric(k, a);
      if (v > max) max = v;
    }
  });
  max = max || 1;
  graph.forEachNode((k, a) => {
    if (a.kind !== 'artist') return; // genre hubs stay sized by member count
    graph.setNodeAttribute(k, 'size', artWebNodeSize(metric(k, a), max, a.isStar === true));
  });
}

// ── Pathfinding ──────────────────────────────────────────────────────────────

/**
 * Build the similarity-only graph used for pathfinding (7684-7700).
 *
 * UNDIRECTED on purpose: similarity pairs are stored once, sorted, so a directed
 * graph would miss reverse traversals and report "no connection" for most pairs.
 *
 * Pathfinding deliberately runs here rather than on the DISPLAYED graph, so a
 * path always means "sounds like → sounds like" and never "both are tagged
 * Rock", which the membership-to-hub edges would otherwise allow.
 */
export function artWebSimGraph(
  data: WebPayload | null,
  Graph: WebGraphCtor | null,
): WebGraph | null {
  if (artistWeb.simGraph) return artistWeb.simGraph;
  if (!Graph || !data) return null;
  const g = new Graph({ type: 'undirected' });
  (data.edges || []).forEach((e) => {
    if (e.kind !== 'similarity') return;
    if (!g.hasNode(e.source)) g.addNode(e.source, {});
    if (!g.hasNode(e.target)) g.addNode(e.target, {});
    if (!g.hasEdge(e.source, e.target)) g.addEdge(e.source, e.target, { weight: e.weight || 1 });
  });
  artistWeb.simGraph = g;
  return g;
}

/** The consecutive-pair keys a path highlights, order-independent (7739-7742). */
export function artWebPathPairs(path: string[]): Set<string> {
  const pairs = new Set<string>();
  for (let i = 0; i + 1 < path.length; i++) {
    const a = path[i];
    const b = path[i + 1];
    pairs.add(a < b ? a + '|' + b : b + '|' + a);
  }
  return pairs;
}

/** How a found path reads in the panel (7789-7790). */
export function artWebPathSummary(path: string[]): { hops: number; between: number; via: string } {
  const hops = path.length - 1;
  const between = path.length - 2;
  return {
    hops,
    between,
    via:
      between === 0
        ? 'directly similar'
        : `via ${between} artist${between === 1 ? '' : 's'} in between`,
  };
}

// ── The reducers ─────────────────────────────────────────────────────────────

type NodeData = Record<string, unknown>;

/**
 * The node reducer (7358-7398) — the single place UI state becomes styling.
 *
 * The first line is the point: with nothing highlighting or filtering, the base
 * attributes are returned AS THEY ARE, with no per-node clone. That is the
 * common case and it runs once per node (~5k) on EVERY refresh — hover, pan,
 * keystroke.
 *
 * Precedence is path → genre filter → focus/search. Focus beats search, and a
 * search labels all of its hits while a focus labels only its root: labelling
 * every neighbour floods a big genre cluster with white pills.
 */
export function artWebNodeReducer(node: string, data: NodeData): NodeData {
  const st = artistWeb;
  if (!st.pathNodes && !st.genreFilter && !st.focusSet && !st.searchMatch) return data;
  const res = Object.assign({}, data);
  // Shortest-path mode takes priority: the chain lights up, everything else darkens.
  if (st.pathNodes) {
    if (st.pathNodes.has(node)) {
      res.color = data.baseColor || data.color;
      res.zIndex = 3;
      const isEnd = node === st.pathSource || node === st.pathTarget;
      res.forceLabel = isEnd || !!st.pathResult; // label the whole chain once complete
      if (isEnd) res.highlighted = true;
    } else {
      res.color = WEB_DIM_NODE;
      res.label = '';
      res.zIndex = 0;
    }
    return res;
  }
  // A persistent genre filter dims anything outside the chosen genres,
  // regardless of focus or search.
  if (st.genreFilter && !st.genreFilter.has(data.genre as string)) {
    res.color = WEB_DIM_NODE;
    res.label = '';
    res.zIndex = 0;
    return res;
  }
  const active = st.focusSet || st.searchMatch; // focus (hover/select) wins over search
  if (!active) return res;
  const searching = !st.focusSet && !!st.searchMatch;
  if (active.has(node)) {
    res.color = data.baseColor || data.color;
    res.zIndex = 2;
    res.forceLabel = searching || node === st.focusRoot;
    if (node === st.focusRoot) res.highlighted = true; // sigma draws a halo on the root
  } else {
    res.color = WEB_DIM_NODE;
    res.label = '';
    res.zIndex = 0;
  }
  return res;
}

/**
 * The edge reducer (7400-7457).
 *
 * Membership edges are NEVER rendered — they are a layout scaffold, and drawing
 * them starbursts big hubs into solid white.
 *
 * The resting fast-path matters even more here than for nodes: ~35k similarity
 * edges return with no clone and no source/target lookups. This was the biggest
 * per-refresh allocation sink.
 *
 * Declutter is scoped to `kind === 'similarity'` deliberately. The threshold is
 * computed from similarity weights, and applying it to the Discovery lens's
 * mostly-weight-1 edges hid essentially all of them, leaving candidates as
 * floating dots.
 */
export function artWebEdgeReducer(edge: string, data: NodeData, graph?: WebGraph | null): NodeData {
  const st = artistWeb;
  if (data.kind === 'membership') {
    const r = Object.assign({}, data);
    r.hidden = true;
    return r;
  }
  if (!st.pathNodes && !st.focusSet && !st.searchMatch && !st.genreFilter && !st.edgeDeclutter) {
    return data;
  }
  const res = Object.assign({}, data);
  if (
    st.edgeDeclutter &&
    data.kind === 'similarity' &&
    !st.pathNodes &&
    !st.focusSet &&
    !st.searchMatch &&
    ((data.weight as number) || 1) < st.edgeThreshold
  ) {
    res.hidden = true;
    return res;
  }
  const g = graph === undefined ? artistWeb.graph : graph;
  // Shortest-path mode: only the consecutive edges along the chain show.
  if (st.pathNodes) {
    if (g && st.pathPairs && st.pathPairs.size) {
      const s = g.source(edge);
      const t = g.target(edge);
      const key = s < t ? s + '|' + t : t + '|' + s;
      if (st.pathPairs.has(key)) {
        res.zIndex = 3;
        res.color = webHexToRgba((data.baseColor as string) || '#ffffff', 0.95);
        res.size = ((data.size as number) || 0.7) * 2.4;
        return res;
      }
    }
    res.hidden = true;
    return res;
  }
  if (st.genreFilter && g) {
    const sg = g.getNodeAttribute(g.source(edge), 'genre') as string;
    const tg = g.getNodeAttribute(g.target(edge), 'genre') as string;
    if (!(st.genreFilter.has(sg) && st.genreFilter.has(tg))) {
      res.hidden = true;
      return res;
    }
  }
  const active = st.focusSet || st.searchMatch;
  if (active && g) {
    const s = g.source(edge);
    const t = g.target(edge);
    // Focus: only edges fully INSIDE the set → a clean neighbourhood.
    // Search: any edge TOUCHING a match.
    const show = st.focusSet ? active.has(s) && active.has(t) : active.has(s) || active.has(t);
    if (show) {
      res.zIndex = 1;
      res.color = webHexToRgba((data.baseColor as string) || '#888888', 0.75);
      res.size = ((data.size as number) || 0.7) * 1.7;
    } else {
      res.hidden = true; // faint non-focus edges accumulate into a white haze
    }
  }
  return res;
}

// ── The node-spread effect ───────────────────────────────────────────────────

/** How hard a frame eases toward its target (7283). */
export const WEB_SPREAD_EASE = 0.18;
/** Below this the node is snapped home and dropped from the active set (7305). */
export const WEB_SPREAD_EPSILON = 0.0005;

/**
 * One frame of the spread effect (7276-7317).
 *
 * Nodes in the spread set ease toward their home PUSHED away from the selected
 * node; everything else eases back home. Only nodes that are actually displaced
 * are animated — `spreadActive` accumulates pushed nodes and drops each one as
 * it arrives home, so this is never a full ~5k-node sweep per frame.
 *
 * Returns whether anything moved, which is what decides if another frame runs.
 */
export function artWebSpreadTick(graph: WebGraph): boolean {
  const st = artistWeb;
  const home = st.home;
  if (!home) return false;
  const set = st.spreadSet;
  const root = st.spreadRoot;
  const rootHome = root && home[root] ? home[root] : null;
  const PUSH = st.spreadPush;
  const EASE = WEB_SPREAD_EASE;

  if (!st.spreadActive) st.spreadActive = new Set();
  if (set) set.forEach((k) => (st.spreadActive as Set<string>).add(k));

  let moving = false;
  (st.spreadActive as Set<string>).forEach((k) => {
    const h = home[k];
    if (!h) {
      (st.spreadActive as Set<string>).delete(k);
      return;
    }
    const ax = graph.getNodeAttribute(k, 'x') as number;
    const ay = graph.getNodeAttribute(k, 'y') as number;
    let tx = h.x;
    let ty = h.y;
    const pushed = !!(set && rootHome && set.has(k));
    if (pushed) {
      const dx = h.x - rootHome.x;
      const dy = h.y - rootHome.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.0001;
      tx = h.x + (dx / dist) * PUSH; // push each neighbour outward from the selection
      ty = h.y + (dy / dist) * PUSH;
    }
    const nx = ax + (tx - ax) * EASE;
    const ny = ay + (ty - ay) * EASE;
    if (Math.abs(nx - ax) > WEB_SPREAD_EPSILON || Math.abs(ny - ay) > WEB_SPREAD_EPSILON) {
      graph.setNodeAttribute(k, 'x', nx);
      graph.setNodeAttribute(k, 'y', ny);
      moving = true;
    } else if (!pushed) {
      // Reached home and not being pushed → snap exact + stop tracking it.
      graph.setNodeAttribute(k, 'x', tx);
      graph.setNodeAttribute(k, 'y', ty);
      (st.spreadActive as Set<string>).delete(k);
    }
  });
  return moving;
}

/**
 * Set the spread to a node's neighbours (7251-7262), or clear it when the node
 * has none.
 *
 * Genre hubs with hundreds of members are deliberately NOT excluded — the whole
 * cluster blooming outward from its label is the intended effect, and per-frame
 * cost is the full render either way.
 */
export function artWebSetSpread(root: string, focusSet: Set<string>): boolean {
  const st = artistWeb;
  if (!st.cursorFX || !st.home) return false;
  const neighbors = new Set(focusSet);
  neighbors.delete(root);
  if (neighbors.size === 0) {
    st.spreadRoot = null;
    st.spreadSet = null;
    return false;
  }
  st.spreadRoot = root;
  st.spreadSet = neighbors;
  return true;
}
