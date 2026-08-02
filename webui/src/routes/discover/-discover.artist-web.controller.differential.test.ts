import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';

import { loadVanilla } from '../../test/vanilla-extract';
import { type ArtistWebState, type WebGraph, artistWeb } from './-discover.artist-web';
import {
  WEB_CAMERA,
  WEB_DISCOVERY_URL,
  WEB_EXPAND_PER,
  WEB_LIBRARY_URL,
  WEB_NO_CANDIDATES,
  WEB_SEARCH_MIN_CHARS,
  WEB_SIGMA_SETTINGS,
  WEB_SYNC_ITERATIONS,
  WEB_ZOOM_IN,
  WEB_ZOOM_OUT,
  webApplyHover,
  webApplySelection,
  webCanPreview,
  webCardKind,
  webClearPath,
  webClearSelection,
  webCountKinds,
  webDiscoveryIsEmpty,
  webDiscoveryPayloadOk,
  webEdgeButtonLabel,
  webExpandPosition,
  webExpandRing,
  webFocusSet,
  webGenreRows,
  webLayoutSettings,
  webLibsReady,
  webNeedsDiscoveryFetch,
  webNextGen,
  webPathClick,
  webPathNoneHint,
  webPathStartHint,
  webPreseedOptions,
  webPreviewId,
  webResetForClose,
  webResetForRender,
  webResolveGraph,
  webResolveLens,
  webResultIsCurrent,
  webSearchActive,
  webSearchEnterTarget,
  webSearchHits,
  webSettleBudget,
  webShouldSnapshotSiblings,
  webSidebarHeading,
  webToggleGenre,
  webTooltip,
  webWatchlistPair,
} from './-discover.artist-web.controller';
import {
  WEB_DISCOVERY_GENRES,
  WEB_GENRE_MEMBERS,
  WEB_LEGEND_LIMIT,
  WEB_PREVIEW_IDLE,
  WEB_SHORTCUTS,
  webArtistCard,
  webDiscoveryCard,
  webGenreCard,
  webLegendItems,
  webPathRows,
  webPreviewPlayingLabel,
} from './-discover.artist-web.panel';

/**
 * The Artist Web's controller and panel.
 *
 * The card builders and the tooltip are compared by letting the REAL vanilla
 * paint into a real DOM and reading its output back — the same technique the
 * Artist Map's chrome uses. Everything else pins the decision plus the constant
 * it depends on against the text still in discover.js.
 */
const SOURCE = readFileSync(resolve(process.cwd(), 'static/discover.js'), 'utf8');
/** The toolbar buttons are markup, so their ratios are pinned against the shell. */
const OVERLAY = readFileSync(
  resolve(process.cwd(), 'src/routes/discover/-ui/artist-web-overlay.tsx'),
  'utf8',
);

// ── A minimal graphology ─────────────────────────────────────────────────────

type Attrs = Record<string, unknown>;

class FakeGraph implements WebGraph {
  nodes = new Map<string, Attrs>();
  edges = new Map<string, { source: string; target: string; attrs: Attrs }>();
  get order() {
    return this.nodes.size;
  }
  get size() {
    return this.edges.size;
  }
  addNode(key: string, attrs: Attrs) {
    this.nodes.set(key, { ...attrs });
  }
  addEdge(source: string, target: string, attrs: Attrs) {
    this.edges.set(`${source}|${target}`, { source, target, attrs: { ...attrs } });
  }
  hasNode(key: string) {
    return this.nodes.has(key);
  }
  hasEdge(s: string, t: string) {
    return this.edges.has(`${s}|${t}`) || this.edges.has(`${t}|${s}`);
  }
  degree(key: string) {
    let d = 0;
    this.edges.forEach((e) => {
      if (e.source === key) d++;
      if (e.target === key) d++;
    });
    return d;
  }
  source(edge: string) {
    return this.edges.get(edge)!.source;
  }
  target(edge: string) {
    return this.edges.get(edge)!.target;
  }
  getNodeAttribute(key: string, name: string) {
    return this.nodes.get(key)?.[name];
  }
  getNodeAttributes(key: string) {
    return this.nodes.get(key) as Attrs;
  }
  setNodeAttribute(key: string, name: string, v: unknown) {
    (this.nodes.get(key) as Attrs)[name] = v;
  }
  mergeNodeAttributes(key: string, a: Attrs) {
    Object.assign(this.nodes.get(key) as Attrs, a);
  }
  mergeEdgeAttributes(e: string, a: Attrs) {
    Object.assign(this.edges.get(e)!.attrs, a);
  }
  forEachNode(cb: (k: string, a: Attrs) => void) {
    [...this.nodes.entries()].forEach(([k, a]) => cb(k, a));
  }
  forEachEdge(
    a: string | ((e: string, at: Attrs, s: string) => void),
    b?: (e: string, at: Attrs, s: string) => void,
  ) {
    const node = typeof a === 'string' ? a : null;
    const cb = (typeof a === 'string' ? b : a) as (e: string, at: Attrs, s: string) => void;
    [...this.edges.entries()].forEach(([k, e]) => {
      if (node && e.source !== node && e.target !== node) return;
      cb(k, e.attrs, e.source);
    });
  }
  forEachNeighbor(key: string, cb: (nb: string, a: Attrs) => void) {
    const seen = new Set<string>();
    this.edges.forEach((e) => {
      const other = e.source === key ? e.target : e.target === key ? e.source : null;
      if (other && !seen.has(other)) {
        seen.add(other);
        cb(other, this.nodes.get(other) as Attrs);
      }
    });
  }
}

// ── The vanilla side ─────────────────────────────────────────────────────────

