/**
 * Artist Web — lifecycle, layout supervision, selection and path mode.
 *
 * Transcribed from `webui/static/discover.js` 6633-6995 (open, lenses, the FA2
 * supervisor), 7319-7351 (close), 7459-7541 (search + camera), 7543-7679
 * (hover, selection, tooltip) and 7681-7768 (path mode).
 *
 * The decisions live here as testable functions; the DOM and the sigma instance
 * are reached through a host, the same shape the map's interaction layer uses.
 */

import {
  type ArtistWebState,
  type WebGraph,
  type WebGraphCtor,
  type WebLens,
  type WebPayload,
  type WebSizeBy,
  artistWeb,
} from './-discover.artist-web';

// ── Endpoints + copy ─────────────────────────────────────────────────────────

export const WEB_LIBRARY_URL = '/api/graph/library';
export const WEB_DISCOVERY_URL = '/api/graph/discovery';
export const WEB_EXPAND_URL = '/api/graph/discovery/expand';
export const WEB_PREVIEW_URL = '/api/graph/discovery/preview';
export const WEB_THUMB_URL = '/api/library/artist';

export const WEB_BUILDING = 'Building your artist web…';
export const WEB_REBUILDING = 'Rebuilding…';
export const WEB_LOAD_FAILED = 'Failed to load the artist web.';
export const WEB_DISCOVERY_FAILED = 'Failed to load discovery.';
export const WEB_NO_LIBS = "graphology / sigma didn't load — check the CDN &lt;script&gt; tags.";
export const WEB_NO_CANDIDATES =
  'No discovery candidates yet — add artists to your Watchlist so SoulSync can fetch similar artists to explore.';
export const WEB_PATH_PROMPT = 'Click an artist, then a second one, to trace how they connect.';
export const WEB_PATH_NOT_ARTISTS = 'Pick artists, not genre hubs.';
export const WEB_FIRST_RUN_HINT =
  'Hover to identify · click an artist to explore · <b>?</b> for the guide';
export const WEB_FIRST_RUN_KEY = 'artweb_seen_hint';
/** How long the first-run pill stays up, then how long it takes to fade (7844). */
export const WEB_HINT_MS = 8000;
export const WEB_HINT_FADE_MS = 400;

// ── The CDN globals ──────────────────────────────────────────────────────────

interface WebGlobals {
  graphology?: { Graph?: WebGraphCtor } | WebGraphCtor;
  Sigma?: unknown;
  graphologyLibrary?: {
    FA2Layout?: unknown;
    layoutForceAtlas2?: {
      assign(g: WebGraph, opts: unknown): void;
      inferSettings(g: WebGraph): Record<string, unknown>;
    };
    layout?: { circlepack?: { assign(g: WebGraph, opts: unknown): void } };
    communitiesLouvain?: (g: WebGraph, opts: unknown) => Record<string, string | number>;
    shortestPath?: { bidirectional?: (g: WebGraph, a: string, b: string) => string[] | null };
    metrics?: {
      centrality?: {
        betweenness?: (g: WebGraph) => Record<string, number>;
        betweennessCentrality?: (g: WebGraph) => Record<string, number>;
      };
    };
  };
}

/**
 * graphology's UMD default export IS the Graph class (6686), so the port has to
 * accept either shape — `window.graphology.Graph` on some builds and
 * `window.graphology` itself on others.
 */
export function webResolveGraph(
  w: WebGlobals = window as unknown as WebGlobals,
): WebGraphCtor | null {
  const g = w.graphology;
  if (!g) return null;
  return ((g as { Graph?: WebGraphCtor }).Graph || g) as WebGraphCtor;
}

/** Both libraries have to be there before anything is worth attempting (6687). */
export function webLibsReady(w: WebGlobals = window as unknown as WebGlobals): boolean {
  return !!webResolveGraph(w) && !!w.Sigma;
}

/** Louvain, if the CDN bundle shipped it (7093). */
export function webLouvain(w: WebGlobals = window as unknown as WebGlobals) {
  return w.graphologyLibrary?.communitiesLouvain || null;
}

