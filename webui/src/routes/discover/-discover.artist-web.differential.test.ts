import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { loadVanilla } from '../../test/vanilla-extract';
import {
  type ArtistWebState,
  WEB_CANVAS_BG,
  WEB_DIM_NODE,
  WEB_DISCOVERY_COLOR,
  WEB_GENRE_FALLBACK,
  WEB_OWNED_COLOR,
  WEB_PALETTE,
  WEB_STAR_ANCHORS,
  WEB_STAR_COUNT,
  WEB_STAR_SIZE,
  type WebGraph,
  type WebGraphCtor,
  type WebPayload,
  type WebRawNode,
  artWebApplySize,
  artWebBuildCommunity,
  artWebBuildDiscovery,
  artWebBuildGenre,
  artWebEdgeReducer,
  artWebEdgeThreshold,
  artWebFinishLayout,
  artWebNodeReducer,
  artWebPathPairs,
  artWebPathSummary,
  artWebSetSpread,
  artWebSimGraph,
  artWebSpreadTick,
  artistWeb,
  webDrawLabel,
  webEdgeAlpha,
  webEdgeSize,
  webGenreColorMap,
  webHexToRgba,
  webTopArtists,
} from './-discover.artist-web';

/**
 * Differential parity for the Artist Web's core.
 *
 * The three lens builders take the Graph CONSTRUCTOR as an argument, so both
 * sides can be handed the same minimal graphology stand-in and their finished
 * graphs compared attribute by attribute. `Math.random` (the builders' initial
 * x/y) is seeded and rewound per side, so identical inputs really do produce
 * identical output rather than merely similar output.
 *
 * A mutation pass raised 86 mutants and 85 die here. The survivor is EQUIVALENT
 * and is recorded rather than hidden: the `|| 0.0001` distance guard in
 * `artWebSpreadTick`. With a neighbour exactly on the root, the guard yields a
 * target equal to the node's own home (no movement); without it the target is
 * NaN, the movement comparison is false, and there is still no movement. It is
 * defensive, not load-bearing.
 */
const SOURCE = readFileSync(resolve(process.cwd(), 'static/discover.js'), 'utf8');

// ── A minimal graphology ─────────────────────────────────────────────────────

type Attrs = Record<string, unknown>;

class FakeGraph implements WebGraph {
  nodes = new Map<string, Attrs>();
  edges = new Map<string, { source: string; target: string; attrs: Attrs }>();
  undirected: boolean;

  constructor(opts?: { type?: string }) {
    this.undirected = opts?.type === 'undirected';
  }