const PREAMBLE = `
let _artistWeb = {
  sigma: { refresh() {}, getCamera() { return { animate() {}, animatedReset() {}, ratio: 1 }; },
           getNodeDisplayData() { return { x: 0, y: 0 }; } },
  graph: null, onKey: null, gen: 0, lens: 'genre',
  data: null, discoveryData: null, genreColor: null, index: [],
  searchMatch: null, focusSet: null, focusRoot: null,
  selectedKey: null, selectedFocus: null,
  genreFilter: null, genreCounts: null,
  sizeBy: 'popularity', betweenCache: null,
  edgeDeclutter: false, edgeThreshold: 2,
  pathMode: false, pathSource: null, pathTarget: null,
  pathNodes: null, pathPairs: null, pathResult: null, simGraph: null,
  cursorFX: true, fxRAF: null, home: null,
  spreadRoot: null, spreadSet: null, spreadPush: 0, spreadActive: null,
  fa2: null, fa2Timer: null, previewAudio: null, previewKey: null,
  _hoverNode: null, _mouse: null, _mouseBound: false,
};
const WEB_OWNED_COLOR = '#5b8def';
const WEB_DISCOVERY_COLOR = '#ffb74d';
const WEB_GENRE_FALLBACK = '#6b7aa8';
const _artWebThumbCache = {};
let _artWebTipThumbTimer = null;
let _pathStub = { path: null };
function _webHexToRgba(hex, alpha) {
  const h = (hex || '').replace('#', '');
  if (h.length !== 6) return 'rgba(140,140,150,' + alpha + ')';
  return 'rgba(' + parseInt(h.slice(0,2),16) + ',' + parseInt(h.slice(2,4),16) + ',' + parseInt(h.slice(4,6),16) + ',' + alpha + ')';
}
function _miniStat(label, value, hue) {
  return '<div class="ms" data-label="' + label + '" data-value="' + value + '"></div>';
}
function buildArtistDetailPath(id, source) { return '/artist-detail/' + source + '/' + id; }
const escapeForInlineJs = (s) => String(s);
function _artWebEnsurePanel() {
  let p = document.getElementById('artweb-panel');
  if (p) return p;
  const c = document.getElementById('artist-web-container');
  if (!c) return null;
  p = document.createElement('div');
  p.id = 'artweb-panel';
  p.innerHTML = '<div id="artweb-panel-body"></div>';
  c.appendChild(p);
  return p;
}
function _artWebComputePath(a, b) { return _pathStub.path; }
function _artWebClosePanel() {}
function _artWebPathHint() {}
function _artWebHidePathHint() {}
function _artWebShowPathPanel() {}
function _artWebResolveTipThumb() {}
function _artWebStopPreview() {}
function showToast() {}
function updateWatchlistCount() {}
`;

interface Vanilla {
  _artistWeb: ArtistWebState;
  _artWebThumbCache: Record<string, string | null | undefined>;
  _pathStub: { path: string[] | null };
  _artWebShowTooltip: (key: string | null) => void;
  _artWebShowArtist: (node: string) => void;
  _artWebShowGenre: (node: string) => void;
  _artWebShowDiscovery: (node: string) => void;
  _artWebShowPathPanel: (path: string[]) => void;
  _artWebRenderLegend: (built: unknown) => void;
  _artWebPathClick: (node: string) => void;
  _artWebHover: (node: string | null) => void;
  _artWebClickNode: (node: string) => void;
  _artWebClearSelection: () => void;
  artWebToggleGenre: (g: string) => void;
  _artWebPopulateGenreList: (q: string) => void;
  artWebSearch: (q: string) => void;
}

const V = loadVanilla<Vanilla>(
  [
    '_artWebShowTooltip',
    '_artWebShowArtist',
    '_artWebShowGenre',
    '_artWebShowDiscovery',
    '_artWebShowPathPanel',
    '_artWebRenderLegend',
    '_artWebPathClick',
    '_artWebHover',
    '_artWebClickNode',
    '_artWebClearSelection',
    'artWebToggleGenre',
    '_artWebPopulateGenreList',
    'artWebSearch',
  ],
  PREAMBLE,
  ['_artistWeb', '_artWebThumbCache', '_pathStub'],
);

/** The markup the web writes into, as index.html declares it (4455-4518). */
function mount() {
  document.body.innerHTML = `
    <div class="artist-map-container" id="artist-web-container">
      <div class="artist-map-toolbar">
        <div class="artmap-stats" id="artist-web-stats"></div>
        <input id="artist-web-search">
        <button id="artweb-edges-btn"><span>Edges</span></button>
      </div>
      <div class="artmap-content-row">
        <div class="artmap-genre-sidebar" id="artweb-genre-sidebar" style="display:none;">
          <div class="artmap-genre-sidebar-header"><span>Genres</span>
            <input type="text" class="artmap-genre-sidebar-search"></div>
          <div class="artmap-genre-sidebar-list" id="artweb-genre-sidebar-list"></div>
        </div>
        <div id="artist-web-canvas"></div>
        <div id="artist-web-legend" class="artweb-legend" style="display:none;"></div>
      </div>
      <div class="artist-map-search-results" id="artist-web-search-results"></div>
      <div class="artist-map-tooltip" id="artist-web-tooltip"></div>
    </div>`;
}

function sync(state: Partial<ArtistWebState> = {}) {
  const base: Partial<ArtistWebState> = {
    index: [],
    searchMatch: null,
    focusSet: null,
    focusRoot: null,
    selectedKey: null,
    selectedFocus: null,
    genreFilter: null,
    genreCounts: null,
    pathMode: false,
    pathNodes: null,
    pathPairs: null,
    pathSource: null,
    pathTarget: null,
    pathResult: null,
    edgeDeclutter: false,
    lens: 'genre',
    graph: null,
    home: null,
    spreadRoot: null,
    spreadSet: null,
    spreadActive: null,
    discoveryData: null,
    betweenCache: null,
    gen: 0,
    ...state,
  };
  for (const k of Object.keys(base)) {
    const v = (base as Record<string, unknown>)[k];
    (artistWeb as Record<string, unknown>)[k] = v instanceof Set ? new Set(v) : v;
    (V._artistWeb as Record<string, unknown>)[k] = v instanceof Set ? new Set(v) : v;
  }
}

beforeEach(() => {
  mount();
  sync();
});

// ── Constants ────────────────────────────────────────────────────────────────