/**
 * Betweenness centrality, under either of its two names (6783-6784).
 *
 * Computed on the SIMILARITY graph — roughly a thousand nodes — not the ~5k
 * display graph, and cached, because a genre-hub-laden graph would be slow.
 */
export function webBetweenness(
  simGraph: WebGraph | null,
  w: WebGlobals = window as unknown as WebGlobals,
): Record<string, number> {
  if (artistWeb.betweenCache) return artistWeb.betweenCache;
  const c = w.graphologyLibrary?.metrics?.centrality;
  const fn = c && (c.betweenness || c.betweennessCentrality);
  let res: Record<string, number> = {};
  if (fn && simGraph) {
    try {
      res = fn(simGraph);
    } catch {
      res = {};
    }
  }
  artistWeb.betweenCache = res;
  return res;
}

/** The bidirectional shortest path, or null when unavailable (7748-7753). */
export function webComputePath(
  simGraph: WebGraph | null,
  a: string,
  b: string,
  w: WebGlobals = window as unknown as WebGlobals,
): string[] | null {
  const sp = w.graphologyLibrary?.shortestPath;
  if (!sp || !sp.bidirectional || !simGraph || !simGraph.hasNode(a) || !simGraph.hasNode(b)) {
    return null;
  }
  try {
    return sp.bidirectional(simGraph, a, b);
  } catch {
    return null;
  }
}

// ── Layout ───────────────────────────────────────────────────────────────────

/**
 * The forceAtlas2 settings, shared by the worker and the synchronous fallback
 * (6954-6961 / 7202-7209).
 *
 * linLog separates clusters into islands, outbound attraction spreads the hubs,
 * and adjustSizes stops node circles overlapping. Changing any of these changes
 * the shape of every map.
 */
export function webLayoutSettings(inferred: Record<string, unknown>): Record<string, unknown> {
  return {
    ...inferred,
    barnesHutOptimize: true,
    linLogMode: true,
    outboundAttractionDistribution: true,
    adjustSizes: true,
    gravity: 1.2,
    scalingRatio: 3,
    slowDown: 4,
  };
}

/** The synchronous fallback runs a fixed iteration count (7201). */
export const WEB_SYNC_ITERATIONS = 800;

/**
 * How long the live worker layout is allowed to settle (6975).
 *
 * Wall-clock, not iterations — a worker iterates as fast as it can. It scales
 * with graph size and is capped at 11s, and the pre-seed is what makes the
 * larger budget pay off rather than just idle.
 */
export function webSettleBudget(order: number): number {
  return Math.min(11000, 1600 + order * 1.6);
}

/** How long after the camera reset before the trailing refresh (6985). */
export const WEB_SETTLE_REFRESH_MS = 650;

/**
 * The circlepack pre-seed's options (6934).
 *
 * Genre and Community pack by the `genre` attribute the builders set on every
 * node, so FA2 refines structure instead of untangling noise. Discovery has no
 * grouping worth packing by, so it packs flat.
 */
export function webPreseedOptions(lens: WebLens): Record<string, unknown> {
  return lens === 'discovery' ? {} : { hierarchyAttributes: ['genre'] };
}

// ── Sigma's mount options ────────────────────────────────────────────────────

/**
 * The renderer settings (7220-7229).
 *
 * `hideEdgesOnMove` and `hideLabelsOnMove` are both perf AND clarity: labels
 * re-measure with measureText every frame, and thousands of edges during a pan
 * are noise. The consequence is that the last frame of any camera ANIMATION is
 * edge-less, which is why every animated move schedules a trailing refresh.
 */
export const WEB_SIGMA_SETTINGS = {
  renderLabels: true,
  labelRenderedSizeThreshold: 20,
  hideEdgesOnMove: true,
  hideLabelsOnMove: true,
  /** A sparser label grid → fewer simultaneous labels/measureText per frame. */
  labelGridCellSize: 150,
};

/** Camera timings (7512 / 7528 / 7534 / 7800). */
export const WEB_CAMERA = {
  focusRatio: 0.15,
  cameraToRatio: 0.12,
  focusMs: 500,
  zoomMs: 250,
  fitMs: 400,
  /** hideEdgesOnMove leaves the final frame edge-less; every move trails a refresh. */
  refreshAfterFocus: 600,
  refreshAfterZoom: 350,
  refreshAfterFit: 500,
};