  get order() {
    return this.nodes.size;
  }
  get size() {
    return this.edges.size;
  }
  private key(s: string, t: string) {
    return this.undirected && s > t ? `${t}|${s}` : `${s}|${t}`;
  }
  addNode(key: string, attrs: Attrs) {
    if (this.nodes.has(key)) throw new Error(`duplicate node ${key}`);
    this.nodes.set(key, { ...attrs });
  }
  addEdge(source: string, target: string, attrs: Attrs) {
    this.edges.set(this.key(source, target), { source, target, attrs: { ...attrs } });
  }
  hasNode(key: string) {
    return this.nodes.has(key);
  }
  hasEdge(source: string, target: string) {
    return this.edges.has(this.key(source, target)) || this.edges.has(this.key(target, source));
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
  setNodeAttribute(key: string, name: string, value: unknown) {
    (this.nodes.get(key) as Attrs)[name] = value;
  }
  mergeNodeAttributes(key: string, attrs: Attrs) {
    Object.assign(this.nodes.get(key) as Attrs, attrs);
  }
  mergeEdgeAttributes(edge: string, attrs: Attrs) {
    Object.assign(this.edges.get(edge)!.attrs, attrs);
  }
  forEachNode(cb: (key: string, attrs: Attrs) => void) {
    [...this.nodes.entries()].forEach(([k, a]) => cb(k, a));
  }
  forEachEdge(
    a: string | ((e: string, attrs: Attrs, s: string) => void),
    b?: (e: string, attrs: Attrs, s: string) => void,
  ) {
    const node = typeof a === 'string' ? a : null;
    const cb = (typeof a === 'string' ? b : a) as (e: string, attrs: Attrs, s: string) => void;
    [...this.edges.entries()].forEach(([k, e]) => {
      if (node && e.source !== node && e.target !== node) return;
      cb(k, e.attrs, e.source);
    });
  }
  forEachNeighbor(key: string, cb: (nb: string, attrs: Attrs) => void) {
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

/** A deterministic, rewindable stand-in for the builders' `Math.random`. */
let seed = 1;
function seededRandom() {
  seed = (seed * 1664525 + 1013904223) % 4294967296;
  return seed / 4294967296;
}
function rewind() {
  seed = 1;
}

/** A comparable snapshot of a built graph. */
function snapshot(g: WebGraph) {
  const fg = g as FakeGraph;
  return {
    nodes: [...fg.nodes.entries()].map(([k, a]) => [k, a]),
    edges: [...fg.edges.entries()].map(([k, e]) => [k, e.source, e.target, e.attrs]),
  };
}

// ── The vanilla side ─────────────────────────────────────────────────────────

const PREAMBLE = `
let _artistWeb = {
  sigma: null, graph: null, onKey: null, gen: 0, lens: 'genre',
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
const WEB_PALETTE = ['#1db954', '#e91e63', '#3f8cff', '#ff9800', '#9c27b0', '#00bcd4', '#ffd54f',
    '#f44336', '#8bc34a', '#ff5722', '#7c4dff', '#26c6da', '#cddc39', '#ff4081', '#009688', '#c0846b'];
const WEB_GENRE_FALLBACK = '#6b7aa8';
const WEB_OWNED_COLOR = '#5b8def';
const WEB_DISCOVERY_COLOR = '#ffb74d';
const WEB_STAR_COUNT = 20;
const WEB_STAR_SIZE = 8;
const _WEB_DIM_NODE = '#2b2b34';
const _stubs = { sim: null, btw: null };
function _artWebSimGraph() { return _stubs.sim; }
function _artWebBetweenness() { return _stubs.btw; }
function _artWebStartFX() {}
function _artWebSyncSizeButtons() {}
function _artWebClearSpread() { _artistWeb.spreadRoot = null; _artistWeb.spreadSet = null; _artWebStartFX(); }
`;

interface Vanilla {
  _artistWeb: ArtistWebState;
  /** A mutable holder — `let` bindings inside the preamble cannot be reassigned
   *  from out here, so the size stubs read through this instead. */
  _stubs: { sim: WebGraph | null; btw: Record<string, number> | null };
  _webEdgeAlpha: (w: unknown) => number;
  _webEdgeSize: (w: unknown) => number;
  _webHexToRgba: (hex: unknown, a: number) => string;
  _webGenreColorMap: (nodes: unknown[]) => { color: (g: string) => string; counts: Attrs };
  _webTopArtists: (nodes: unknown[], n: number) => Set<string>;
  _webDrawLabel: (ctx: unknown, data: unknown, settings: unknown) => void;
  _artWebBuildGenre: (
    data: unknown,
    G: unknown,
  ) => { graph: WebGraph; stats: string; counts: Attrs };
  _artWebBuildCommunity: (
    data: unknown,
    G: unknown,
  ) => { graph: WebGraph; stats: string; counts: Attrs; colorOf: (r: string) => string };
  _artWebBuildDiscovery: (
    data: unknown,
    G: unknown,
  ) => { graph: WebGraph; stats: string; counts: Attrs };
  _artWebFinishLayout: (g: WebGraph) => void;
  _artWebNodeReducer: (node: string, data: Attrs) => Attrs;
  _artWebEdgeReducer: (edge: string, data: Attrs) => Attrs;
  _artWebSpreadTick: () => void;
  _artWebSetSpread: (root: string, focus: Set<string>) => void;
  _artWebApplySize: (mode: string) => void;
  _artWebComputePath: (a: string, b: string) => string[] | null;
}

const V = loadVanilla<Vanilla>(
  [
    '_webEdgeAlpha',
    '_webEdgeSize',
    '_webHexToRgba',
    '_webGenreColorMap',
    '_webTopArtists',
    '_webDrawLabel',
    '_artWebBuildGenre',
    '_artWebBuildCommunity',
    '_artWebBuildDiscovery',
    '_artWebFinishLayout',
    '_artWebNodeReducer',
    '_artWebEdgeReducer',
    '_artWebSpreadTick',
    '_artWebSetSpread',
    '_artWebApplySize',
    '_artWebComputePath',
  ],
  PREAMBLE,
  ['_artistWeb', '_stubs'],
);

/** Reset both singletons to the same state. */
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
    pathNodes: null,
    pathPairs: null,
    pathSource: null,
    pathTarget: null,
    pathResult: null,
    edgeDeclutter: false,
    edgeThreshold: 2,
    graph: null,
    home: null,
    spreadRoot: null,
    spreadSet: null,
    spreadActive: null,
    spreadPush: 0,
    cursorFX: true,
    simGraph: null,
    ...state,
  };
  for (const k of Object.keys(base)) {
    const v = (base as Record<string, unknown>)[k];
    (artistWeb as Record<string, unknown>)[k] = v instanceof Set ? new Set(v) : v;
    (V._artistWeb as Record<string, unknown>)[k] = v instanceof Set ? new Set(v) : v;
  }
}

beforeEach(() => {
  sync();
  rewind();
  vi.spyOn(Math, 'random').mockImplementation(seededRandom);
});

// ── The palette ──────────────────────────────────────────────────────────────

describe('the palette still matches discover.js', () => {
  it('keeps all sixteen genre colours, in order', () => {
    expect(WEB_PALETTE).toHaveLength(16);
    for (const c of WEB_PALETTE) expect(SOURCE).toContain(c);
    // Order matters: clusters are assigned by size rank.
    expect(SOURCE).toContain(`'${WEB_PALETTE[0]}', '${WEB_PALETTE[1]}', '${WEB_PALETTE[2]}'`);
  });

  it('keeps the four semantic colours and the dim value', () => {
    expect(SOURCE).toContain(`WEB_GENRE_FALLBACK = '${WEB_GENRE_FALLBACK}'`);
    expect(SOURCE).toContain(`WEB_OWNED_COLOR = '${WEB_OWNED_COLOR}'`);
    expect(SOURCE).toContain(`WEB_DISCOVERY_COLOR = '${WEB_DISCOVERY_COLOR}'`);
    expect(SOURCE).toContain(`WEB_CANVAS_BG = '${WEB_CANVAS_BG}'`);
    expect(SOURCE).toContain(`_WEB_DIM_NODE = '${WEB_DIM_NODE}'`);
  });

  it('keeps the star tuning', () => {
    expect(SOURCE).toContain(`WEB_STAR_COUNT = ${WEB_STAR_COUNT}`);
    expect(SOURCE).toContain(`WEB_STAR_SIZE = ${WEB_STAR_SIZE}`);
    expect(SOURCE).toContain(`.slice(0, ${WEB_STAR_ANCHORS})`);
  });
});

// ── Edge styling ─────────────────────────────────────────────────────────────

describe('edge styling', () => {
  const weights = [undefined, 0, 1, 2, 3, 5, 12, 12.8, 13, 20, 100, -1];
  for (const w of weights) {
    it(`webEdgeAlpha matches the vanilla for ${w}`, () => {
      expect(webEdgeAlpha(w)).toBe(V._webEdgeAlpha(w));
    });
    it(`webEdgeSize matches the vanilla for ${w}`, () => {
      expect(webEdgeSize(w)).toBe(V._webEdgeSize(w));
    });
  }

  it('caps alpha at 0.4 so nothing goes opaque at rest', () => {
    expect(webEdgeAlpha(1000)).toBe(0.4);
    expect(webEdgeAlpha(12.8)).toBe(0.4);
    expect(webEdgeAlpha(12)).toBeLessThan(0.4);
  });

  const hexes: [unknown, number][] = [
    ['#1db954', 0.5],
    ['1db954', 0.5],
    ['#FFF', 0.5],
    ['', 0.5],
    [null, 0.5],
    [undefined, 0.5],
    ['#zzzzzz', 0.5],
    ['#000000', 0],
    ['#ffffff', 1],
    ['#1db95', 0.5],
    ['#1db9544', 0.5],
  ];
  for (const [hex, alpha] of hexes) {
    it(`webHexToRgba matches the vanilla for ${String(hex)}/${alpha}`, () => {
      expect(webHexToRgba(hex as string, alpha)).toBe(V._webHexToRgba(hex, alpha));
    });
  }

  it('falls back to a neutral gray rather than throwing on bad hex', () => {
    expect(webHexToRgba('nope', 0.3)).toBe('rgba(140,140,150,0.3)');
  });
});

// ── Cluster colours + stars ──────────────────────────────────────────────────

describe('webGenreColorMap', () => {
  const cases: [string, WebRawNode[]][] = [
    ['no nodes', []],
    ['genre hubs are not counted', [{ key: 'g', label: 'G', kind: 'genre', genre: 'Rock' }]],
    [
      'clusters ranked by size',
      [
        { key: 'a', label: 'A', kind: 'artist', cluster: 'Rock' },
        { key: 'b', label: 'B', kind: 'artist', cluster: 'Rock' },
        { key: 'c', label: 'C', kind: 'artist', cluster: 'Jazz' },
      ],
    ],
    [
      'Other is always gray, wherever it ranks',
      [
        ...Array.from({ length: 9 }, (_, i) => ({
          key: `o${i}`,
          label: 'O',
          kind: 'artist',
          cluster: 'Other',
        })),
        { key: 'a', label: 'A', kind: 'artist', cluster: 'Rock' },
      ],
    ],
    [
      'more clusters than the palette cycles rather than going gray',
      Array.from({ length: 25 }, (_, i) => ({
        key: `a${i}`,
        label: 'A',
        kind: 'artist',
        cluster: `G${String(i).padStart(2, '0')}`,
      })),
    ],
    ['a node with no cluster is skipped', [{ key: 'a', label: 'A', kind: 'artist' }]],
    // Only ARTISTS are counted. A hub carrying a cluster of its own must not
    // inflate that cluster — dropping the kind check is invisible without one.
    [
      'a genre hub carrying a cluster is still not counted',
      [
        { key: 'g', label: 'G', kind: 'genre', genre: 'Rock', cluster: 'Rock' },
        { key: 'a', label: 'A', kind: 'artist', cluster: 'Rock' },
      ],
    ],
  ];

  for (const [label, nodes] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const mine = webGenreColorMap(nodes);
      const theirs = V._webGenreColorMap(nodes);
      expect(mine.counts).toEqual(theirs.counts);
      for (const g of [...Object.keys(mine.counts), 'Other', 'Nonexistent']) {
        expect(mine.color(g)).toBe(theirs.color(g));
      }
    });
  }

