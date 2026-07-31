/**
 * Artist Map — the circle-packed staged canvas visualisation.
 *
 * This module holds the map's mutable singleton plus every part of it that is
 * decidable from arguments and state alone: the genre-island layout engine, the
 * camera math, hit testing, and the animation stepper. The canvas painters, the
 * image pipeline and the DOM chrome live in sibling modules; they all read this
 * same `artMap` object, exactly as the vanilla's `_artMap` was shared.
 *
 * Transcribed from `webui/static/discover.js` 5829-6073 and the geometry parts
 * of 8273-10284 — read end to end before any of this was written.
 *
 * The singleton is deliberate, not a shortcut. A canvas renderer owns imperative
 * state by nature; modelling `placed`/`zoom`/`offsetX` as React state would
 * re-render on every mousemove and would be a rewrite, not a port.
 */

export type ArtMapNodeId = number | string;

/** A node as `_artMapLayoutIslands` writes it into `placed` (6018-6039). */
export interface ArtMapNode {
  id: ArtMapNodeId;
  name: string;
  x: number;
  y: number;
  radius: number;
  opacity: number;
  type: string;
  image_url: string;
  _origId?: ArtMapNodeId;
  genres?: string[];
  spotify_id?: string;
  itunes_id?: string;
  deezer_id?: string;
  discogs_id?: string;
  musicbrainz_id?: string;
  popularity?: number;
  _hue?: number;
  _island?: string;
  _isLabel?: boolean;
  _count?: number;
  _bobPhase?: number;
  _bobAmp?: number;
  /** Ring depth from the explorer payload — 1 = directly similar to the centre. */
  ring?: number;
  /** Reveal/bloom animation slots, assigned by the animation engine. */
  aScale?: number | null;
  aAlpha?: number | null;
  _revealAt?: number;
  _revealDur?: number;
  _riseAmp?: number;
  _revealRise?: number;
}

/** A raw node as the three map endpoints deliver it, before layout. */
export interface ArtMapRawNode {
  id?: ArtMapNodeId;
  name?: string;
  type?: string;
  genres?: string[];
  image_url?: string;
  popularity?: number;
  spotify_id?: string;
  itunes_id?: string;
  deezer_id?: string;
  discogs_id?: string;
  musicbrainz_id?: string;
  ring?: number;
  _focal?: boolean;
}

export interface ArtMapGroup {
  name: string;
  nodes: ArtMapRawNode[];
  count?: number;
}

export interface ArtMapIsland {
  name: string;
  cx: number;
  cy: number;
  r: number;
  hue: number;
  count: number;
  /** Assigned by `artMapBeginReveal` so each island blooms in turn (8893). */
  _order?: number;
}

export interface ArtMapEdge {
  source: ArtMapNodeId;
  target: ArtMapNodeId;
  weight?: number;
}

export interface ArtMapRipple {
  cx: number;
  cy: number;
  hue: number;
  maxR: number;
  t0: number;
  dur: number;
  /** Set only on click ripples — the radial shove strength (8971). */
  push?: number;
  width?: number;
}

export interface ArtMapState {
  placed: ArtMapNode[];
  edges: ArtMapEdge[];
  images: Record<string, CanvasImageSource>;
  canvas: HTMLCanvasElement | null;
  ctx: CanvasRenderingContext2D | null;
  offscreen: HTMLCanvasElement | null;
  offCtx: CanvasRenderingContext2D | null;
  width: number;
  height: number;
  offsetX: number;
  offsetY: number;
  zoom: number;
  hoveredNode: ArtMapNode | null;
  animFrame: number | null;
  dirty: boolean;
  WATCHLIST_R: number;
  BUFFER: number;
  MAX_BUFFER_PX: number;
  LIVE_PX: number;
  _anim: { running: boolean; raf: number | null; last: number; _lastDraw?: number };
  _fieldAlpha: number;
  _revealT0: number;
  _panelW: number;
  _islands?: ArtMapIsland[];
  _nodeById?: Record<string, ArtMapNode> | null;
  _ripples?: ArtMapRipple[] | null;
  _watchSet?: Set<string>;
  _watchChecked?: Set<string>;
  _focusIdx?: number;
  _oneIsland?: boolean;
  _mapTitle?: string;
  _hideSimilar?: boolean;
  _revealing?: boolean;
  _ambient?: boolean;
  _liveOverflow?: boolean;
  _liveBuildZoom?: number;
  _liveCount?: number;
  _bufferScale?: number | null;
  _bufferMinX?: number;
  _bufferMinY?: number;
  _drawAlphaMul?: number | null;
  _now?: number;
  _panelArtistId?: ArtMapNodeId | null;
  _panelOpen?: boolean;
  _gloss?: HTMLCanvasElement;
  _halos?: Record<string, HTMLCanvasElement>;
  [key: string]: unknown;
}