/** The zoom buttons' ratios — note IN is a ratio BELOW 1 (index.html 4496-4497). */
export const WEB_ZOOM_IN = 0.7;
export const WEB_ZOOM_OUT = 1.4;

// ── Open / close lifecycle ───────────────────────────────────────────────────

/**
 * Whether a hub card's deep link should override the remembered lens (6700).
 *
 * Only the three known lenses count; anything else leaves the previous choice
 * alone, so a stray call cannot blank the view.
 */
export function webResolveLens(requested: unknown, current: WebLens | null): WebLens {
  if (requested === 'genre' || requested === 'community' || requested === 'discovery') {
    return requested;
  }
  return current || 'genre';
}

/**
 * Whether opening should snapshot the siblings' display values (6645).
 *
 * Only when NOT already open. A re-entrant open — the error card's Retry button
 * — would otherwise overwrite each sibling's real `_prevDisplay` ('') with
 * 'none', and closing would leave Discover blank.
 */
export function webShouldSnapshotSiblings(currentDisplay: string): boolean {
  return currentDisplay !== 'flex';
}

/**
 * Bump the generation and return it (6638).
 *
 * Every in-flight fetch captures this and bails on resolve if it changed, so a
 * response for a Web that has since been closed or reopened cannot mount a sigma
 * instance into a hidden host or clobber the user's current selection.
 */
export function webNextGen(): number {
  return ++artistWeb.gen;
}

/** Whether an in-flight result still belongs to the current view (6739). */
export function webResultIsCurrent(myGen: number, expectLens?: WebLens): boolean {
  if (artistWeb.gen !== myGen) return false;
  if (expectLens && artistWeb.lens !== expectLens) return false;
  return true;
}

/**
 * Whether a discovery payload is good enough to cache (6734).
 *
 * A 500 resolves `r.json()` too, and caching `{"error": …}` used to leave the
 * lens permanently blank with no way to retry. Only a well-formed payload is
 * kept.
 */
export function webDiscoveryPayloadOk(
  ok: boolean,
  d: { error?: string; nodes?: unknown },
): boolean {
  return ok && !d.error && Array.isArray(d.nodes);
}

/** State that must be cleared before a lens rebuilds (6836-6848). */
export function webResetForRender(): void {
  artistWeb.searchMatch = null;
  artistWeb.focusSet = null;
  artistWeb.focusRoot = null;
  artistWeb.selectedKey = null;
  artistWeb.selectedFocus = null;
  artistWeb.pathSource = null;
  artistWeb.pathTarget = null;
  artistWeb.pathResult = null;
  artistWeb.pathNodes = null;
  artistWeb.pathPairs = null;
  artistWeb.genreFilter = null;
  artistWeb.sizeBy = 'popularity'; // build sizes by popularity; the user re-picks per view
  artistWeb.index = [];
  // The spread stays dormant until the layout settles and captures home.
  artistWeb.home = null;
  artistWeb.spreadRoot = null;
  artistWeb.spreadSet = null;
  artistWeb.spreadActive = null;
}

/** State that must be cleared on close (7319-7351). */
export function webResetForClose(): void {
  artistWeb.gen++; // invalidate any in-flight fetch
  artistWeb.spreadRoot = null;
  artistWeb.spreadSet = null;
  artistWeb.spreadActive = null;
  artistWeb.graph = null; // a late re-select cannot refresh a dead graph
  artistWeb._hoverNode = null;
  artistWeb.pathMode = false;
  artistWeb.pathNodes = null;
  artistWeb.pathPairs = null;
  artistWeb.pathResult = null;
  artistWeb.pathSource = null;
  artistWeb.pathTarget = null;
}

/** Whether a lens switch needs the discovery fetch first (6760). */
export function webNeedsDiscoveryFetch(lens: WebLens): boolean {
  return lens === 'discovery' && !artistWeb.discoveryData;
}

/** A valid discovery payload with zero candidates should GUIDE, not blank (6878). */
export function webDiscoveryIsEmpty(lens: WebLens, order: number): boolean {
  return lens === 'discovery' && order === 0;
}