  it('cycles the palette instead of leaving a genre gray', () => {
    const nodes = Array.from({ length: 20 }, (_, i) => ({
      key: `a${i}`,
      label: 'A',
      kind: 'artist',
      cluster: `G${String(i).padStart(2, '0')}`,
    }));
    const { color } = webGenreColorMap(nodes);
    const used = nodes.map((n) => color(n.cluster as string));
    expect(used.every((c) => c !== WEB_GENRE_FALLBACK)).toBe(true);
    expect(new Set(used).size).toBe(16); //  16 colours across 20 genres
  });
});

describe('webTopArtists', () => {
  const cases: [string, WebRawNode[], number][] = [
    ['none', [], 20],
    [
      'zero-popularity artists are excluded outright',
      [
        { key: 'a', label: 'A', popularity: 0 },
        { key: 'b', label: 'B' },
        { key: 'c', label: 'C', popularity: 1 },
      ],
      20,
    ],
    [
      'top N by popularity',
      Array.from({ length: 30 }, (_, i) => ({ key: `a${i}`, label: 'A', popularity: i })),
      20,
    ],
    ['n larger than the list', [{ key: 'a', label: 'A', popularity: 5 }], 20],
    ['n of zero', [{ key: 'a', label: 'A', popularity: 5 }], 0],
  ];
  for (const [label, nodes, n] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      expect([...webTopArtists(nodes, n)].sort()).toEqual([...V._webTopArtists(nodes, n)].sort());
    });
  }

  it('gives NO stars to a library with no popularity data', () => {
    const nodes = Array.from({ length: 30 }, (_, i) => ({ key: `a${i}`, label: 'A' }));
    expect(webTopArtists(nodes, 20).size).toBe(0);
  });
});

// ── The label renderer ───────────────────────────────────────────────────────

describe('webDrawLabel', () => {
  function recorder() {
    const log: string[] = [];
    const ctx = new Proxy({} as Record<string, unknown>, {
      get(_t, p: string) {
        if (p === 'roundRect') return roundRect ? () => log.push('roundRect') : undefined;
        if (p === 'measureText') return () => ({ width: 42 });
        return (...a: unknown[]) => log.push(`${p}(${a.join(',')})`);
      },
      set(_t, p: string, v: unknown) {
        log.push(`${p}=${String(v)}`);
        return true;
      },
    });
    return { ctx: ctx as unknown as CanvasRenderingContext2D, log };
  }
  let roundRect = true;

  const cases: [string, unknown, unknown][] = [
    ['no label draws nothing', { x: 10, y: 20, size: 6 }, {}],
    ['a small node', { label: 'A', x: 10, y: 20, size: 4 }, {}],
    ['a big hub clamps the font at 18', { label: 'Rock', x: 10, y: 20, size: 40 }, {}],
    ['a tiny node clamps the font at 8', { label: 'x', x: 10, y: 20, size: 1 }, {}],
    ['no size falls back to 6', { label: 'x', x: 10, y: 20 }, {}],
    [
      'custom font settings',
      { label: 'x', x: 10, y: 20, size: 10 },
      { labelFont: 'Inter', labelWeight: 'bold' },
    ],
    ['fractional coords are rounded', { label: 'x', x: 10.6, y: 20.4, size: 10 }, {}],
  ];

  for (const [label, data, settings] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      roundRect = true;
      const a = recorder();
      V._webDrawLabel(a.ctx, data, settings);
      const b = recorder();
      webDrawLabel(
        b.ctx,
        data as { label?: string; x: number; y: number; size?: number },
        settings as { labelFont?: string },
      );
      expect(b.log).toEqual(a.log);
    });
  }

  it('falls back to a square box where roundRect is unavailable', () => {
    roundRect = false;
    const a = recorder();
    V._webDrawLabel(a.ctx, { label: 'x', x: 0, y: 0, size: 10 }, {});
    const b = recorder();
    webDrawLabel(b.ctx, { label: 'x', x: 0, y: 0, size: 10 }, {});
    expect(b.log).toEqual(a.log);
    expect(a.log.join('\n')).toContain('fillRect(');
    roundRect = true;
  });
});

// ── The three lens builders ──────────────────────────────────────────────────

const genrePayload: WebPayload = {
  nodes: [
    { key: 'g:rock', label: 'Rock', kind: 'genre', genre: 'Rock' },
    { key: 'g:jazz', label: 'Jazz', kind: 'genre', genre: 'Jazz' },
    {
      key: 'a1',
      label: 'Aphex Twin',
      kind: 'artist',
      cluster: 'Rock',
      primary_genre: 'idm',
      popularity: 90,
      id: 11,
      source: 'plex',
      thumb: '/t1',
    },
    { key: 'a2', label: 'Boards', kind: 'artist', cluster: 'Rock', popularity: 40, id: 12 },
    { key: 'a3', label: 'Coltrane', kind: 'artist', cluster: 'Jazz', popularity: 0 },
    { key: 'a4', label: 'Dud', kind: 'artist', cluster: 'Other' },
  ],
  edges: [
    { source: 'a1', target: 'a2', weight: 5, kind: 'similarity' },
    { source: 'a1', target: 'a3', weight: 1, kind: 'similarity' },
    { source: 'g:rock', target: 'a1', weight: 1, kind: 'membership' },
    { source: 'a1', target: 'a2', weight: 9, kind: 'similarity' }, //  duplicate, skipped
    { source: 'a1', target: 'ghost', weight: 3, kind: 'similarity' }, // unplaced, skipped
  ],
  counts: { artists: 4, genres: 2 },
};