describe('the tuning still matches discover.js', () => {
  it('keeps the endpoints', () => {
    expect(SOURCE).toContain(WEB_LIBRARY_URL);
    expect(SOURCE).toContain(WEB_DISCOVERY_URL);
    expect(SOURCE).toContain(WEB_NO_CANDIDATES);
  });

  it('keeps the layout settings', () => {
    const s = webLayoutSettings({ inferred: true });
    expect(s).toMatchObject({
      barnesHutOptimize: true,
      linLogMode: true,
      outboundAttractionDistribution: true,
      adjustSizes: true,
      gravity: 1.2,
      scalingRatio: 3,
      slowDown: 4,
      inferred: true,
    });
    expect(SOURCE).toContain('gravity: 1.2, scalingRatio: 3, slowDown: 4');
    expect(SOURCE).toContain(`iterations: ${WEB_SYNC_ITERATIONS}`);
  });

  it('keeps the settle budget', () => {
    expect(webSettleBudget(0)).toBe(1600);
    expect(webSettleBudget(1000)).toBe(3200);
    expect(webSettleBudget(100000)).toBe(11000); //  capped
    expect(SOURCE).toContain('Math.min(11000, 1600 + graph.order * 1.6)');
  });

  it('keeps the sigma settings', () => {
    expect(SOURCE).toContain(
      `labelRenderedSizeThreshold: ${WEB_SIGMA_SETTINGS.labelRenderedSizeThreshold}`,
    );
    expect(SOURCE).toContain(`labelGridCellSize: ${WEB_SIGMA_SETTINGS.labelGridCellSize}`);
    expect(SOURCE).toContain('hideEdgesOnMove: true');
    expect(SOURCE).toContain('hideLabelsOnMove: true');
  });

  it('keeps the camera ratios, including the inverted zoom buttons', () => {
    // A ratio BELOW 1 is zoomed IN, which is why the + button passes 0.7.
    expect(WEB_ZOOM_IN).toBeLessThan(1);
    expect(WEB_ZOOM_OUT).toBeGreaterThan(1);
    // The vanilla buttons' onclick literals died with the index.html markup;
    // the LIVING markup is the React overlay, which must pass the same
    // ratios. LITERALS on the right, same reason as below.
    expect(OVERLAY).toContain('onZoom(0.7)');
    expect(OVERLAY).toContain('onZoom(1.4)');
    // LITERALS. Interpolating the constant lets a mutation pick a value that
    // happens to appear elsewhere in the file — 0.12 → 0.15 passed that way,
    // because artWebFocusNode really does use 0.15.
    expect(WEB_CAMERA.focusRatio).toBe(0.15);
    expect(WEB_CAMERA.cameraToRatio).toBe(0.12);
    // The path panel zooms in CLOSER than search focus does.
    expect(WEB_CAMERA.cameraToRatio).toBeLessThan(WEB_CAMERA.focusRatio);
    expect(SOURCE).toContain('ratio: 0.15');
    expect(SOURCE).toContain('ratio: 0.12');
  });

  it('keeps the expand + legend + card caps', () => {
    expect(SOURCE).toContain(`per: ${WEB_EXPAND_PER}`);
    expect(SOURCE).toContain(`.slice(0, ${WEB_LEGEND_LIMIT})`);
    expect(SOURCE).toContain(`members.slice(0, ${WEB_GENRE_MEMBERS})`);
    expect(SOURCE).toContain(`genres.slice(0, ${WEB_DISCOVERY_GENRES})`);
  });

  it('keeps the four help shortcuts', () => {
    expect(WEB_SHORTCUTS.map((s) => s.action)).toEqual([
      'Focus search',
      'Fit to view',
      'Zoom in / out',
      'Back / close',
    ]);
    for (const s of WEB_SHORTCUTS) expect(SOURCE).toContain(`<span>${s.action}</span>`);
  });
});

// ── The CDN globals ──────────────────────────────────────────────────────────

describe('resolving the CDN globals', () => {
  it('accepts graphology as either a namespace or the class itself', () => {
    const cls = class {} as never;
    expect(webResolveGraph({ graphology: { Graph: cls } })).toBe(cls);
    expect(webResolveGraph({ graphology: cls })).toBe(cls);
    expect(webResolveGraph({})).toBeNull();
  });

  it('needs BOTH libraries before anything is attempted', () => {
    const cls = class {} as never;
    expect(webLibsReady({ graphology: cls, Sigma: {} })).toBe(true);
    expect(webLibsReady({ graphology: cls })).toBe(false);
    expect(webLibsReady({ Sigma: {} })).toBe(false);
  });
});

// ── Lifecycle ────────────────────────────────────────────────────────────────