/** The sidebar's heading follows the lens (6860). */
export function webSidebarHeading(lens: WebLens): string {
  return lens === 'community' ? 'Communities' : 'Genres';
}

// ── Search ───────────────────────────────────────────────────────────────────

/** Below this the dropdown closes and the dimming clears (7465). */
export const WEB_SEARCH_MIN_CHARS = 2;
export const WEB_SEARCH_LIMIT = 8;

/**
 * Client-side search over the loaded artist index (7473).
 *
 * Instant, because the index was built during the lens render — no request per
 * keystroke, unlike the Artist Map's toolbar search, which queries the metadata
 * source for artists you may not own.
 */
export function webSearchHits(query: string): { key: string; label: string }[] {
  const q = (query || '').trim().toLowerCase();
  if (q.length < WEB_SEARCH_MIN_CHARS) return [];
  return artistWeb.index.filter((n) => n.label.toLowerCase().includes(q));
}

/** Whether a query is long enough to search at all (7465). */
export function webSearchActive(query: string): boolean {
  return (query || '').trim().toLowerCase().length >= WEB_SEARCH_MIN_CHARS;
}

/** Enter jumps to the first match in the set (7494). */
export function webSearchEnterTarget(): string | null {
  const m = artistWeb.searchMatch;
  if (!m || !m.size) return null;
  return m.values().next().value as string;
}

// ── Hover + selection ────────────────────────────────────────────────────────

/** A node plus its neighbours — the focus set for both hover and click (7628). */
export function webFocusSet(graph: WebGraph, node: string): Set<string> {
  const set = new Set([node]);
  graph.forEachNeighbor(node, (nb) => set.add(nb));
  return set;
}

/**
 * Apply a hover (7621-7638).
 *
 * Hovering OUT restores the click-selection's highlight rather than clearing —
 * so a selected artist stays lit while you look around it. In path mode the
 * tooltip still shows (it helps pick the two nodes) but nothing dims.
 */
export function webApplyHover(graph: WebGraph | null, node: string | null): void {
  const st = artistWeb;
  st._hoverNode = node;
  if (st.pathMode) return;
  if (node && graph) {
    st.focusSet = webFocusSet(graph, node);
    st.focusRoot = node;
  } else {
    st.focusSet = st.selectedFocus || null;
    st.focusRoot = st.selectedKey || null;
  }
}

/** Apply a click-selection (7640-7655); a click supersedes a search dim. */
export function webApplySelection(graph: WebGraph, node: string): Set<string> {
  const set = webFocusSet(graph, node);
  artistWeb.selectedKey = node;
  artistWeb.selectedFocus = set;
  artistWeb.focusSet = set;
  artistWeb.focusRoot = node;
  artistWeb.searchMatch = null;
  return set;
}

/** Which card a click opens, by node kind (7651-7654). */
export function webCardKind(kind: unknown): 'genre' | 'discovery' | 'artist' {
  if (kind === 'genre') return 'genre';
  if (kind === 'discovery') return 'discovery';
  return 'artist';
}

export function webClearSelection(): void {
  artistWeb.selectedKey = null;
  artistWeb.selectedFocus = null;
  artistWeb.focusSet = null;
  artistWeb.focusRoot = null;
}

// ── The hover tooltip ────────────────────────────────────────────────────────

export interface WebTooltip {
  label: string;
  badge: string;
  connections: number;
  connectionText: string;
  genre: string;
  imageUrl: string | null;
  /** True when an owned artist's thumb still needs resolving. */
  needsThumb: boolean;
  artistId: unknown;
}

/** How long a node must be hovered before its thumb is fetched (7611). */
export const WEB_TIP_THUMB_MS = 140;

/**
 * The tooltip model (7570-7612).
 *
 * The connection count is SIMILARITY links only — a genre hub has none, so its
 * degree IS its member count and it is reported that way, with the noun changed
 * to match.
 *
 * Images differ by kind: a discovery candidate ships a full URL, while an owned
 * artist carries a Plex-relative thumb that will not load as-is, so it is
 * resolved lazily and cached — only for artists you actually pause on, because
 * the endpoint writes to the DB per call and eager-resolving ~5k would take
 * minutes.
 */