describe('artWebBuildGenre', () => {
  it('builds an identical graph', () => {
    rewind();
    const theirs = V._artWebBuildGenre(genrePayload, FakeGraph);
    const theirIndex = [...V._artistWeb.index];
    sync();
    rewind();
    const mine = artWebBuildGenre(genrePayload, FakeGraph as unknown as WebGraphCtor);
    expect(snapshot(mine.graph)).toEqual(snapshot(theirs.graph));
    expect(mine.stats).toBe(theirs.stats);
    expect(mine.counts).toEqual(theirs.counts);
    expect(artistWeb.index).toEqual(theirIndex);
  });

  it('never renders membership edges — only sizes them for layout', () => {
    rewind();
    const built = artWebBuildGenre(genrePayload, FakeGraph as unknown as WebGraphCtor);
    const membership = [...(built.graph as FakeGraph).edges.values()].find(
      (e) => e.attrs.kind === 'membership',
    );
    expect(membership?.attrs.size).toBe(0.35);
    // …and the reducer hides it outright.
    expect(artWebEdgeReducer('e', membership!.attrs).hidden).toBe(true);
  });

  it('indexes ARTISTS only, so a genre hub is not searchable', () => {
    sync();
    rewind();
    artWebBuildGenre(genrePayload, FakeGraph as unknown as WebGraphCtor);
    expect(artistWeb.index.map((i) => i.key)).toEqual(['a1', 'a2', 'a3', 'a4']);
  });

  it('square-roots a non-star artist’s popularity', () => {
    // Every artist in the main fixture is a star (there are fewer than 20 with
    // popularity), and a star takes the flat WEB_STAR_SIZE — so the size formula
    // itself is only observable past the star cap.
    const many: WebPayload = {
      nodes: Array.from({ length: 25 }, (_, i) => ({
        key: `a${i}`,
        label: `A${i}`,
        kind: 'artist',
        cluster: 'Rock',
        popularity: 100 - i * 4,
      })),
      edges: [],
    };
    sync();
    rewind();
    const g = artWebBuildGenre(many, FakeGraph as unknown as WebGraphCtor).graph;
    // a24 has popularity 4 and is outside the top 20.
    expect(g.getNodeAttribute('a24', 'isStar')).toBe(false);
    expect(g.getNodeAttribute('a24', 'size')).toBeCloseTo(2 + Math.sqrt(4) / 3, 9);
  });

  it('reports the SERVER counts when it has them, and falls back to the node tally', () => {
    rewind();
    // The link count is taken from the RAW payload, so it includes the duplicate
    // and the edge to an unplaced node — four, where only three edges were
    // actually added to the graph. Transcribed as-is: the number describes what
    // the server sent, not what got drawn.
    expect(artWebBuildGenre(genrePayload, FakeGraph as unknown as WebGraphCtor).stats).toBe(
      '4 artists · 2 genres · 4 similarity links',
    );
    sync();
    rewind();
    expect(
      artWebBuildGenre({ ...genrePayload, counts: {} }, FakeGraph as unknown as WebGraphCtor).stats,
    ).toBe('6 artists · ? genres · 4 similarity links');
  });
});