describe('open + close lifecycle', () => {
  it('only accepts a known lens from a deep link', () => {
    expect(webResolveLens('community', 'genre')).toBe('community');
    expect(webResolveLens('nonsense', 'community')).toBe('community');
    expect(webResolveLens(undefined, null)).toBe('genre');
  });

  it('snapshots the siblings only when NOT already open', () => {
    // A re-entrant open (the error card's Retry) would otherwise record every
    // sibling's display as 'none' and blank Discover on close.
    expect(webShouldSnapshotSiblings('none')).toBe(true);
    expect(webShouldSnapshotSiblings('')).toBe(true);
    expect(webShouldSnapshotSiblings('flex')).toBe(false);
  });

  it('invalidates in-flight fetches by generation', () => {
    sync({ gen: 4 });
    const mine = webNextGen();
    expect(mine).toBe(5);
    expect(webResultIsCurrent(mine)).toBe(true);
    webNextGen();
    expect(webResultIsCurrent(mine)).toBe(false);
  });

  it('also bails when the user has moved off the lens that asked', () => {
    sync({ lens: 'discovery' });
    const mine = webNextGen();
    expect(webResultIsCurrent(mine, 'discovery')).toBe(true);
    artistWeb.lens = 'genre';
    expect(webResultIsCurrent(mine, 'discovery')).toBe(false);
  });

  it('refuses to cache a discovery error body', () => {
    // A 500 resolves r.json() too; caching {"error": …} used to leave the lens
    // permanently blank with no retry.
    expect(webDiscoveryPayloadOk(true, { nodes: [] })).toBe(true);
    expect(webDiscoveryPayloadOk(false, { nodes: [] })).toBe(false);
    expect(webDiscoveryPayloadOk(true, { error: 'boom', nodes: [] })).toBe(false);
    expect(webDiscoveryPayloadOk(true, {})).toBe(false);
  });

  it('fetches discovery only the first time it is viewed', () => {
    sync({ discoveryData: null });
    expect(webNeedsDiscoveryFetch('discovery')).toBe(true);
    expect(webNeedsDiscoveryFetch('genre')).toBe(false);
    sync({ discoveryData: { nodes: [] } });
    expect(webNeedsDiscoveryFetch('discovery')).toBe(false);
  });

  it('guides rather than blanking on a zero-candidate payload', () => {
    expect(webDiscoveryIsEmpty('discovery', 0)).toBe(true);
    expect(webDiscoveryIsEmpty('discovery', 5)).toBe(false);
    expect(webDiscoveryIsEmpty('genre', 0)).toBe(false);
  });

  it('renames the sidebar for the community lens', () => {
    expect(webSidebarHeading('community')).toBe('Communities');
    expect(webSidebarHeading('genre')).toBe('Genres');
    expect(webSidebarHeading('discovery')).toBe('Genres');
  });

  it('packs by genre except on discovery', () => {
    expect(webPreseedOptions('genre')).toEqual({ hierarchyAttributes: ['genre'] });
    expect(webPreseedOptions('community')).toEqual({ hierarchyAttributes: ['genre'] });
    expect(webPreseedOptions('discovery')).toEqual({});
  });

  it('clears every piece of view state before a rebuild', () => {
    sync({
      searchMatch: new Set(['a']),
      focusSet: new Set(['a']),
      focusRoot: 'a',
      selectedKey: 'a',
      genreFilter: new Set(['Rock']),
      pathNodes: new Set(['a']),
      index: [{ key: 'a', label: 'A' }],
      home: { a: { x: 0, y: 0 } },
      sizeBy: 'influence',
    });
    webResetForRender();
    expect(artistWeb.searchMatch).toBeNull();
    expect(artistWeb.focusSet).toBeNull();
    expect(artistWeb.selectedKey).toBeNull();
    expect(artistWeb.genreFilter).toBeNull();
    expect(artistWeb.pathNodes).toBeNull();
    expect(artistWeb.index).toEqual([]);
    expect(artistWeb.home).toBeNull();
    expect(artistWeb.sizeBy).toBe('popularity');
  });

  it('drops the graph reference on close', () => {
    // So a late async re-select — expand-after-close — cannot refresh a dead one.
    sync({ graph: new FakeGraph(), pathMode: true, gen: 2 });
    webResetForClose();
    expect(artistWeb.graph).toBeNull();
    expect(artistWeb.pathMode).toBe(false);
    expect(artistWeb.gen).toBe(3);
  });
});

// ── Search ───────────────────────────────────────────────────────────────────

describe('search', () => {
  const index = [
    { key: 'a', label: 'Aphex Twin' },
    { key: 'b', label: 'Boards of Canada' },
    { key: 'c', label: 'APHEX' },
  ];

  it('matches the vanilla over the loaded index', () => {
    for (const q of ['', 'a', 'ap', 'APH', 'canada', 'zz', '  ap  ']) {
      sync({ index });
      V._artistWeb.index = index;
      V.artWebSearch(q);
      const theirs = V._artistWeb.searchMatch ? [...V._artistWeb.searchMatch].sort() : null;
      const mine = webSearchActive(q)
        ? webSearchHits(q)
            .map((h) => h.key)
            .sort()
        : null;
      expect(mine).toEqual(theirs);
    }
  });

  it('needs two characters', () => {
    sync({ index });
    expect(webSearchActive('a')).toBe(false);
    expect(webSearchActive('ap')).toBe(true);
    expect(WEB_SEARCH_MIN_CHARS).toBe(2);
  });

  it('is case-insensitive and substring-based', () => {
    sync({ index });
    expect(webSearchHits('APH').map((h) => h.key)).toEqual(['a', 'c']);
    expect(webSearchHits('of ca').map((h) => h.key)).toEqual(['b']);
  });

  it('takes the first match on Enter, and nothing when there are none', () => {
    sync({ searchMatch: new Set(['b', 'a']) });
    expect(webSearchEnterTarget()).toBe('b');
    sync({ searchMatch: new Set() });
    expect(webSearchEnterTarget()).toBeNull();
    sync({ searchMatch: null });
    expect(webSearchEnterTarget()).toBeNull();
  });
});

// ── Hover + selection ────────────────────────────────────────────────────────

describe('hover and selection', () => {
  function graph() {
    const g = new FakeGraph();
    g.addNode('a', { kind: 'artist', label: 'A' });
    g.addNode('b', { kind: 'artist', label: 'B' });
    g.addNode('c', { kind: 'artist', label: 'C' });
    g.addNode('hub', { kind: 'genre', label: 'Rock' });
    g.addEdge('a', 'b', { kind: 'similarity' });
    g.addEdge('hub', 'a', { kind: 'membership' });
    return g;
  }

  it('focuses a node together with its neighbours', () => {
    expect([...webFocusSet(graph(), 'a')].sort()).toEqual(['a', 'b', 'hub']);
  });

  it('matches the vanilla on hover in and out', () => {
    const g = graph();
    sync({ graph: g });
    V._artistWeb.graph = g;
    V._artWebHover('a');
    const theirs = {
      focus: [...(V._artistWeb.focusSet as Set<string>)].sort(),
      root: V._artistWeb.focusRoot,
    };
    webApplyHover(g, 'a');
    expect({
      focus: [...(artistWeb.focusSet as Set<string>)].sort(),
      root: artistWeb.focusRoot,
    }).toEqual(theirs);

    V._artWebHover(null);
    webApplyHover(g, null);
    expect(artistWeb.focusSet).toEqual(V._artistWeb.focusSet);
    expect(artistWeb.focusRoot).toBe(V._artistWeb.focusRoot);
  });

  it('restores the click-selection when the hover clears', () => {
    const g = graph();
    sync({ graph: g });
    webApplySelection(g, 'b');
    webApplyHover(g, 'a');
    expect(artistWeb.focusRoot).toBe('a');
    webApplyHover(g, null);
    expect(artistWeb.focusRoot).toBe('b'); //  back to the selection, not cleared
    expect([...(artistWeb.focusSet as Set<string>)].sort()).toEqual(['a', 'b']);
  });

  it('does not dim while tracing a path', () => {
    const g = graph();
    sync({ graph: g, pathMode: true });
    webApplyHover(g, 'a');
    expect(artistWeb.focusSet).toBeNull();
    expect(artistWeb._hoverNode).toBe('a'); //  …but the tooltip still knows
  });

  it('lets a click supersede a search dim', () => {
    const g = graph();
    sync({ graph: g, searchMatch: new Set(['c']) });
    webApplySelection(g, 'a');
    expect(artistWeb.searchMatch).toBeNull();
    expect(artistWeb.selectedKey).toBe('a');
  });

  it('routes each node kind to its own card', () => {
    expect(webCardKind('genre')).toBe('genre');
    expect(webCardKind('discovery')).toBe('discovery');
    expect(webCardKind('owned')).toBe('artist');
    expect(webCardKind('artist')).toBe('artist');
    expect(webCardKind(undefined)).toBe('artist');
  });

  it('clears everything on deselect', () => {
    const g = graph();
    sync({ graph: g });
    webApplySelection(g, 'a');
    webClearSelection();
    expect([artistWeb.selectedKey, artistWeb.focusSet, artistWeb.focusRoot]).toEqual([
      null,
      null,
      null,
    ]);
  });
});