export function webTooltip(
  graph: WebGraph,
  nodeKey: string,
  thumbCache: Record<string, string | null | undefined>,
): WebTooltip {
  const a = graph.getNodeAttributes(nodeKey);
  const kind = a.kind as string;
  let conn = 0;
  if (kind === 'genre') {
    conn = graph.degree(nodeKey);
  } else {
    graph.forEachEdge(nodeKey, (_e, attr) => {
      if (attr.kind === 'similarity') conn++;
    });
  }
  const noun = kind === 'genre' ? 'artist' : 'connection';
  const cached = a.artistId != null ? thumbCache[a.artistId as string] : undefined;
  const imgSrc = kind === 'discovery' ? ((a.image_url as string) ?? null) : cached || null;
  return {
    label: (a.label as string) || '',
    badge: kind === 'discovery' ? 'To discover' : kind === 'genre' ? 'Genre' : '',
    connections: conn,
    connectionText: conn ? `${conn} ${noun}${conn === 1 ? '' : 's'}` : '',
    genre: (a.primaryGenre as string) || '',
    imageUrl: imgSrc,
    needsThumb:
      !imgSrc &&
      kind !== 'genre' &&
      kind !== 'discovery' &&
      a.artistId != null &&
      thumbCache[a.artistId as string] === undefined,
    artistId: a.artistId,
  };
}

// ── Path mode ────────────────────────────────────────────────────────────────

export type WebPathStep =
  | { kind: 'reject-hub' }
  | { kind: 'start'; node: string; label: string }
  | { kind: 'same-node' }
  | { kind: 'no-path'; from: string; to: string }
  | { kind: 'complete'; path: string[] };

/**
 * One click in path mode (7713-7746).
 *
 * The first click (or any click after a completed path) restarts from that node.
 * Clicking the same node again is ignored rather than treated as a zero-length
 * path. Genre hubs are refused outright — a path through a hub would mean "both
 * are tagged Rock", which is not what the feature claims.
 */
export function webPathClick(
  graph: WebGraph,
  node: string,
  findPath: (a: string, b: string) => string[] | null,
): WebPathStep {
  const st = artistWeb;
  if (graph.getNodeAttribute(node, 'kind') === 'genre') return { kind: 'reject-hub' };
  const label = (graph.getNodeAttribute(node, 'label') as string) || node;

  if (!st.pathSource || st.pathResult) {
    st.pathSource = node;
    st.pathTarget = null;
    st.pathResult = null;
    st.pathNodes = new Set([node]);
    st.pathPairs = new Set();
    st.searchMatch = null;
    st.focusSet = null;
    st.focusRoot = null;
    return { kind: 'start', node, label };
  }
  if (node === st.pathSource) return { kind: 'same-node' };
  const path = findPath(st.pathSource, node);
  if (!path || path.length < 2) {
    return {
      kind: 'no-path',
      from: (graph.getNodeAttribute(st.pathSource, 'label') as string) || st.pathSource,
      to: label,
    };
  }
  st.pathTarget = node;
  st.pathResult = path;
  st.pathNodes = new Set(path);
  st.pathPairs = new Set();
  for (let i = 0; i + 1 < path.length; i++) {
    const x = path[i];
    const y = path[i + 1];
    st.pathPairs.add(x < y ? x + '|' + y : y + '|' + x);
  }
  return { kind: 'complete', path };
}

export function webClearPath(): void {
  artistWeb.pathSource = null;
  artistWeb.pathTarget = null;
  artistWeb.pathResult = null;
  artistWeb.pathNodes = null;
  artistWeb.pathPairs = null;
}

/** `Start: <b>Aphex Twin</b> — now click a second artist.` (7726). */
export function webPathStartHint(label: string): string {
  return `Start: <b>${label}</b> — now click a second artist.`;
}

/** `No similarity path between <b>A</b> and <b>B</b>.` (7732). */
export function webPathNoneHint(from: string, to: string): string {
  return `No similarity path between <b>${from}</b> and <b>${to}</b>.`;
}

// ── Preview + watchlist + expand ─────────────────────────────────────────────