describe('artWebBuildCommunity', () => {
  const louvain = (g: WebGraph) => {
    const out: Record<string, number> = {};
    (g as FakeGraph).nodes.forEach((_a, k) => {
      out[k] = k === 'a3' ? 1 : 0;
    });
    return out;
  };

  it('builds an identical graph', () => {
    rewind();
    // The vanilla builder reaches for the CDN global directly; the port takes it
    // as an argument, so both get the same function.
    (window as unknown as { graphologyLibrary: unknown }).graphologyLibrary = {
      communitiesLouvain: louvain,
    };
    const theirs = V._artWebBuildCommunity(genrePayload, FakeGraph);
    const theirIndex = [...V._artistWeb.index];
    sync();
    rewind();
    const mine = artWebBuildCommunity(
      genrePayload,
      FakeGraph as unknown as WebGraphCtor,
      louvain as unknown as (g: WebGraph, o: unknown) => Record<string, number>,
    );
    expect(snapshot(mine.graph)).toEqual(snapshot(theirs.graph));
    expect(mine.stats).toBe(theirs.stats);
    expect(mine.counts).toEqual(theirs.counts);
    expect(artistWeb.index).toEqual(theirIndex);
  });

  it('ignores a non-similarity edge even between two artists', () => {
    // Guards the `kind === 'similarity'` filter itself. Today the API only emits
    // 'similarity' and 'membership' (and membership always touches a hub, which
    // is filtered out anyway), so this is what makes the filter observable.
    const odd: WebPayload = {
      nodes: [
        { key: 'p', label: 'P', kind: 'artist', popularity: 5 },
        { key: 'q', label: 'Q', kind: 'artist', popularity: 4 },
      ],
      edges: [{ source: 'p', target: 'q', weight: 1, kind: 'coincidence' }],
    };
    sync();
    rewind();
    const built = artWebBuildCommunity(odd, FakeGraph as unknown as WebGraphCtor, null);
    expect(built.graph.order).toBe(0);
  });

  it('keeps ONLY artists with a similarity link — the discoverable core', () => {
    sync();
    rewind();
    const built = artWebBuildCommunity(
      genrePayload,
      FakeGraph as unknown as WebGraphCtor,
      louvain as unknown as (g: WebGraph, o: unknown) => Record<string, number>,
    );
    // a4 has no similarity edge and no genre hub is included at all.
    expect([...(built.graph as FakeGraph).nodes.keys()].sort()).toEqual(['a1', 'a2', 'a3']);
  });

  it('names each community after its highest-degree member', () => {
    sync();
    rewind();
    const built = artWebBuildCommunity(
      genrePayload,
      FakeGraph as unknown as WebGraphCtor,
      louvain as unknown as (g: WebGraph, o: unknown) => Record<string, number>,
    );
    // a1 has two links; a3 is alone in its community and names it itself.
    expect(Object.keys(built.counts).sort()).toEqual(['Aphex Twin', 'Coltrane']);
  });

  it('falls back to one community when louvain is unavailable', () => {
    sync();
    rewind();
    const built = artWebBuildCommunity(genrePayload, FakeGraph as unknown as WebGraphCtor, null);
    expect(Object.keys(built.counts)).toHaveLength(1);
  });

  it('assigns palette colours by community SIZE, not by insertion order', () => {
    const payload: WebPayload = {
      nodes: [
        { key: 'small', label: 'Small', kind: 'artist', popularity: 9 },
        { key: 'smallB', label: 'SmallB', kind: 'artist', popularity: 8 },
        { key: 'big1', label: 'Big1', kind: 'artist', popularity: 7 },
        { key: 'big2', label: 'Big2', kind: 'artist', popularity: 6 },
        { key: 'big3', label: 'Big3', kind: 'artist', popularity: 5 },
        { key: 'big4', label: 'Big4', kind: 'artist', popularity: 4 },
      ],
      edges: [
        { source: 'small', target: 'smallB', weight: 1, kind: 'similarity' },
        { source: 'big1', target: 'big2', weight: 1, kind: 'similarity' },
        { source: 'big2', target: 'big3', weight: 1, kind: 'similarity' },
        { source: 'big3', target: 'big4', weight: 1, kind: 'similarity' },
      ],
    };
    // The SMALL community is inserted first but is the smaller of the two, so
    // dropping the size sort swaps which one takes palette entry 0.
    const split = (g: WebGraph) => {
      const out: Record<string, number> = {};
      (g as FakeGraph).nodes.forEach((_a, k) => {
        out[k] = k.startsWith('small') ? 0 : 1;
      });
      return out;
    };
    sync();
    rewind();
    const built = artWebBuildCommunity(
      payload,
      FakeGraph as unknown as WebGraphCtor,
      split as unknown as (g: WebGraph, o: unknown) => Record<string, number>,
    );
    const big = built.graph.getNodeAttribute('big1', 'baseColor');
    const small = built.graph.getNodeAttribute('small', 'baseColor');
    expect(big).toBe(WEB_PALETTE[0]);
    expect(small).toBe(WEB_PALETTE[1]);
  });

  it('guards a repeated representative name with the community id', () => {
    sync();
    rewind();
    const twins: WebPayload = {
      nodes: [
        { key: 'x1', label: 'Same', kind: 'artist', popularity: 5 },
        { key: 'x2', label: 'Same', kind: 'artist', popularity: 4 },
        { key: 'y1', label: 'Other', kind: 'artist', popularity: 3 },
        { key: 'y2', label: 'Other2', kind: 'artist', popularity: 2 },
      ],
      edges: [
        { source: 'x1', target: 'y1', weight: 1, kind: 'similarity' },
        { source: 'x2', target: 'y2', weight: 1, kind: 'similarity' },
      ],
    };
    const split = (g: WebGraph) => {
      const out: Record<string, number> = {};
      (g as FakeGraph).nodes.forEach((_a, k) => {
        out[k] = k.startsWith('x1') || k.startsWith('y1') ? 0 : 1;
      });
      return out;
    };
    const built = artWebBuildCommunity(
      twins,
      FakeGraph as unknown as WebGraphCtor,
      split as unknown as (g: WebGraph, o: unknown) => Record<string, number>,
    );
    // Both communities would be led by a "Same"-labelled artist; the second gets
    // its id appended so the counts map cannot collapse.
    expect(Object.keys(built.counts)).toHaveLength(2);
  });
});

describe('artWebBuildDiscovery', () => {
  const payload: WebPayload = {
    nodes: [
      { key: 'o1', label: 'Owned One', kind: 'owned', id: 3, thumb: '/t' },
      { key: 'o2', label: 'Owned Two', kind: 'owned', id: 4 },
      {
        key: 'd1',
        label: 'Cand A',
        kind: 'discovery',
        image_url: '/i',
        genres: ['idm'],
        ids: [['spotify', 'sp1']],
        popularity: 60,
      },
      { key: 'd2', label: 'Cand B', kind: 'discovery' },
    ],
    edges: [
      { source: 'o1', target: 'd1', weight: 4 },
      { source: 'o1', target: 'd2', weight: 1 },
      { source: 'o2', target: 'd1', weight: 2 },
    ],
    counts: { owned: 2, discovery: 2 },
  };

  it('builds an identical graph', () => {
    rewind();
    const theirs = V._artWebBuildDiscovery(payload, FakeGraph);
    const theirIndex = [...V._artistWeb.index];
    sync();
    rewind();
    const mine = artWebBuildDiscovery(payload, FakeGraph as unknown as WebGraphCtor);
    expect(snapshot(mine.graph)).toEqual(snapshot(theirs.graph));
    expect(mine.stats).toBe(theirs.stats);
    expect(artistWeb.index).toEqual(theirIndex);
  });

  it('sizes an anchor by its frontier and a candidate by its BEST link', () => {
    sync();
    rewind();
    const g = artWebBuildDiscovery(payload, FakeGraph as unknown as WebGraphCtor).graph;
    // o1 anchors two candidates, o2 only one.
    expect(g.getNodeAttribute('o1', 'size')).toBeGreaterThan(
      g.getNodeAttribute('o2', 'size') as number,
    );
    // d1's strongest link is 4, d2's is 1.
    expect(g.getNodeAttribute('d1', 'size')).toBeGreaterThan(
      g.getNodeAttribute('d2', 'size') as number,
    );
  });

  it('labels only the 25 biggest anchors', () => {
    const wide: WebPayload = {
      nodes: [
        ...Array.from({ length: 30 }, (_, i) => ({
          key: `o${i}`,
          label: `O${i}`,
          kind: 'owned',
          id: i,
        })),
        { key: 'd', label: 'D', kind: 'discovery' },
      ],
      // Anchor i gets i+1 candidates' worth of degree, so the ranking is total.
      edges: Array.from({ length: 30 }, (_, i) =>
        Array.from({ length: i + 1 }, () => ({ source: `o${i}`, target: 'd', weight: 1 })),
      ).flat(),
    };
    sync();
    rewind();
    const g = artWebBuildDiscovery(wide, FakeGraph as unknown as WebGraphCtor).graph;
    let labelled = 0;
    g.forEachNode((_k, a) => {
      if (a.kind === 'owned' && a.forceLabel) labelled++;
    });
    // A LITERAL 25 — asserting against the constant moves with the mutation and
    // the cap could be halved unnoticed.
    expect(labelled).toBe(25);
    expect(WEB_STAR_ANCHORS).toBe(25);
  });

  it('indexes owned AND unowned, unlike the genre lens', () => {
    sync();
    rewind();
    artWebBuildDiscovery(payload, FakeGraph as unknown as WebGraphCtor);
    expect(artistWeb.index.map((i) => i.key)).toEqual(['o1', 'o2', 'd1', 'd2']);
  });

  it('reports zeros rather than blanks with no counts', () => {
    sync();
    rewind();
    expect(
      artWebBuildDiscovery({ nodes: [], edges: [] }, FakeGraph as unknown as WebGraphCtor).stats,
    ).toBe('0 of your artists · 0 to discover');
  });
});