// ── The tooltip ──────────────────────────────────────────────────────────────

describe('webTooltip', () => {
  function graph() {
    const g = new FakeGraph();
    g.addNode('a', { kind: 'artist', label: 'A', primaryGenre: 'idm', artistId: 7 });
    g.addNode('b', { kind: 'artist', label: 'B' });
    g.addNode('solo', { kind: 'artist', label: 'Solo' });
    g.addNode('hub', { kind: 'genre', label: 'Rock' });
    g.addNode('cand', { kind: 'discovery', label: 'Cand', image_url: '/c.jpg' });
    g.addEdge('a', 'b', { kind: 'similarity' });
    g.addEdge('hub', 'a', { kind: 'membership' });
    return g;
  }

  const cases: [string, string, Record<string, string | null | undefined>][] = [
    ['an artist with one similarity link', 'a', {}],
    ['an artist with none', 'solo', {}],
    ['a genre hub reports MEMBERS, not connections', 'hub', {}],
    ['a discovery candidate ships its own image', 'cand', {}],
    ['an owned artist with a cached thumb', 'a', { 7: '/cached.jpg' }],
    ['an owned artist already tried and found nothing', 'a', { 7: '' }],
    // `null` means a request is IN FLIGHT. Only `undefined` means untried, which
    // is why the test is `=== undefined` rather than `== null`.
    ['an owned artist whose thumb is already in flight', 'a', { 7: null }],
  ];

  for (const [label, key, cache] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const g = graph();
      sync({ graph: g });
      V._artistWeb.graph = g;
      for (const k of Object.keys(V._artWebThumbCache)) delete V._artWebThumbCache[k];
      Object.assign(V._artWebThumbCache, cache);
      V._artistWeb._mouse = { x: 0, y: 0 };
      V._artWebShowTooltip(key);
      const tip = document.getElementById('artist-web-tooltip') as HTMLElement;
      const mine = webTooltip(g, key, cache);

      expect(tip.querySelector('.artmap-tip-name')?.textContent).toBe(mine.label);
      expect(tip.querySelector('.artmap-tip-badge')?.textContent ?? '').toBe(mine.badge);
      expect(tip.querySelector('.artmap-tip-conn')?.textContent ?? '').toBe(mine.connectionText);
      expect(tip.querySelector('.artmap-tip-genres span')?.textContent ?? '').toBe(mine.genre);
      const img = tip.querySelector('img.artmap-tip-img');
      expect(img?.getAttribute('src') ?? null).toBe(mine.imageUrl);
    });
  }

  it('counts SIMILARITY links only, so it disagrees with the card by one', () => {
    const g = graph();
    // 'a' has one similarity edge and one membership edge to its genre hub.
    expect(webTooltip(g, 'a', {}).connections).toBe(1);
    expect(g.degree('a')).toBe(2);
  });

  it('asks for a thumb only for an owned artist it has not tried yet', () => {
    const g = graph();
    expect(webTooltip(g, 'a', {}).needsThumb).toBe(true);
    expect(webTooltip(g, 'a', { 7: '' }).needsThumb).toBe(false);
    expect(webTooltip(g, 'a', { 7: null }).needsThumb).toBe(false); //  in flight
    expect(webTooltip(g, 'cand', {}).needsThumb).toBe(false);
    expect(webTooltip(g, 'hub', {}).needsThumb).toBe(false);
  });
});

// ── Path mode ────────────────────────────────────────────────────────────────