/** The deezer id a preview needs, or null (7936). */
export function webPreviewId(ids: [string, string][] | undefined): string | null {
  const dz = (ids || []).find((p) => p && p[0] === 'deezer');
  return dz ? dz[1] : null;
}

/** Whether a candidate can be previewed at all (index.html-side gate, 8088). */
export function webCanPreview(ids: [string, string][] | undefined): boolean {
  return (ids || []).some((p) => p && p[0] === 'deezer');
}

/**
 * The id a watchlist add sends (8103).
 *
 * Spotify is preferred, then whatever came first. The SOURCE goes with it so the
 * endpoint never has to guess — a bare numeric Deezer or iTunes id used to be
 * mistaken for a library row id and could watch a completely different artist.
 */
export function webWatchlistPair(ids: [string, string][] | undefined): [string, string] | null {
  const pairs = ids || [];
  return pairs.find((p) => p && p[0] === 'spotify') || pairs[0] || null;
}

/** How many similar artists an expand asks for (8138). */
export const WEB_EXPAND_PER = 10;

/** The ring an expand places new nodes on, and how far out (8152-8155). */
export function webExpandRing(spreadPush: number): number {
  return (spreadPush || 0.1) * 2.2;
}

/** Where the nth of `total` new nodes lands around its parent (8154-8155). */
export function webExpandPosition(
  parent: { x: number; y: number },
  i: number,
  total: number,
  radius: number,
  jitter: number,
): { x: number; y: number } {
  const th = (2 * Math.PI * i) / total + jitter;
  return { x: parent.x + Math.cos(th) * radius, y: parent.y + Math.sin(th) * radius };
}

/** The live owned/discovery tally after an expand (8189-8191). */
export function webCountKinds(graph: WebGraph): { owned: number; discovery: number } {
  let owned = 0;
  let discovery = 0;
  graph.forEachNode((_k, a) => {
    if (a.kind === 'owned') owned++;
    else if (a.kind === 'discovery') discovery++;
  });
  return { owned, discovery };
}

/** `12 of your artists · 340 to discover` (8191). */
export function webDiscoveryStats(owned: number, discovery: number): string {
  return `${owned} of your artists · ${discovery} to discover`;
}

// ── The genre filter ─────────────────────────────────────────────────────────

/**
 * Toggle one genre in the filter (8237-8246).
 *
 * An empty set becomes null rather than staying an empty Set — the reducers test
 * `st.genreFilter` for truthiness, and an empty Set is truthy, which would dim
 * the entire graph.
 */
export function webToggleGenre(genre: string): void {
  const st = artistWeb;
  if (!st.genreFilter) st.genreFilter = new Set();
  if (st.genreFilter.has(genre)) st.genreFilter.delete(genre);
  else st.genreFilter.add(genre);
  if (st.genreFilter.size === 0) st.genreFilter = null;
}

/** The sidebar's rows: every counted genre, largest first, filtered by query (8222-8223). */
export function webGenreRows(
  counts: Record<string, number>,
  query: string,
): { genre: string; count: number; active: boolean }[] {
  const q = (query || '').trim().toLowerCase();
  return Object.keys(counts)
    .sort((a, b) => counts[b] - counts[a])
    .filter((g) => !q || g.toLowerCase().includes(q))
    .map((g) => ({
      genre: g,
      count: counts[g],
      active: !!(artistWeb.genreFilter && artistWeb.genreFilter.has(g)),
    }));
}

/** The edge button's label follows the declutter state (8270). */
export function webEdgeButtonLabel(declutter: boolean): string {
  return declutter ? 'Strong' : 'Edges';
}

// ── The host ─────────────────────────────────────────────────────────────────

/** Everything the controller needs from the page, so none of it is imported. */
export interface ArtWebHost {
  setStats(text: string): void;
  setHostMessage(html: string): void;
  refresh(): void;
  cameraTo(key: string, ratio: number, duration: number): void;
  refreshAfter(ms: number): void;
  showCard(kind: 'genre' | 'discovery' | 'artist', node: string): void;
  closePanel(): void;
  pathHint(html: string): void;
  hidePathHint(): void;
  showPathPanel(path: string[]): void;
}

export type { ArtistWebState, WebPayload, WebSizeBy };