// ── Layout bookkeeping ───────────────────────────────────────────────────────

describe('artWebFinishLayout', () => {
  function positioned(coords: [string, number, number][]) {
    const g = new FakeGraph();
    coords.forEach(([k, x, y]) => g.addNode(k, { x, y }));
    return g;
  }

  const cases: [string, [string, number, number][]][] = [
    [
      'a spread',
      [
        ['a', -10, -5],
        ['b', 30, 25],
        ['c', 0, 0],
      ],
    ],
    ['a single node — the span guard', [['a', 5, 5]]],
    [
      'a horizontal line',
      [
        ['a', 0, 0],
        ['b', 100, 0],
      ],
    ],
    [
      'a vertical line',
      [
        ['a', 0, 0],
        ['b', 0, 100],
      ],
    ],
    [
      'negative coordinates only',
      [
        ['a', -100, -100],
        ['b', -50, -50],
      ],
    ],
  ];

  for (const [label, coords] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      V._artWebFinishLayout(positioned(coords));
      const theirs = {
        home: V._artistWeb.home,
        push: V._artistWeb.spreadPush,
      };
      artWebFinishLayout(positioned(coords));
      expect({ home: artistWeb.home, push: artistWeb.spreadPush }).toEqual(theirs);
    });
  }

  it('derives the spread distance from the settled span, not a constant', () => {
    artWebFinishLayout(
      positioned([
        ['a', 0, 0],
        ['b', 200, 0],
      ]),
    );
    expect(artistWeb.spreadPush).toBeCloseTo(7, 6);
  });

  it('falls back to a span of 1 for a single node', () => {
    artWebFinishLayout(positioned([['a', 5, 5]]));
    expect(artistWeb.spreadPush).toBeCloseTo(0.035, 9);
  });
});

describe('artWebEdgeThreshold', () => {
  function weighted(weights: number[], kind = 'similarity') {
    const g = new FakeGraph();
    weights.forEach((w, i) => {
      g.addNode(`n${i}`, {});
      g.addNode(`m${i}`, {});
      g.addEdge(`n${i}`, `m${i}`, { weight: w, kind });
    });
    return g;
  }

  it('defaults to 2 with no similarity edges at all', () => {
    expect(artWebEdgeThreshold(new FakeGraph())).toBe(2);
    expect(artWebEdgeThreshold(weighted([1, 1], 'membership'))).toBe(2);
  });

  it('bumps above the minimum when the median IS the minimum', () => {
    // Most edges are weight-1, so a plain median would hide nothing.
    expect(artWebEdgeThreshold(weighted([1, 1, 1, 1, 5]))).toBe(2);
  });

  it('uses the median when it is above the minimum', () => {
    expect(artWebEdgeThreshold(weighted([1, 2, 5, 8, 9]))).toBe(5);
  });

  it('ignores membership weights when computing the median', () => {
    const g = new FakeGraph();
    const add = (i: number, w: number, kind: string) => {
      g.addNode(`n${i}`, {});
      g.addNode(`m${i}`, {});
      g.addEdge(`n${i}`, `m${i}`, { weight: w, kind });
    };
    add(0, 5, 'similarity');
    add(1, 8, 'similarity');
    add(2, 1, 'membership');
    add(3, 1, 'membership');
    // Similarity-only: [5,8] → median index 1 → 8. Including membership:
    // [1,1,5,8] → median index 2 → 5. The two disagree.
    expect(artWebEdgeThreshold(g)).toBe(8);
  });

  it('treats a missing weight as 1', () => {
    const g = new FakeGraph();
    g.addNode('a', {});
    g.addNode('b', {});
    g.addEdge('a', 'b', { kind: 'similarity' });
    expect(artWebEdgeThreshold(g)).toBe(2);
  });
});

// ── Node sizing ──────────────────────────────────────────────────────────────

describe('artWebApplySize', () => {
  function built() {
    const g = new FakeGraph();
    g.addNode('a', { kind: 'artist', popularity: 100, isStar: true, size: 8 });
    g.addNode('b', { kind: 'artist', popularity: 25, size: 3 });
    g.addNode('c', { kind: 'artist', popularity: 0, size: 2 });
    g.addNode('hub', { kind: 'genre', size: 12 });
    return g;
  }
  function sim() {
    const g = new FakeGraph();
    g.addNode('a', {});
    g.addNode('b', {});
    g.addEdge('a', 'b', {});
    return g;
  }

  for (const mode of ['popularity', 'connections', 'influence'] as const) {
    it(`matches the vanilla for ${mode}`, () => {
      const theirGraph = built();
      const theirSim = sim();
      const btw = { a: 0.9, b: 0.1 };
      V._artistWeb.graph = theirGraph;
      V._stubs.sim = theirSim;
      V._stubs.btw = btw;
      V._artWebApplySize(mode);

      const mineGraph = built();
      artWebApplySize(mineGraph, mode, sim(), btw);
      expect(snapshot(mineGraph)).toEqual(snapshot(theirGraph));
    });
  }

  it('excludes genre hubs from the normalisation maximum', () => {
    // A hub outranking every artist would shrink all of them toward the floor.
    const g = new FakeGraph();
    g.addNode('a', { kind: 'artist', popularity: 100, size: 3 });
    g.addNode('hub', { kind: 'genre', popularity: 10000, size: 12 });
    artWebApplySize(g, 'popularity', null, null);
    expect(g.getNodeAttribute('a', 'size')).toBeCloseTo(5.5, 9); //  the top of the range
  });

  it('leaves genre hubs sized by member count', () => {
    const g = built();
    artWebApplySize(g, 'popularity', null, null);
    expect(g.getNodeAttribute('hub', 'size')).toBe(12);
  });

  it('never shrinks a star below 6', () => {
    const g = built();
    artWebApplySize(g, 'influence', null, {});
    expect(g.getNodeAttribute('a', 'size')).toBe(6);
    expect(g.getNodeAttribute('b', 'size')).toBe(2);
  });
});

// ── Pathfinding ──────────────────────────────────────────────────────────────