/**
 * The map singleton (5830-5864). Every field below is transcribed at its vanilla
 * default; the tuning comments are kept because the values are not arbitrary.
 */
export const artMap: ArtMapState = {
  placed: [],
  edges: [],
  images: {},
  canvas: null,
  ctx: null,
  offscreen: null,
  offCtx: null, // offscreen buffer for fast pan/zoom
  width: 0,
  height: 0,
  offsetX: 0,
  offsetY: 0,
  zoom: 0.15,
  hoveredNode: null,
  animFrame: null,
  dirty: true, // true = need to rebuild offscreen buffer
  WATCHLIST_R: 320,
  BUFFER: 8,
  // Max offscreen-buffer dimension (px). The buffer renders the whole world, so
  // on big/dense maps (e.g. 2000-node genre map) this was 10240 → a 76 MP canvas
  // that took ~1s to rebuild and ~150ms to blit per frame (3 fps). Capping it far
  // lower makes rebuild + blit cheap (and pushes more nodes under the LOD dot
  // threshold). Only binds on large worlds — small maps stay crisp via the
  // z*2 / 1.0 caps.
  MAX_BUFFER_PX: 4096,
  // A node is "live" (redrawn every frame so it can scale/bob/ripple) when its
  // on-screen radius clears this; everything smaller stays baked in the buffer.
  LIVE_PX: 12,
  _anim: { running: false, raf: null, last: 0 },
  _fieldAlpha: 1, // global fade for the static far-field buffer (reveal)
  _revealT0: 0, // performance.now() when the current reveal began
  _panelW: 320, // right-side info panel width (reserved when framing islands)
};

// ── Genre-island layout (shared by watchlist / genre / explore) ───────────────

/**
 * Deterministic hue (0-360) from a genre name, so each island has a stable tint
 * (5919-5924).
 *
 * The rolling `% 360` inside the loop is not the same as taking it once at the
 * end — it keeps the accumulator small, and different inputs collide differently
 * than a single final modulus would. Transcribed exactly.
 */
export function artMapGenreHue(name: string | null | undefined): number {
  let h = 0;
  const s = (name || '').toLowerCase();
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
  return h;
}

export interface ArtMapPlacement {
  node: ArtMapRawNode;
  dx: number;
  dy: number;
}

/**
 * Pack members into a FILLED disc, centre outward (5928-5949).
 *
 * Ring 0 is the single most-popular member at the origin; each subsequent ring
 * holds as many as fit its circumference and is rotated by the golden angle so
 * the rings never line up into visible spokes.
 *
 * The returned `islandR` is the ring distance AFTER the final increment, so it
 * already includes one ring of padding. With exactly one member the loop never
 * runs and it is `nodeR * 2 + gap`; with none it is `nodeR`.
 */
export function artMapPackDisc(
  members: ArtMapRawNode[],
  nodeR: number,
  gap: number,
): { placements: ArtMapPlacement[]; islandR: number } {
  const placements: ArtMapPlacement[] = [];
  if (!members.length) return { placements, islandR: nodeR };
  placements.push({ node: members[0], dx: 0, dy: 0 });
  let idx = 1;
  let ring = 1;
  let ringDist = nodeR * 2 + gap;
  const step = nodeR * 2 + gap;
  while (idx < members.length) {
    const circ = 2 * Math.PI * ringDist;
    const cap = Math.max(1, Math.floor(circ / step));
    const cnt = Math.min(cap, members.length - idx);
    const aStep = (2 * Math.PI) / cnt;
    const off = ring * 2.399963; // golden offset per ring → no spokes
    for (let i = 0; i < cnt; i++) {
      const a = off + i * aStep;
      placements.push({
        node: members[idx + i],
        dx: Math.cos(a) * ringDist,
        dy: Math.sin(a) * ringDist,
      });
    }
    idx += cnt;
    ringDist += step;
    ring++;
  }
  return { placements, islandR: ringDist };
}