describe('path mode', () => {
  function graph() {
    const g = new FakeGraph();
    g.addNode('a', { kind: 'artist', label: 'Aphex Twin' });
    g.addNode('b', { kind: 'artist', label: 'Boards' });
    g.addNode('hub', { kind: 'genre', label: 'Rock' });
    return g;
  }

  it('refuses a genre hub', () => {
    const g = graph();
    sync();
    expect(webPathClick(g, 'hub', () => null)).toEqual({ kind: 'reject-hub' });
    expect(artistWeb.pathSource).toBeNull();
  });

  it('starts from the first artist clicked', () => {
    const g = graph();
    sync({ searchMatch: new Set(['b']), focusSet: new Set(['b']) });
    const out = webPathClick(g, 'a', () => null);
    expect(out).toEqual({ kind: 'start', node: 'a', label: 'Aphex Twin' });
    expect([...(artistWeb.pathNodes as Set<string>)]).toEqual(['a']);
    // Starting a trace clears any search dim or hover focus.
    expect(artistWeb.searchMatch).toBeNull();
    expect(artistWeb.focusSet).toBeNull();
  });

  it('ignores a second click on the same node', () => {
    const g = graph();
    sync();
    webPathClick(g, 'a', () => null);
    expect(webPathClick(g, 'a', () => null)).toEqual({ kind: 'same-node' });
  });

  it('reports both names when there is no path', () => {
    const g = graph();
    sync();
    webPathClick(g, 'a', () => null);
    expect(webPathClick(g, 'b', () => null)).toEqual({
      kind: 'no-path',
      from: 'Aphex Twin',
      to: 'Boards',
    });
    expect(webPathNoneHint('Aphex Twin', 'Boards')).toContain('No similarity path between');
  });

  it('treats a one-node result as no path', () => {
    const g = graph();
    sync();
    webPathClick(g, 'a', () => null);
    expect(webPathClick(g, 'b', () => ['a']).kind).toBe('no-path');
  });

  it('completes and keys the pairs order-independently', () => {
    const g = graph();
    sync();
    webPathClick(g, 'a', () => null);
    const out = webPathClick(g, 'b', () => ['a', 'x', 'b']);
    expect(out).toEqual({ kind: 'complete', path: ['a', 'x', 'b'] });
    expect([...(artistWeb.pathPairs as Set<string>)].sort()).toEqual(['a|x', 'b|x']);
  });

  it('restarts from the next click once a path is complete', () => {
    const g = graph();
    sync();
    webPathClick(g, 'a', () => null);
    webPathClick(g, 'b', () => ['a', 'b']);
    expect(webPathClick(g, 'b', () => null).kind).toBe('start');
  });

  it('matches the vanilla through a whole trace', () => {
    const g = graph();
    sync({ graph: g });
    V._artistWeb.graph = g;
    V._pathStub.path = ['a', 'b'];
    V._artWebPathClick('a');
    V._artWebPathClick('b');
    webPathClick(g, 'a', () => ['a', 'b']);
    webPathClick(g, 'b', () => ['a', 'b']);
    expect([...(artistWeb.pathNodes as Set<string>)].sort()).toEqual(
      [...(V._artistWeb.pathNodes as Set<string>)].sort(),
    );
    expect([...(artistWeb.pathPairs as Set<string>)].sort()).toEqual(
      [...(V._artistWeb.pathPairs as Set<string>)].sort(),
    );
    expect(artistWeb.pathResult).toEqual(V._artistWeb.pathResult);
  });

  it('clears cleanly', () => {
    const g = graph();
    sync();
    webPathClick(g, 'a', () => null);
    webClearPath();
    expect([
      artistWeb.pathSource,
      artistWeb.pathTarget,
      artistWeb.pathResult,
      artistWeb.pathNodes,
      artistWeb.pathPairs,
    ]).toEqual([null, null, null, null, null]);
  });

  it('names the start in its hint', () => {
    expect(webPathStartHint('Aphex Twin')).toBe(
      'Start: <b>Aphex Twin</b> — now click a second artist.',
    );
  });
});

// ── Preview, watchlist, expand ───────────────────────────────────────────────

describe('preview, watchlist and expand', () => {
  it('needs a deezer id to preview', () => {
    expect(
      webPreviewId([
        ['spotify', 's'],
        ['deezer', 'd'],
      ]),
    ).toBe('d');
    expect(webPreviewId([['spotify', 's']])).toBeNull();
    expect(webPreviewId(undefined)).toBeNull();
    expect(webCanPreview([['deezer', 'd']])).toBe(true);
    expect(webCanPreview([['itunes', 'i']])).toBe(false);
  });

  it('labels the preview button by state', () => {
    expect(WEB_PREVIEW_IDLE).toContain('Preview top track');
    expect(webPreviewPlayingLabel('Windowlicker')).toBe('⏸ Windowlicker');
    expect(webPreviewPlayingLabel(undefined)).toBe('⏸ Playing preview');
  });

  it('prefers spotify for a watchlist add, then whatever came first', () => {
    expect(
      webWatchlistPair([
        ['deezer', 'd'],
        ['spotify', 's'],
      ]),
    ).toEqual(['spotify', 's']);
    expect(
      webWatchlistPair([
        ['deezer', 'd'],
        ['itunes', 'i'],
      ]),
    ).toEqual(['deezer', 'd']);
    expect(webWatchlistPair([])).toBeNull();
    expect(webWatchlistPair(undefined)).toBeNull();
  });

  it('sends the SOURCE with the id, so the endpoint never guesses', () => {
    // A bare numeric deezer/itunes id used to be mistaken for a library row id
    // and could watch a completely different artist.
    const pair = webWatchlistPair([['deezer', '12345']]);
    expect(pair).toEqual(['deezer', '12345']);
  });

  it('rings new nodes around their parent', () => {
    expect(webExpandRing(0.5)).toBeCloseTo(1.1, 9);
    expect(webExpandRing(0)).toBeCloseTo(0.22, 9); //  the 0.1 fallback
    const p = webExpandPosition({ x: 10, y: 20 }, 0, 4, 2, 0);
    expect(p).toEqual({ x: 12, y: 20 });
    const q = webExpandPosition({ x: 10, y: 20 }, 1, 4, 2, 0);
    expect(q.x).toBeCloseTo(10, 9);
    expect(q.y).toBeCloseTo(22, 9);
  });

  it('recounts both kinds after an expand', () => {
    const g = new FakeGraph();
    g.addNode('o', { kind: 'owned' });
    g.addNode('d1', { kind: 'discovery' });
    g.addNode('d2', { kind: 'discovery' });
    g.addNode('x', { kind: 'artist' });
    expect(webCountKinds(g)).toEqual({ owned: 1, discovery: 2 });
  });
});

// ── The genre filter ─────────────────────────────────────────────────────────