describe('the similarity graph + paths', () => {
  const data: WebPayload = {
    edges: [
      { source: 'b', target: 'a', weight: 3, kind: 'similarity' },
      { source: 'b', target: 'c', weight: 1, kind: 'similarity' },
      { source: 'x', target: 'y', weight: 1, kind: 'membership' },
      { source: 'b', target: 'a', weight: 9, kind: 'similarity' },
    ],
  };

  it('is UNDIRECTED and holds similarity edges only', () => {
    sync();
    const g = artWebSimGraph(data, FakeGraph as unknown as WebGraphCtor) as FakeGraph;
    expect([...g.nodes.keys()].sort()).toEqual(['a', 'b', 'c']);
    expect(g.size).toBe(2); //  the duplicate is skipped
    // Pairs are stored once, sorted — a directed graph would report "no
    // connection" for most pairs.
    expect(g.hasEdge('a', 'b')).toBe(true);
    expect(g.undirected).toBe(true);
  });

  it('caches, so the second call is the same object', () => {
    sync();
    const first = artWebSimGraph(data, FakeGraph as unknown as WebGraphCtor);
    expect(artWebSimGraph(data, FakeGraph as unknown as WebGraphCtor)).toBe(first);
  });

  it('returns null without data or a constructor', () => {
    sync();
    expect(artWebSimGraph(null, FakeGraph as unknown as WebGraphCtor)).toBeNull();
    sync();
    expect(artWebSimGraph(data, null)).toBeNull();
  });

  it('keys path pairs order-independently', () => {
    expect([...artWebPathPairs(['b', 'a', 'c'])]).toEqual(['a|b', 'a|c']);
    expect(artWebPathPairs(['a'])).toEqual(new Set());
  });

  it('summarises a path the way the panel reads it', () => {
    expect(artWebPathSummary(['a', 'b'])).toEqual({
      hops: 1,
      between: 0,
      via: 'directly similar',
    });
    expect(artWebPathSummary(['a', 'b', 'c'])).toEqual({
      hops: 2,
      between: 1,
      via: 'via 1 artist in between',
    });
    expect(artWebPathSummary(['a', 'b', 'c', 'd']).via).toBe('via 2 artists in between');
  });
});

// ── The reducers ─────────────────────────────────────────────────────────────

describe('artWebNodeReducer', () => {
  const data = {
    label: 'A',
    color: '#123456',
    baseColor: '#1db954',
    genre: 'Rock',
    size: 4,
  };

  const cases: [string, Partial<ArtistWebState>, string][] = [
    ['at rest, the data passes straight through', {}, 'n1'],
    ['a search hit', { searchMatch: new Set(['n1']) }, 'n1'],
    ['a search miss', { searchMatch: new Set(['other']) }, 'n1'],
    ['a focus root', { focusSet: new Set(['n1', 'n2']), focusRoot: 'n1' }, 'n1'],
    [
      'a focus neighbour is NOT labelled',
      { focusSet: new Set(['n1', 'n2']), focusRoot: 'n2' },
      'n1',
    ],
    ['outside the focus', { focusSet: new Set(['n2']), focusRoot: 'n2' }, 'n1'],
    [
      'focus beats search',
      { focusSet: new Set(['n2']), focusRoot: 'n2', searchMatch: new Set(['n1']) },
      'n1',
    ],
    ['inside the genre filter', { genreFilter: new Set(['Rock']) }, 'n1'],
    ['outside the genre filter', { genreFilter: new Set(['Jazz']) }, 'n1'],
    [
      'the genre filter beats a focus',
      { genreFilter: new Set(['Jazz']), focusSet: new Set(['n1']), focusRoot: 'n1' },
      'n1',
    ],
    ['a path endpoint', { pathNodes: new Set(['n1', 'n2']), pathSource: 'n1' }, 'n1'],
    [
      'a path middle before completion is unlabelled',
      { pathNodes: new Set(['n1', 'n2']), pathSource: 'n2' },
      'n1',
    ],
    [
      'a path middle AFTER completion is labelled',
      { pathNodes: new Set(['n1', 'n2']), pathSource: 'n2', pathResult: ['n2', 'n1'] },
      'n1',
    ],
    // Both ends are labelled, not just the one you clicked first.
    [
      'a path TARGET before completion',
      { pathNodes: new Set(['n1', 'n2']), pathSource: 'n2', pathTarget: 'n1' },
      'n1',
    ],
    ['off the path entirely', { pathNodes: new Set(['n2']) }, 'n1'],
    [
      'path mode beats everything else',
      { pathNodes: new Set(['n2']), genreFilter: new Set(['Rock']), focusSet: new Set(['n1']) },
      'n1',
    ],
  ];

  for (const [label, state, node] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      sync(state);
      expect(artWebNodeReducer(node, { ...data })).toEqual(V._artWebNodeReducer(node, { ...data }));
    });
  }

  it('returns the SAME object at rest, with no clone', () => {
    sync();
    const d = { ...data };
    expect(artWebNodeReducer('n1', d)).toBe(d);
  });

  it('clones once anything is active', () => {
    sync({ searchMatch: new Set(['n1']) });
    const d = { ...data };
    expect(artWebNodeReducer('n1', d)).not.toBe(d);
  });

  it('falls back to `color` when a node has no baseColor', () => {
    sync({ searchMatch: new Set(['n1']) });
    const out = artWebNodeReducer('n1', { color: '#abcdef', genre: 'Rock' });
    expect(out.color).toBe('#abcdef');
  });
});