/**
 * Group flat nodes by PRIMARY genre into `{name, count, nodes[]}`, largest first
 * (5954-5972).
 *
 * Only `genres[0]` counts — an artist tagged both "rock" and "jazz" lands on the
 * rock island only. Nodes with no genre fall into "Other", and the name is
 * title-cased so "hip hop" and "Hip Hop" share an island.
 *
 * Once there are more genres than `maxIslands`, the long tail folds into a
 * SECOND "Other" group appended at the end — which is why an existing "Other"
 * that survived into the head can coexist with it. Transcribed as-is.
 */
export function artMapGroupByGenre(
  nodes: ArtMapRawNode[],
  maxIslands = 14,
): { name: string; nodes: ArtMapRawNode[]; count: number }[] {
  const byGenre: Record<string, ArtMapRawNode[]> = {};
  for (const n of nodes) {
    const g = n.genres && n.genres.length ? String(n.genres[0]) : 'Other';
    const key = g.replace(/\b\w/g, (c) => c.toUpperCase());
    (byGenre[key] = byGenre[key] || []).push(n);
  }
  let groups = Object.keys(byGenre).map((name) => ({
    name,
    nodes: byGenre[name],
    count: byGenre[name].length,
  }));
  groups.sort((a, b) => b.count - a.count);
  if (groups.length > maxIslands) {
    // Fold the long tail of tiny genres into one "Other" island.
    const head = groups.slice(0, maxIslands - 1);
    const tail = groups.slice(maxIslands - 1);
    const tailNodes = tail.flatMap((g) => g.nodes);
    head.push({ name: 'Other', nodes: tailNodes, count: tailNodes.length });
    groups = head;
  }
  return groups;
}

export interface ArtMapLayoutOpts {
  nodeR?: number;
  gap?: number;
  maxPerIsland?: number;
}

/**
 * Lay out islands → fills `artMap.placed`, `artMap._islands` and
 * `artMap._nodeById` (5976-6046).
 *
 * Islands are seeded on a golden spiral then pushed apart for up to 160 passes.
 * The push loop breaks as soon as a pass moves nothing, so a small map costs one
 * pass; only a crowded one pays for all 160.
 *
 * `count` comes from the GROUP, not from the placed members — a genre with 900
 * artists caps at `maxPerIsland` bubbles but still reads "900 artists" on its
 * label, which is the honest number.
 */