describe('the genre filter', () => {
  it('matches the vanilla toggling on and off', () => {
    sync();
    for (const g of ['Rock', 'Jazz', 'Rock']) {
      V.artWebToggleGenre(g);
      webToggleGenre(g);
      const theirs = V._artistWeb.genreFilter ? [...V._artistWeb.genreFilter].sort() : null;
      const mine = artistWeb.genreFilter ? [...artistWeb.genreFilter].sort() : null;
      expect(mine).toEqual(theirs);
    }
  });

  it('becomes NULL rather than an empty set', () => {
    // The reducers test the filter for truthiness, and an empty Set is truthy —
    // leaving one would dim the entire graph.
    sync();
    webToggleGenre('Rock');
    expect(artistWeb.genreFilter?.size).toBe(1);
    webToggleGenre('Rock');
    expect(artistWeb.genreFilter).toBeNull();
  });

  it('lists genres largest first, filtered by the query', () => {
    sync({ genreFilter: new Set(['Jazz']) });
    const rows = webGenreRows({ Rock: 10, Jazz: 40, Folk: 2 }, '');
    expect(rows.map((r) => r.genre)).toEqual(['Jazz', 'Rock', 'Folk']);
    expect(rows[0].active).toBe(true);
    expect(rows[1].active).toBe(false);
    expect(webGenreRows({ Rock: 10, Jazz: 40 }, 'ro').map((r) => r.genre)).toEqual(['Rock']);
    expect(webGenreRows({ Rock: 10, Jazz: 40 }, ' RO ').map((r) => r.genre)).toEqual(['Rock']);
  });

  it('renames the edges button when decluttering', () => {
    expect(webEdgeButtonLabel(false)).toBe('Edges');
    expect(webEdgeButtonLabel(true)).toBe('Strong');
  });
});

// ── The legend + the cards ───────────────────────────────────────────────────

describe('the legend', () => {
  it('matches the vanilla for the discovery lens', () => {
    sync({ lens: 'discovery' });
    V._artistWeb.lens = 'discovery';
    V._artWebRenderLegend({ counts: {}, colorOf: () => '#fff' });
    const box = document.getElementById('artist-web-legend') as HTMLElement;
    const mine = webLegendItems('discovery', {}, () => '#fff');
    const rows = [...box.querySelectorAll('.artweb-legend-row')];
    expect(rows.map((r) => r.querySelector('.artweb-legend-label')?.textContent)).toEqual(
      mine.map((i) => i.label),
    );
  });

  it('matches the vanilla for the genre lens, biggest first', () => {
    const counts = { Rock: 40, Jazz: 90, Folk: 2 };
    const colorOf = (g: string) => `#${g.length}00000`;
    sync({ lens: 'genre' });
    V._artistWeb.lens = 'genre';
    V._artWebRenderLegend({ counts, colorOf });
    const box = document.getElementById('artist-web-legend') as HTMLElement;
    const mine = webLegendItems('genre', counts, colorOf);
    const rows = [...box.querySelectorAll('.artweb-legend-row')];
    expect(rows.map((r) => r.querySelector('.artweb-legend-label')?.textContent)).toEqual(
      mine.map((i) => i.label),
    );
    expect(rows.map((r) => r.querySelector('.artweb-legend-count')?.textContent)).toEqual(
      mine.map((i) => String(i.count)),
    );
    expect(mine[0].label).toBe('Jazz');
  });

  it('caps at eight groups', () => {
    const counts: Record<string, number> = {};
    for (let i = 0; i < 20; i++) counts[`G${i}`] = i;
    // A LITERAL 8 — asserting the constant moves with the mutation.
    expect(webLegendItems('genre', counts, () => '#fff')).toHaveLength(8);
    expect(WEB_LEGEND_LIMIT).toBe(8);
  });

  it('yields nothing to render for an empty group set', () => {
    expect(webLegendItems('genre', {}, () => '#fff')).toEqual([]);
  });
});