describe('artWebEdgeReducer', () => {
  function graph() {
    const g = new FakeGraph();
    g.addNode('n1', { genre: 'Rock' });
    g.addNode('n2', { genre: 'Rock' });
    g.addNode('n3', { genre: 'Jazz' });
    g.addEdge('n1', 'n2', {});
    g.addEdge('n1', 'n3', {});
    return g;
  }
  const data = { kind: 'similarity', weight: 1, size: 0.7, baseColor: '#1db954' };

  const cases: [string, Partial<ArtistWebState>, string, Record<string, unknown>][] = [
    ['membership is always hidden', {}, 'n1|n2', { ...data, kind: 'membership' }],
    ['at rest it passes straight through', {}, 'n1|n2', { ...data }],
    [
      'declutter hides a weak similarity edge',
      { edgeDeclutter: true, edgeThreshold: 2 },
      'n1|n2',
      { ...data },
    ],
    [
      'declutter keeps one at the threshold',
      { edgeDeclutter: true, edgeThreshold: 2 },
      'n1|n2',
      { ...data, weight: 2 },
    ],
    [
      'declutter spares a DISCOVERY edge',
      { edgeDeclutter: true, edgeThreshold: 2 },
      'n1|n2',
      { ...data, kind: 'discovery' },
    ],
    [
      'declutter yields to an active focus',
      { edgeDeclutter: true, edgeThreshold: 2, focusSet: new Set(['n1', 'n2']) },
      'n1|n2',
      { ...data },
    ],
    [
      'a focus edge fully inside the set',
      { focusSet: new Set(['n1', 'n2']) },
      'n1|n2',
      { ...data },
    ],
    ['a focus edge only half inside', { focusSet: new Set(['n1']) }, 'n1|n2', { ...data }],
    ['a search edge merely TOUCHING a hit', { searchMatch: new Set(['n1']) }, 'n1|n2', { ...data }],
    ['a search edge touching nothing', { searchMatch: new Set(['n9']) }, 'n1|n2', { ...data }],
    ['both ends inside the genre filter', { genreFilter: new Set(['Rock']) }, 'n1|n2', { ...data }],
    ['one end outside the genre filter', { genreFilter: new Set(['Rock']) }, 'n1|n3', { ...data }],
    [
      'a path edge',
      { pathNodes: new Set(['n1', 'n2']), pathPairs: new Set(['n1|n2']) },
      'n1|n2',
      { ...data },
    ],
    [
      'an edge off the path',
      { pathNodes: new Set(['n1', 'n2']), pathPairs: new Set(['n1|n2']) },
      'n1|n3',
      { ...data },
    ],
    [
      'path mode with no pairs yet hides everything',
      { pathNodes: new Set(['n1']) },
      'n1|n2',
      { ...data },
    ],
  ];

  for (const [label, state, edge, d] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const g = graph();
      sync({ ...state, graph: g });
      V._artistWeb.graph = g;
      expect(artWebEdgeReducer(edge, { ...d }, g)).toEqual(V._artWebEdgeReducer(edge, { ...d }));
    });
  }

  it('returns the SAME object at rest — the biggest allocation sink', () => {
    sync();
    const d = { ...data };
    expect(artWebEdgeReducer('n1|n2', d, graph())).toBe(d);
  });
});

// ── The spread effect ────────────────────────────────────────────────────────

describe('the spread effect', () => {
  function laid() {
    const g = new FakeGraph();
    g.addNode('root', { x: 0, y: 0 });
    g.addNode('a', { x: 10, y: 0 });
    g.addNode('b', { x: 0, y: 10 });
    return g;
  }
  const home = { root: { x: 0, y: 0 }, a: { x: 10, y: 0 }, b: { x: 0, y: 10 } };

  it('refuses to spread without a captured layout', () => {
    sync({ home: null });
    expect(artWebSetSpread('root', new Set(['root', 'a']))).toBe(false);
  });

  it('refuses to spread with the effect switched off', () => {
    sync({ home, cursorFX: false });
    expect(artWebSetSpread('root', new Set(['root', 'a']))).toBe(false);
  });

  it('clears rather than spreading a node with no neighbours', () => {
    sync({ home, spreadRoot: 'x', spreadSet: new Set(['y']) });
    expect(artWebSetSpread('root', new Set(['root']))).toBe(false);
    expect(artistWeb.spreadRoot).toBeNull();
    expect(artistWeb.spreadSet).toBeNull();
  });

  it('excludes the root from its own spread set', () => {
    sync({ home });
    artWebSetSpread('root', new Set(['root', 'a', 'b']));
    expect([...(artistWeb.spreadSet as Set<string>)].sort()).toEqual(['a', 'b']);
  });

  it('matches the vanilla frame for frame', () => {
    const mineGraph = laid();
    const theirGraph = laid();
    const state = {
      home,
      spreadPush: 5,
      spreadRoot: 'root',
      spreadSet: new Set(['a', 'b']),
      spreadActive: null,
    };
    sync(state);
    V._artistWeb.graph = theirGraph;
    V._artistWeb.sigma = { refresh() {} };
    (artistWeb as ArtistWebState).graph = mineGraph;

    for (let frame = 0; frame < 12; frame++) {
      V._artWebSpreadTick();
      artWebSpreadTick(mineGraph);
      expect(snapshot(mineGraph)).toEqual(snapshot(theirGraph));
      // The vanilla nulls fxRAF at the top of every tick; re-arm both.
      V._artistWeb.fxRAF = null;
    }
  });

  it('eases pushed nodes outward and settles them back home', () => {
    const g = laid();
    sync({ home, spreadPush: 5, spreadRoot: 'root', spreadSet: new Set(['a']) });
    for (let i = 0; i < 60; i++) artWebSpreadTick(g);
    // A PUSHED node only ever eases toward its target — the exact snap is
    // reserved for nodes coming home, so this asymptotes just short of 15.
    expect(g.getNodeAttribute('a', 'x')).toBeCloseTo(15, 2);
    expect(g.getNodeAttribute('a', 'x')).not.toBe(15);

    sync({ home, spreadPush: 5, spreadRoot: null, spreadSet: null, spreadActive: new Set(['a']) });
    (g as FakeGraph).setNodeAttribute('a', 'x', 15);
    let moving = true;
    for (let i = 0; i < 200 && moving; i++) moving = artWebSpreadTick(g);
    expect(g.getNodeAttribute('a', 'x')).toBe(10); //  snapped exactly home
    expect(artistWeb.spreadActive?.has('a')).toBe(false);
  });

  it('survives a neighbour sitting exactly on the root', () => {
    // dx/dy are both 0. With the guard the target is the node's own home, so it
    // does not move; WITHOUT it the target is NaN, the movement check fails, and
    // it also does not move — the `|| 0.0001` is defensive rather than
    // load-bearing, and a mutation removing it is EQUIVALENT (recorded, not
    // papered over). This still pins that the position stays finite either way.
    const g = new FakeGraph();
    g.addNode('root', { x: 0, y: 0 });
    g.addNode('twin', { x: 0, y: 0 });
    sync({
      home: { root: { x: 0, y: 0 }, twin: { x: 0, y: 0 } },
      spreadPush: 5,
      spreadRoot: 'root',
      spreadSet: new Set(['twin']),
    });
    artWebSpreadTick(g);
    expect(Number.isNaN(g.getNodeAttribute('twin', 'x') as number)).toBe(false);
    expect(Number.isNaN(g.getNodeAttribute('twin', 'y') as number)).toBe(false);
  });

  it('reports "not moving" once everything has settled', () => {
    const g = laid();
    sync({ home, spreadPush: 5, spreadRoot: null, spreadSet: null });
    expect(artWebSpreadTick(g)).toBe(false);
  });

  it('drops a node the layout has no home for', () => {
    const g = laid();
    sync({ home: { a: { x: 10, y: 0 } }, spreadActive: new Set(['a', 'ghost']) });
    artWebSpreadTick(g);
    expect(artistWeb.spreadActive?.has('ghost')).toBe(false);
  });
});