export function artMapLayoutIslands(groups: ArtMapGroup[], opts: ArtMapLayoutOpts = {}): void {
  artMap.placed = [];
  artMap._islands = [];
  const nodeR = opts.nodeR || artMap.WATCHLIST_R * 0.22;
  const gap = opts.gap || artMap.BUFFER * 2.2;
  const cap = opts.maxPerIsland || 300;
  let pid = 0;

  const islands = groups.map((g) => {
    const members = g.nodes
      .slice()
      .sort(
        (a, b) =>
          (b._focal ? 1 : 0) - (a._focal ? 1 : 0) || (b.popularity || 0) - (a.popularity || 0),
      )
      .slice(0, cap);
    const { placements, islandR } = artMapPackDisc(members, nodeR, gap);
    return {
      name: g.name,
      count: g.count != null ? g.count : members.length,
      placements,
      islandR,
      hue: artMapGenreHue(g.name),
      cx: 0,
      cy: 0,
    };
  });

  // Golden-spiral seed placement
  islands.forEach((isl, i) => {
    if (i === 0) {
      isl.cx = 0;
      isl.cy = 0;
    } else {
      const a = i * 2.399963;
      const r = isl.islandR * Math.sqrt(i) * 1.05;
      isl.cx = Math.cos(a) * r;
      isl.cy = Math.sin(a) * r;
    }
  });
  // Push apart — generous water between islands
  for (let pass = 0; pass < 160; pass++) {
    let moved = false;
    for (let i = 0; i < islands.length; i++) {
      for (let j = i + 1; j < islands.length; j++) {
        const dx = islands[j].cx - islands[i].cx;
        const dy = islands[j].cy - islands[i].cy;
        const dist = Math.hypot(dx, dy) || 1;
        const minD = islands[i].islandR + islands[j].islandR + nodeR * 3.5;
        if (dist < minD) {
          const push = (minD - dist) / 2 + 1;
          islands[i].cx -= (dx / dist) * push;
          islands[i].cy -= (dy / dist) * push;
          islands[j].cx += (dx / dist) * push;
          islands[j].cy += (dy / dist) * push;
          moved = true;
        }
      }
    }
    if (!moved) break;
  }

  for (const isl of islands) {
    // Floating genre title above the island
    artMap.placed.push({
      id: `label_${isl.name}`,
      name: isl.name,
      x: isl.cx,
      y: isl.cy - isl.islandR - nodeR * 1.4,
      radius: Math.max(nodeR * 1.3, isl.islandR * 0.16),
      opacity: 1,
      type: 'genre_label',
      _isLabel: true,
      _count: isl.count,
      _hue: isl.hue,
      image_url: '',
    });
    for (const p of isl.placements) {
      const n = p.node;
      artMap.placed.push({
        id: pid++,
        _origId: n.id,
        name: n.name as string,
        x: isl.cx + p.dx,
        y: isl.cy + p.dy,
        radius: nodeR * (n._focal ? 1.45 : 1),
        opacity: 1,
        type: n.type || 'similar',
        image_url: n.image_url || '',
        genres: n.genres || [],
        spotify_id: n.spotify_id || '',
        itunes_id: n.itunes_id || '',
        deezer_id: n.deezer_id || '',
        discogs_id: n.discogs_id || '',
        musicbrainz_id: n.musicbrainz_id || '',
        popularity: n.popularity || 0,
        _hue: isl.hue,
        _island: isl.name,
        // Ambient buoyancy — phase varies by position so bubbles bob in a gentle
        // wave (not in unison); amplitude in world units.
        _bobPhase: (isl.cx + p.dx + isl.cy + p.dy) * 0.0022,
        _bobAmp: nodeR * 0.12,
      });
    }
    artMap._islands.push({
      name: isl.name,
      cx: isl.cx,
      cy: isl.cy,
      r: isl.islandR,
      hue: isl.hue,
      count: isl.count,
    });
  }

  artMap._nodeById = {};
  artMap.placed.forEach((n) => {
    (artMap._nodeById as Record<string, ArtMapNode>)[n.id as string] = n;
  });
}

/**
 * Remap edges that used original node ids to the new placed-node ids (6049-6058).
 *
 * The first placed node wins each original id, so when the same artist appears
 * on two islands the edge follows the first copy. Self-edges and edges whose
 * endpoint never got placed (capped out of a big island) are dropped.
 */
export function artMapRemapEdges(edges: ArtMapEdge[] | null | undefined): ArtMapEdge[] {
  const map: Record<string, ArtMapNodeId> = {};
  for (const n of artMap.placed) {
    if (n._origId != null && map[n._origId as string] == null) map[n._origId as string] = n.id;
  }
  const out: ArtMapEdge[] = [];
  for (const e of edges || []) {
    const s = map[e.source as string];
    const t = map[e.target as string];
    if (s != null && t != null && s !== t)
      out.push({ source: s, target: t, weight: e.weight || 1 });
  }
  return out;
}

// ── The info panel's share of the viewport ───────────────────────────────────

/** 760px is the map's mobile breakpoint (6224-6226). */
export function artMapIsMobile(): boolean {
  return (window.innerWidth || document.documentElement.clientWidth || 9999) <= 760;
}

/**
 * Horizontal space the panel reserves when framing islands (6230-6232) — none on
 * mobile, where the bottom sheet overlays instead of sitting beside the map.
 */
export function artMapReservedW(): number {
  return artMapIsMobile() ? 0 : artMap._panelW;
}

/**
 * Auto-zoom/pan so all placed nodes fit the viewport with a margin (6061-6073).
 *
 * Note this measures EVERY placed node, visible or not — unlike `artMapFitToView`
 * (8377), the toolbar button, which skips faded-out ones. That difference is why
 * fitting from the toolbar in one-island mode frames the focused island while a
 * resize-triggered fit frames the whole world.
 */