describe('the side-panel cards', () => {
  function graph() {
    const g = new FakeGraph();
    g.addNode('a', {
      kind: 'artist',
      label: 'Aphex Twin',
      baseColor: '#1db954',
      popularity: 88.6,
      primaryGenre: 'idm',
      artistId: 7,
    });
    g.addNode('b', { kind: 'artist', label: 'Boards', baseColor: '#e91e63', popularity: 40 });
    g.addNode('hub', { kind: 'genre', label: 'Rock', genre: 'Rock', baseColor: '#3f8cff' });
    g.addNode('cand', {
      kind: 'discovery',
      label: 'Cand',
      image_url: '/c.jpg',
      genresList: ['idm', 'ambient'],
      ids: [
        ['spotify', 'sp1'],
        ['deezer', 'dz1'],
      ],
    });
    g.addEdge('a', 'b', { kind: 'similarity' });
    g.addEdge('hub', 'a', { kind: 'membership' });
    g.addEdge('hub', 'b', { kind: 'membership' });
    return g;
  }
  const detail = (id: unknown, source: string) => `/artist-detail/${source}/${String(id)}`;

  it('matches the vanilla artist card', () => {
    const g = graph();
    sync({ graph: g, lens: 'genre' });
    V._artistWeb.graph = g;
    V._artWebShowArtist('a');
    const body = document.getElementById('artweb-panel-body') as HTMLElement;
    const mine = webArtistCard(g, 'a', 'genre', detail);

    expect(body.textContent).toContain(mine.label);
    expect(body.textContent).toContain(mine.primaryGenre);
    const stats = [...body.querySelectorAll('.ms')].map((s) => [
      s.getAttribute('data-label'),
      s.getAttribute('data-value'),
    ]);
    expect(stats).toEqual([
      ['Popularity', String(mine.popularity)],
      ['Connections', String(mine.connections)],
    ]);
    expect(body.querySelector('a[href^="/artist-detail/"]')?.getAttribute('href')).toBe(
      mine.detailPath,
    );
  });

  it('clamps popularity into 0-100 and rounds it', () => {
    const g = graph();
    g.setNodeAttribute('a', 'popularity', 250);
    expect(webArtistCard(g, 'a', 'genre', detail).popularity).toBe(100);
    g.setNodeAttribute('a', 'popularity', -5);
    expect(webArtistCard(g, 'a', 'genre', detail).popularity).toBe(0);
    g.setNodeAttribute('a', 'popularity', 62.6);
    expect(webArtistCard(g, 'a', 'genre', detail).popularity).toBe(63);
    g.setNodeAttribute('a', 'popularity', undefined);
    expect(webArtistCard(g, 'a', 'genre', detail).popularity).toBe(0);
  });

  it('offers Play radio only for a node with a library id', () => {
    const g = graph();
    expect(webArtistCard(g, 'a', 'genre', detail).canPlayRadio).toBe(true);
    expect(webArtistCard(g, 'b', 'genre', detail).canPlayRadio).toBe(false);
  });

  it('counts the membership edge, unlike the tooltip', () => {
    const g = graph();
    // Degree 2 (one similarity + one membership) where the tooltip says 1.
    expect(webArtistCard(g, 'a', 'genre', detail).connections).toBe(2);
    expect(webTooltip(g, 'a', {}).connections).toBe(1);
  });

  it('always links an owned artist to the LIBRARY route', () => {
    const g = graph();
    // a.source would be the server name ('plex'), which is not a detail source —
    // using it is what produced the broken /artist-detail/plex/… link.
    expect(webArtistCard(g, 'a', 'genre', detail).detailPath).toBe('/artist-detail/library/7');
    expect(webArtistCard(g, 'b', 'genre', detail).detailPath).toBeNull();
  });

  it('offers Expand only on the discovery lens', () => {
    const g = graph();
    expect(webArtistCard(g, 'a', 'genre', detail).canExpand).toBe(false);
    expect(webArtistCard(g, 'a', 'discovery', detail).canExpand).toBe(true);
  });

  it('matches the vanilla genre card', () => {
    const g = graph();
    sync({ graph: g });
    V._artistWeb.graph = g;
    V._artWebShowGenre('hub');
    const body = document.getElementById('artweb-panel-body') as HTMLElement;
    const mine = webGenreCard(g, 'hub');
    expect(body.textContent).toContain(mine.genre);
    expect(body.textContent).toContain(`${mine.total} artist`);
    const rows = [...body.querySelectorAll('[onclick^="_artWebGoToArtist"]')];
    expect(rows.map((r) => r.querySelectorAll('span')[1].textContent)).toEqual(
      mine.members.map((m) => m.label),
    );
  });

  it('orders genre members by popularity', () => {
    expect(webGenreCard(graph(), 'hub').members.map((m) => m.label)).toEqual([
      'Aphex Twin',
      'Boards',
    ]);
  });

  it('lists ARTIST neighbours only', () => {
    const g = graph();
    g.addNode('other-hub', { kind: 'genre', label: 'Jazz' });
    g.addNode('cand2', { kind: 'discovery', label: 'Cand2' });
    g.addEdge('hub', 'other-hub', { kind: 'membership' });
    g.addEdge('hub', 'cand2', { kind: 'discovery' });
    expect(webGenreCard(g, 'hub').members.map((m) => m.label)).toEqual(['Aphex Twin', 'Boards']);
  });

  it('reports the TRUE total above the thirty it lists', () => {
    const g = graph();
    for (let i = 0; i < 40; i++) {
      g.addNode(`x${i}`, { kind: 'artist', label: `X${i}`, popularity: i });
      g.addEdge('hub', `x${i}`, { kind: 'membership' });
    }
    const card = webGenreCard(g, 'hub');
    expect(card.members).toHaveLength(30); //  a LITERAL cap
    expect(WEB_GENRE_MEMBERS).toBe(30);
    expect(card.total).toBe(42); //  the honest count, not the shown one
  });

  it('matches the vanilla discovery card', () => {
    const g = graph();
    sync({ graph: g });
    V._artistWeb.graph = g;
    V._artWebShowDiscovery('cand');
    const body = document.getElementById('artweb-panel-body') as HTMLElement;
    const mine = webDiscoveryCard(g, 'cand', detail);
    expect(body.textContent).toContain(mine.label);
    expect(body.querySelector('img')?.getAttribute('src')).toBe(mine.imageUrl);
    expect(body.querySelector('a[href^="/artist-detail/"]')?.getAttribute('href')).toBe(
      mine.detailPath,
    );
    expect(!!body.querySelector('#artweb-preview-btn')).toBe(mine.canPreview);
    // Deliberately no expand button on an unowned candidate.
    expect(body.querySelector('#artweb-expand-btn')).toBeNull();
  });

  it('parses a JSON-string genre list, and survives a bare string', () => {
    const g = graph();
    g.setNodeAttribute('cand', 'genresList', '["a","b"]');
    expect(webDiscoveryCard(g, 'cand', detail).genres).toEqual(['a', 'b']);
    g.setNodeAttribute('cand', 'genresList', 'justone');
    // A bare string must not be spread into characters.
    expect(webDiscoveryCard(g, 'cand', detail).genres).toEqual(['justone']);
    g.setNodeAttribute('cand', 'genresList', null);
    expect(webDiscoveryCard(g, 'cand', detail).genres).toEqual([]);
  });

  it('offers Preview only when there is a DEEZER id, not merely any id', () => {
    const g = graph();
    g.setNodeAttribute('cand', 'ids', [['spotify', 'sp1']]);
    expect(webDiscoveryCard(g, 'cand', detail).canPreview).toBe(false);
    g.setNodeAttribute('cand', 'ids', [
      ['spotify', 'sp1'],
      ['deezer', 'dz1'],
    ]);
    expect(webDiscoveryCard(g, 'cand', detail).canPreview).toBe(true);
  });

  it('uses the FIRST id pair for an unowned artist’s detail page', () => {
    const g = graph();
    // The payload orders them spotify > deezer > itunes.
    expect(webDiscoveryCard(g, 'cand', detail).detailPath).toBe('/artist-detail/spotify/sp1');
    g.setNodeAttribute('cand', 'ids', []);
    expect(webDiscoveryCard(g, 'cand', detail).detailPath).toBeNull();
  });

  it('matches the vanilla path panel', () => {
    const g = graph();
    sync({ graph: g });
    V._artistWeb.graph = g;
    V._artWebShowPathPanel(['a', 'b']);
    const body = document.getElementById('artweb-panel-body') as HTMLElement;
    const mine = webPathRows(g, ['a', 'b']);
    const rows = [...body.querySelectorAll('[onclick^="_artWebCameraTo"]')];
    expect(rows.map((r) => r.querySelectorAll('span')[1].textContent)).toEqual(
      mine.map((m) => m.label),
    );
    expect(mine.map((m) => m.tag)).toEqual(['start', 'end']);
  });

  it('marks only the ends of a longer path', () => {
    const g = graph();
    g.addNode('mid', { label: 'Mid', baseColor: '#fff' });
    expect(webPathRows(g, ['a', 'mid', 'b']).map((r) => r.tag)).toEqual(['start', '', 'end']);
  });
});