export function artMapFitToContent(marginPx = 120): void {
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const n of artMap.placed) {
    minX = Math.min(minX, n.x - n.radius);
    maxX = Math.max(maxX, n.x + n.radius);
    minY = Math.min(minY, n.y - n.radius);
    maxY = Math.max(maxY, n.y + n.radius);
  }
  if (!isFinite(minX)) return;
  const usableW = Math.max(200, artMap.width - artMapReservedW());
  const mapW = maxX - minX + marginPx * 2;
  const mapH = maxY - minY + marginPx * 2;
  artMap.zoom = Math.min(usableW / mapW, artMap.height / mapH, 1);
  artMap.offsetX = usableW / 2 - ((minX + maxX) / 2) * artMap.zoom;
  artMap.offsetY = artMap.height / 2 - ((minY + maxY) / 2) * artMap.zoom;
}

// ── Camera targets ───────────────────────────────────────────────────────────

export interface ArtMapCameraTarget {
  zoom: number;
  offsetX: number;
  offsetY: number;
}

/**
 * Where the toolbar's +/- buttons put the camera (8368-8375).
 *
 * Zooms about the CANVAS CENTRE (not the cursor, which is the wheel handler's
 * job), and clamps to 0.02..3 — note the wheel allows up to 5.
 */
export function artMapZoomTarget(factor: number): ArtMapCameraTarget {
  const cx = artMap.width / 2;
  const cy = artMap.height / 2;
  const targetZoom = Math.max(0.02, Math.min(3, artMap.zoom * factor));
  return {
    zoom: targetZoom,
    offsetX: cx - (cx - artMap.offsetX) * (targetZoom / artMap.zoom),
    offsetY: cy - (cy - artMap.offsetY) * (targetZoom / artMap.zoom),
  };
}

/**
 * Where "fit to view" puts the camera (8377-8393), or null when there is nothing
 * placed.
 *
 * Skips nodes faded below 0.01, so in one-island mode this frames the focused
 * island. It uses a flat 100px margin and the FULL width — it does not reserve
 * the panel's 320px the way `artMapFitToContent` does.
 */
export function artMapFitToViewTarget(): ArtMapCameraTarget | null {
  if (!artMap.placed.length) return null;
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  artMap.placed.forEach((n) => {
    if ((n.opacity || 0) < 0.01) return;
    minX = Math.min(minX, n.x - n.radius);
    maxX = Math.max(maxX, n.x + n.radius);
    minY = Math.min(minY, n.y - n.radius);
    maxY = Math.max(maxY, n.y + n.radius);
  });
  const mapW = maxX - minX + 100;
  const mapH = maxY - minY + 100;
  const targetZoom = Math.min(artMap.width / mapW, artMap.height / mapH, 1);
  return {
    zoom: targetZoom,
    offsetX: artMap.width / 2 - ((minX + maxX) / 2) * targetZoom,
    offsetY: artMap.height / 2 - ((minY + maxY) / 2) * targetZoom,
  };
}

/**
 * Where focusing one island puts the camera (6096-6102).
 *
 * The island is framed in the space LEFT of the info panel, and unlike the two
 * fits above this one may zoom IN past 1 (cap 1.2) so a small island still fills
 * the view.
 */
export function artMapIslandCamera(isl: ArtMapIsland): ArtMapCameraTarget {
  const usableW = Math.max(200, artMap.width - artMapReservedW());
  const span = isl.r * 2.3 + 120;
  const z = Math.min(usableW / span, artMap.height / span, 1.2);
  return {
    zoom: z,
    offsetX: usableW / 2 - isl.cx * z,
    offsetY: artMap.height / 2 - isl.cy * z,
  };
}

// ── Pointer → world, and what is under it ────────────────────────────────────

/** Inverse of `translate(offsetX, offsetY) → scale(zoom)` (10256-10265). */
export function artMapScreenToWorld(
  e: { clientX: number; clientY: number },
  canvas: { getBoundingClientRect(): { left: number; top: number } },
): { nx: number; ny: number } {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  return {
    nx: (sx - artMap.offsetX) / artMap.zoom,
    ny: (sy - artMap.offsetY) / artMap.zoom,
  };
}

/**
 * What bubble is at a world point (10267-10283).
 *
 * A single O(N) pass, no per-move sort and no allocation. Watchlist nodes draw on
 * top so they win ties; otherwise the FIRST node whose circle contains the point
 * wins. Nodes faded below 0.3 — a much higher bar than the 0.01 the renderer
 * uses — are unclickable, so a mid-bloom bubble can be visible but not yet hit.
 *
 * (A spatial grid was tried and reverted upstream: it exploded building cells for
 * large-radius genre cluster nodes, and a flat scan of thousands is sub-ms.)
 */
export function artMapHitTest(wx: number, wy: number): ArtMapNode | null {
  let similarHit: ArtMapNode | null = null;
  for (const n of artMap.placed) {
    if ((n.opacity || 0) < 0.3) continue;
    const dx = wx - n.x;
    const dy = wy - n.y;
    if (dx * dx + dy * dy <= n.radius * n.radius) {
      if (n.type === 'watchlist') return n;
      if (!similarHit) similarHit = n;
    }
  }
  return similarHit;
}

// ── Identity + watchlist state for one node ──────────────────────────────────

/**
 * The best source id for a node, in a FIXED priority order (6234-6238).
 *
 * Unlike the context menu (10058) and the info modal (10289), this one ignores
 * the user's active metadata source entirely — spotify always wins. The three
 * pickers genuinely disagree; transcribed as-is rather than unified.
 */
export function artMapNodeBest(n: ArtMapNode | null | undefined): { id: string; source: string } {
  const map: [keyof ArtMapNode, string][] = [
    ['spotify_id', 'spotify'],
    ['itunes_id', 'itunes'],
    ['deezer_id', 'deezer'],
    ['discogs_id', 'discogs'],
    ['musicbrainz_id', 'musicbrainz'],
  ];
  for (const [k, s] of map) {
    if (n && n[k]) return { id: n[k] as string, source: s };
  }
  return { id: '', source: '' };
}

/** How many map edges touch this node, in either direction (6240-6244). */
export function artMapConnCount(n: ArtMapNode): number {
  let c = 0;
  for (const e of artMap.edges || []) {
    if (e.source === n.id || e.target === n.id) c++;
  }
  return c;
}

/**
 * Is this node on the watchlist (6247-6252)?
 *
 * `type === 'watchlist'` is authoritative and short-circuits, so a node the map
 * was built with stays watched even before the lazy server check answers. Only
 * then does the optimistic `_watchSet` decide.
 */
export function artMapIsWatched(n: ArtMapNode | null | undefined): boolean {
  if (!n) return false;
  if (n.type === 'watchlist') return true;
  const best = artMapNodeBest(n);
  return !!(best.id && artMap._watchSet && artMap._watchSet.has(best.id));
}

// ── Live layer vs static buffer ──────────────────────────────────────────────

/**
 * Does this node render on the live overlay rather than the baked buffer
 * (8683-8693)?
 *
 * Uses the zoom the BUFFER WAS BUILT AT, not the live zoom. The buffer only
 * rebuilds ~300ms after zooming stops, so the split must stay frozen to whatever
 * the (possibly stale) buffer excluded — otherwise a bubble falls out of both
 * during an active zoom and flickers. Both the buffer-exclude and the live-draw
 * read this same function, so the two sets are always exact complements.
 *
 * `_liveOverflow` short-circuits it to false: when more bubbles would be live
 * than the live layer can draw, the buffer bakes everything instead.
 */
export function artMapIsLiveSize(n: ArtMapNode): boolean {
  if (n._isLabel) return false;
  if (artMap._liveOverflow) return false;
  const z = artMap._liveBuildZoom || artMap.zoom;
  return (n.radius || 0) * z >= artMap.LIVE_PX;
}

/** More than this many live bubbles and the buffer takes the whole crowd (8495). */
export const ARTMAP_LIVE_OVERFLOW_LIMIT = 140;

// ── The animation stepper ────────────────────────────────────────────────────

/**
 * Advance every active animation to absolute time `t` (ms); returns true while
 * anything is still moving (8837-8877).
 *
 * Each bubble scales+fades in past its staggered start with a gentle ease-out-back
 * overshoot, alpha runs 1.6x ahead of scale, and the remaining rise decays as
 * (1-p)^3 so it surfaces through water rather than popping.
 *
 * The final `if (!active && _revealing)` returns TRUE after clearing the flag —
 * one more frame is owed, to bake the settled map into the static buffer.
 */
export function artMapStepAnimations(t: number): boolean {
  let active = false;

  const placed = artMap.placed;
  if (placed) {
    for (const n of placed) {
      if (n.aScale == null || n.aScale >= 1) continue;
      if (t < (n._revealAt as number)) {
        active = true;
        continue;
      }
      const p = Math.min(1, (t - (n._revealAt as number)) / (n._revealDur || 480));
      if (p >= 1) {
        n.aScale = 1;
        n.aAlpha = 1;
        n._revealRise = 0;
      } else {
        const c1 = 1.18;
        const c3 = c1 + 1;
        const back = 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2);
        n.aScale = back;
        n.aAlpha = Math.min(1, p * 1.6);
        n._revealRise = Math.pow(1 - p, 3) * (n._riseAmp || 0);
        active = true;
      }
    }
  }

  const rip = artMap._ripples;
  if (rip && rip.length) {
    let anyAlive = false;
    for (const r of rip) if (t < r.t0 + r.dur) anyAlive = true;
    if (anyAlive) active = true;
    else artMap._ripples = [];
  }

  // When the bloom finishes, leave reveal mode and bake everything into the
  // static buffer (one rebuild) so steady-state goes back to the cheap path.
  if (!active && artMap._revealing) {
    artMap._revealing = false;
    artMap.dirty = true;
    return true; // one more frame to do the rebuild + final blit
  }
  return active;
}

/**
 * Total world-space displacement on a node from all active "push" ripples
 * (8942-8961), or null when nothing is pushing.
 *
 * Only ripples carrying `push` (click/tap feedback) shove anything — the reveal
 * bloom's rings are visual only. The envelope is a gaussian bump riding the
 * expanding wavefront, decaying over the ripple's life, and displacements below
 * 0.05 are dropped so a distant ripple costs nothing.
 */
export function artMapNodeDisplacement(n: ArtMapNode): { dx: number; dy: number } | null {
  const rip = artMap._ripples;
  if (!rip || !rip.length) return null;
  const t = artMap._now || performance.now();
  let dx = 0;
  let dy = 0;
  for (const r of rip) {
    if (!r.push) continue;
    const p = (t - r.t0) / r.dur;
    if (p < 0 || p > 1) continue;
    const front = r.maxR * (0.08 + 0.92 * (1 - Math.pow(1 - p, 2)));
    const ddx = n.x - r.cx;
    const ddy = n.y - r.cy;
    const d = Math.hypot(ddx, ddy) || 1;
    const delta = d - front;
    const width = r.width || r.maxR * 0.2;
    const env = Math.exp(-(delta * delta) / (2 * width * width)); // bump at the wavefront
    const push = r.push * env * (1 - p); // decays over the ripple's life
    if (push > 0.05) {
      dx += (ddx / d) * push;
      dy += (ddy / d) * push;
    }
  }
  return dx || dy ? { dx, dy } : null;
}

// ── Image sizing ─────────────────────────────────────────────────────────────

/**
 * Clamp a requested avatar size to 112..384px (9861-9863).
 *
 * Artist images arrive up to 1000x1000. Nodes draw tiny, so holding full-res
 * bitmaps is ruinous: ~1500 nodes x 1000² x 4 bytes ≈ 6 GB of decoded image
 * memory. Decoding to a small avatar keeps the whole map's images in the low
 * hundreds of MB.
 */
export function artMapImgPx(px: number | null | undefined): number {
  return Math.min(384, Math.max(112, Math.round(px || 144)));
}

/**
 * Target avatar px for a node from its world radius (9919-9922).
 *
 * Focal nodes get a floor of 256 before the clamp — so a watchlist bubble is
 * crisp when you zoom into it — while the swarm scales at 1.6x its radius and
 * mostly lands on the 112 floor.
 */
export function artMapNodeImgPx(n: ArtMapNode): number {
  const isFocal = n.type === 'watchlist' || n.type === 'center' || n.ring === 1;
  return artMapImgPx(isFocal ? Math.max(256, (n.radius || 0) * 1.4) : (n.radius || 0) * 1.6);
}
