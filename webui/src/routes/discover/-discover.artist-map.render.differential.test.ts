import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { loadVanilla } from '../../test/vanilla-extract';
import {
  type ArtMapIsland,
  type ArtMapNode,
  type ArtMapRipple,
  type ArtMapState,
  artMap,
} from './-discover.artist-map';
import {
  artMapBeginReveal,
  artMapBloomIsland,
  artMapCompositeNode,
  artMapDraw,
  artMapDrawHoverPop,
  artMapDrawLiveLayer,
  artMapDrawLiveNode,
  artMapDrawNodeToBuffer,
  artMapDrawPerf,
  artMapDrawRipples,
  artMapEmitRipple,
  artMapGlossSprite,
  artMapHaloSprite,
  artMapPerfReport,
  artMapRebuildBuffer,
} from './-discover.artist-map.render';

/**
 * Differential parity for the Artist Map's canvas painters.
 *
 * Canvas code returns nothing to compare, so both sides are run against a
 * RECORDING 2D context that logs every call AND every property assignment in
 * order. Two identical logs mean the two painters issue byte-identical drawing
 * instructions — a stronger claim than "the numbers look right", and the only
 * way to catch a reordered fill/stroke or a dropped globalAlpha reset.
 *
 * `document.createElement('canvas')` is stubbed for both sides (jsdom has no 2D
 * context), handing out recorders and labelling each canvas by creation order —
 * so the sequence in which sprites and buffers get created is compared too.
 */

// ── The recording context ────────────────────────────────────────────────────

interface FakeCanvas {
  __label: string;
  __log: string[];
  width: number;
  height: number;
  getContext: () => unknown;
}

let canvasSeq = 0;
let gradientSeq = 0;

function fmt(v: unknown): string {
  if (v === null) return 'null';
  if (v === undefined) return 'undefined';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(6);
  if (typeof v === 'string') return JSON.stringify(v);
  if (typeof v === 'object') {
    const o = v as { __label?: string; __gradient?: string };
    if (o.__gradient) return o.__gradient;
    if (o.__label) return o.__label;
    return '[obj]';
  }
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  return typeof v; //  functions/symbols never reach a canvas call in this code
}

function makeGradient(log: string[]) {
  const label = `gradient#${++gradientSeq}`;
  return {
    __gradient: label,
    addColorStop(stop: number, color: string) {
      log.push(`${label}.addColorStop(${fmt(stop)},${fmt(color)})`);
    },
  };
}

function makeRecorder(): { ctx: CanvasRenderingContext2D; log: string[] } {
  const log: string[] = [];
  const cache: Record<string, unknown> = {};
  const ctx = new Proxy({} as Record<string, unknown>, {
    get(_t, prop: string) {
      if (prop === '__log') return log;
      if (prop === 'then') return undefined; //  never mistake this for a thenable
      if (!cache[prop]) {
        cache[prop] = (...args: unknown[]) => {
          log.push(`${prop}(${args.map(fmt).join(',')})`);
          if (prop === 'createRadialGradient' || prop === 'createLinearGradient') {
            return makeGradient(log);
          }
          if (prop === 'measureText') return { width: 42 };
          return undefined;
        };
      }
      return cache[prop];
    },
    set(_t, prop: string, value: unknown) {
      log.push(`${prop}=${fmt(value)}`);
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
  return { ctx, log };
}

const realCreateElement = document.createElement.bind(document);
const createdCanvases: FakeCanvas[] = [];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(document as any).createElement = (tag: string, ...rest: unknown[]) => {
  if (String(tag).toLowerCase() !== 'canvas') {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (realCreateElement as any)(tag, ...rest);
  }
  const rec = makeRecorder();
  const c: FakeCanvas = {
    __label: `canvas#${++canvasSeq}`,
    __log: rec.log,
    width: 0,
    height: 0,
    getContext: () => rec.ctx,
  };
  createdCanvases.push(c);
  return c;
};

afterAll(() => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (document as any).createElement = realCreateElement;
});

// ── The vanilla side ─────────────────────────────────────────────────────────

const PREAMBLE = `
const _artMap = {
  placed: [], edges: [], images: {},
  canvas: null, ctx: null, offscreen: null, offCtx: null,
  width: 0, height: 0, offsetX: 0, offsetY: 0, zoom: 0.15,
  hoveredNode: null, animFrame: null, dirty: true,
  WATCHLIST_R: 320, BUFFER: 8, MAX_BUFFER_PX: 4096, LIVE_PX: 12,
  _anim: { running: false, raf: null, last: 0 },
  _fieldAlpha: 1, _revealT0: 0, _panelW: 320,
};
function _artMapStartLoop() {}
function _artMapRender() {}
function _artMapEnsureAmbient() {}
`;

interface Vanilla {
  _artMap: ArtMapState;
  _artMapGlossSprite: () => FakeCanvas;
  _artMapHaloSprite: (hue: number) => FakeCanvas;
  _artMapRebuildBuffer: () => void;
  _artMapDrawNodeToBuffer: (ctx: unknown, n: unknown, scale: number) => void;
  _artMapCompositeNode: (n: unknown) => boolean;
  _artMapIsLiveSize: (n: unknown) => boolean;
  _artMapDrawLiveLayer: (ctx: unknown) => void;
  _artMapDrawHoverPop: (ctx: unknown, n: unknown) => void;
  _artMapDrawLiveNode: (ctx: unknown, n: unknown) => void;
  _artMapDrawRipples: (ctx: unknown) => void;
  _artMapNodeDisplacement: (n: unknown) => unknown;
  _artMapDraw: () => void;
  _artMapDrawPerf: (ctx: unknown, t0: number) => void;
  _artMapBeginReveal: () => void;
  _artMapBloomIsland: (isl: unknown) => void;
  _artMapEmitRipple: (wx: number, wy: number, hue?: number | null) => void;
  _artMapStepAnimations: (t: number) => boolean;
}

const V = loadVanilla<Vanilla>(
  [
    '_artMapGlossSprite',
    '_artMapHaloSprite',
    '_artMapRebuildBuffer',
    '_artMapDrawNodeToBuffer',
    '_artMapCompositeNode',
    '_artMapIsLiveSize',
    '_artMapDrawLiveLayer',
    '_artMapDrawHoverPop',
    '_artMapDrawLiveNode',
    '_artMapDrawRipples',
    '_artMapNodeDisplacement',
    '_artMapDraw',
    '_artMapDrawPerf',
    '_artMapBeginReveal',
    '_artMapBloomIsland',
    '_artMapEmitRipple',
    '_artMapStepAnimations',
  ],
  PREAMBLE,
  ['_artMap'],
);

// ── Harness ──────────────────────────────────────────────────────────────────

const BASE: Partial<ArtMapState> = {
  placed: [],
  edges: [],
  images: {},
  width: 1400,
  height: 800,
  offsetX: 700,
  offsetY: 400,
  zoom: 1,
  dirty: true,
  offscreen: null,
  hoveredNode: null,
  _islands: undefined,
  _nodeById: undefined,
  _ripples: undefined,
  _revealing: undefined,
  _hideSimilar: undefined,
  _liveOverflow: undefined,
  _liveBuildZoom: undefined,
  _drawAlphaMul: undefined,
  _now: 1000,
  _focusIdx: undefined,
  _oneIsland: undefined,
  _bufferScale: undefined,
  _bufferMinX: undefined,
  _bufferMinY: undefined,
  _fieldAlpha: 1,
  _gloss: undefined,
  _halos: undefined,
  _bgGrad: undefined,
  _bgW: undefined,
  _bgH: undefined,
  _constellationFade: undefined,
  _constellationCache: undefined,
  _constellationActive: undefined,
  _perf: undefined,
  _lastPerfTs: undefined,
  _perfPostTs: undefined,
  _rebuildMs: undefined,
  _anim: { running: false, raf: null, last: 0 },
};

/** Reset one side's singleton to a deep copy of `state`. */
function reset(target: ArtMapState, state: Partial<ArtMapState>) {
  const merged = { ...BASE, ...state };
  for (const k of Object.keys(merged)) {
    const v = (merged as Record<string, unknown>)[k];
    (target as Record<string, unknown>)[k] =
      Array.isArray(v) || (v && typeof v === 'object' && !(v instanceof Set) && !('__label' in v))
        ? structuredClone(v)
        : v;
  }
}

/**
 * Run the same scenario on both sides against fresh recorders and return the two
 * operation logs. The canvas counter is reset per side so `canvas#1` means "the
 * first canvas THIS side created" — making creation ORDER part of the diff.
 */
function bothLogs(
  state: Partial<ArtMapState>,
  vanilla: (ctx: CanvasRenderingContext2D) => void,
  mine: (ctx: CanvasRenderingContext2D) => void,
): [string[], string[]] {
  canvasSeq = 0;
  gradientSeq = 0;
  const a = makeRecorder();
  reset(V._artMap, state);
  V._artMap.ctx = a.ctx;
  V._artMap.canvas = { width: 2800, height: 1600 } as HTMLCanvasElement;
  vanilla(a.ctx);

  canvasSeq = 0;
  gradientSeq = 0;
  const b = makeRecorder();
  reset(artMap, state);
  artMap.ctx = b.ctx;
  artMap.canvas = { width: 2800, height: 1600 } as HTMLCanvasElement;
  mine(b.ctx);

  return [a.log, b.log];
}

const node = (over: Partial<ArtMapNode> = {}): ArtMapNode =>
  ({
    id: 1,
    name: 'Aphex Twin',
    x: 10,
    y: 20,
    radius: 70.4,
    opacity: 1,
    type: 'similar',
    image_url: '',
    genres: [],
    popularity: 50,
    _hue: 200,
    _island: 'Rock',
    ...over,
  }) as ArtMapNode;

beforeEach(() => {
  createdCanvases.length = 0;
  vi.spyOn(performance, 'now').mockReturnValue(5000);
  // The vanilla's `_artMapStartLoop` is a no-op in the preamble, but my port
  // calls the real one — which schedules a frame that fires AFTER the test, with
  // the fixture torn down, and throws inside jsdom's rAF queue. Neutralising rAF
  // keeps the two sides symmetric; the loop itself is covered separately.
  vi.stubGlobal('requestAnimationFrame', () => 0);
});

// ── Sprites ──────────────────────────────────────────────────────────────────

describe('the cached sprites', () => {
  it('builds the gloss sprite with identical operations', () => {
    const [a, b] = bothLogs(
      {},
      () => V._artMapGlossSprite(),
      () => artMapGlossSprite(),
    );
    // The sprite paints into its OWN canvas, not the passed ctx.
    expect(b).toEqual(a);
    const logs = createdCanvases.map((c) => c.__log);
    expect(logs[1]).toEqual(logs[0]);
    expect(logs[0].length).toBeGreaterThan(0);
  });

  it('caches the gloss sprite rather than rebuilding it', () => {
    reset(artMap, {});
    const first = artMapGlossSprite();
    const before = createdCanvases.length;
    expect(artMapGlossSprite()).toBe(first);
    expect(createdCanvases.length).toBe(before);
  });

  for (const hue of [0, 42, 200, 359]) {
    it(`builds the halo sprite for hue ${hue} identically`, () => {
      bothLogs(
        {},
        () => V._artMapHaloSprite(hue),
        () => artMapHaloSprite(hue),
      );
      const logs = createdCanvases.map((c) => c.__log);
      expect(logs[1]).toEqual(logs[0]);
      expect(logs[0].join('\n')).toContain(`hsla(${hue},75%,55%,0.22)`);
    });
  }

  it('caches halos PER HUE', () => {
    reset(artMap, {});
    const a = artMapHaloSprite(10);
    const b = artMapHaloSprite(20);
    expect(a).not.toBe(b);
    expect(artMapHaloSprite(10)).toBe(a);
  });
});

// ── The node painter ─────────────────────────────────────────────────────────

describe('artMapDrawNodeToBuffer', () => {
  const bitmap = { __label: 'bitmap', width: 128 } as unknown as CanvasImageSource;

  const cases: [string, ArtMapNode, number, Partial<ArtMapState>][] = [
    ['a hidden node draws nothing', node({ opacity: 0 }), 1, {}],
    ['a barely-visible node', node({ opacity: 0.02 }), 1, {}],
    ['a dot below 2.2px on screen', node({ radius: 2 }), 1, {}],
    ['a dot, watchlist coloured', node({ radius: 2, type: 'watchlist' }), 1, {}],
    // 2.2 is the dot/disc boundary; nothing else in this list lands between
    // 2.2 and 3.2, so a shifted threshold is invisible without it.
    ['just above the dot threshold', node({ radius: 2.5 }), 1, {}],
    ['a mid-size bubble with no art', node({ radius: 10 }), 1, {}],
    ['a bubble just under the gloss threshold', node({ radius: 11.9 }), 1, {}],
    ['a bubble at the gloss threshold', node({ radius: 12 }), 1, {}],
    ['a bubble at the label threshold', node({ radius: 13 }), 1, {}],
    ['a big watchlist bubble', node({ radius: 70.4, type: 'watchlist' }), 1, {}],
    [
      'a centre node is painted like a watchlist one',
      node({ radius: 70.4, type: 'center' }),
      1,
      {},
    ],
    ['a focal ring appears from 7px', node({ radius: 7, type: 'watchlist' }), 1, {}],
    ['no focal ring below 7px', node({ radius: 6.9, type: 'watchlist' }), 1, {}],
    ['a node with no hue', node({ radius: 20, _hue: undefined }), 1, {}],
    ['a node with hue 0', node({ radius: 20, _hue: 0 }), 1, {}],
    [
      'a long name is truncated',
      node({ radius: 40, name: 'A Very Long Artist Name Indeed' }),
      1,
      {},
    ],
    [
      'a long name on a watchlist node truncates at a different width',
      node({ radius: 40, name: 'A Very Long Artist Name Indeed', type: 'watchlist' }),
      1,
      {},
    ],
    ['a genre label', node({ _isLabel: true, name: 'hip hop', _count: 42, radius: 91.5 }), 1, {}],
    ['a genre label with no hue', node({ _isLabel: true, name: 'x', _hue: undefined }), 1, {}],
    ['a genre label with no count', node({ _isLabel: true, name: 'x', _count: undefined }), 1, {}],
    ['a tiny label still clears the 13px font floor', node({ _isLabel: true, radius: 1 }), 1, {}],
    ['a half-scale buffer changes every threshold', node({ radius: 20 }), 0.5, {}],
    ['a node with cached art', node({ radius: 40 }), 1, { images: { 1: bitmap } }],
    [
      'art plus a label darkens behind the text',
      node({ radius: 40 }),
      1,
      { images: { 1: bitmap } },
    ],
    [
      'art below the label threshold is not darkened',
      node({ radius: 12 }),
      1,
      { images: { 1: bitmap } },
    ],
    ['a global fade multiplier applies', node({ radius: 40 }), 1, { _drawAlphaMul: 0.5 }],
    ['a per-node opacity applies', node({ radius: 40, opacity: 0.6 }), 1, {}],
  ];

  for (const [label, n, scale, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const [a, b] = bothLogs(
        state,
        (ctx) => V._artMapDrawNodeToBuffer(ctx, n, scale),
        (ctx) => artMapDrawNodeToBuffer(ctx, n, scale),
      );
      expect(b).toEqual(a);
    });
  }

  it('really is recording something', () => {
    const [a] = bothLogs(
      {},
      (ctx) => V._artMapDrawNodeToBuffer(ctx, node({ radius: 40 }), 1),
      (ctx) => artMapDrawNodeToBuffer(ctx, node({ radius: 40 }), 1),
    );
    expect(a.length).toBeGreaterThan(8);
    expect(a.join('\n')).toContain('arc(');
  });
});

// ── The buffer ───────────────────────────────────────────────────────────────

describe('artMapRebuildBuffer', () => {
  const world = (n: number, over: Partial<ArtMapNode> = {}) =>
    Array.from({ length: n }, (_, i) =>
      node({ id: i, x: i * 200, y: (i % 3) * 150, radius: 40, ...over }),
    );

  const cases: [string, Partial<ArtMapState>][] = [
    ['nothing placed', { placed: [] }],
    ['everything hidden', { placed: world(3, { opacity: 0 }) }],
    // The visibility gate is `> 0.01`, not `> 0` — a bubble one hundredth of the
    // way in is still excluded from the bounds AND the draw.
    ['a barely-faded world is still excluded', { placed: world(3, { opacity: 0.005 }) }],
    ['exactly at the visibility floor', { placed: world(3, { opacity: 0.01 }) }],
    ['a small world', { placed: world(3) }],
    // A fractional world size is the only shape where ceil and floor disagree on
    // the buffer's pixel dimensions.
    [
      'a fractional world rounds the buffer UP',
      {
        placed: [
          node({ id: 0, x: 0, y: 0, radius: 40.3 }),
          node({ id: 1, x: 200.7, y: 0, radius: 40 }),
        ],
      },
    ],
    [
      'a world with edges',
      {
        placed: world(4),
        edges: [
          { source: 0, target: 1 },
          { source: 1, target: 2 },
        ],
      },
    ],
    [
      'an edge to a hidden node is skipped',
      {
        placed: [
          node({ id: 0, x: 0, y: 0, radius: 40 }),
          node({ id: 1, x: 200, y: 0, radius: 40, opacity: 0.01 }),
        ],
        edges: [{ source: 0, target: 1 }],
      },
    ],
    [
      'an edge to a missing node is skipped',
      { placed: world(2), edges: [{ source: 0, target: 99 }] },
    ],
    // These four use SMALL radii on purpose. At radius 40 and zoom 1 every
    // bubble is live-size and the buffer skips it, so the three-pass ordering
    // leaves no trace in the log and a reversed loop survives.
    [
      'labels paint before bubbles',
      { placed: [node({ id: 0, radius: 4 }), node({ id: 1, _isLabel: true, x: 0, y: -300 })] },
    ],
    [
      'watchlist bubbles paint last',
      {
        placed: [node({ id: 0, type: 'watchlist', radius: 4 }), node({ id: 1, x: 300, radius: 4 })],
      },
    ],
    [
      'a ring-1 node paints in the watchlist pass, exactly once',
      { placed: [node({ id: 0, ring: 1, radius: 4 }), node({ id: 1, x: 300, radius: 4 })] },
    ],
    [
      'a label, a swarm bubble and a watchlist bubble in one buffer',
      {
        placed: [
          node({ id: 0, x: 300, radius: 4 }),
          node({ id: 1, type: 'watchlist', radius: 4 }),
          node({ id: 2, _isLabel: true, x: 0, y: -300 }),
        ],
      },
    ],
    [
      'hideSimilar drops the swarm',
      {
        placed: [node({ id: 0, type: 'watchlist', radius: 4 }), node({ id: 1, x: 300, radius: 4 })],
        _hideSimilar: true,
      },
    ],
    ['a low zoom shrinks the buffer', { placed: world(3), zoom: 0.15 }],
    ['a high zoom caps the buffer at 1:1', { placed: world(3), zoom: 4 }],
    ['a zoom of 0 falls back to 0.1', { placed: world(3), zoom: 0 }],
    [
      'a huge world hits MAX_BUFFER_PX',
      { placed: [node({ id: 0, x: -60000, radius: 40 }), node({ id: 1, x: 60000, radius: 40 })] },
    ],
    ['more than 140 live bubbles flips overflow', { placed: world(200), zoom: 1 }],
    ['under the overflow limit keeps the live split', { placed: world(20), zoom: 1 }],
  ];

  for (const [label, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const [a, b] = bothLogs(
        state,
        () => V._artMapRebuildBuffer(),
        () => artMapRebuildBuffer(),
      );
      expect(b).toEqual(a);
      // Plus the offscreen canvas's own operations, and the derived state.
      const logs = createdCanvases.map((c) => c.__log);
      if (logs.length === 2) expect(logs[1]).toEqual(logs[0]);
      expect(artMap._bufferScale).toBe(V._artMap._bufferScale);
      expect(artMap._bufferMinX).toBe(V._artMap._bufferMinX);
      expect(artMap._bufferMinY).toBe(V._artMap._bufferMinY);
      expect(artMap._liveOverflow).toBe(V._artMap._liveOverflow);
      expect(artMap._liveBuildZoom).toBe(V._artMap._liveBuildZoom);
      expect(artMap.dirty).toBe(V._artMap.dirty);
    });
  }

  it('flips _liveOverflow at 140 live bubbles, not 141', () => {
    // 140 live is NOT overflow; 141 is. Straddling the boundary is what makes
    // the comparison operator observable.
    for (const [count, expected] of [
      [140, false],
      [141, true],
    ] as [number, boolean][]) {
      reset(artMap, { placed: world(count), zoom: 1 });
      artMapRebuildBuffer();
      expect(artMap._liveOverflow).toBe(expected);
    }
  });
});

describe('artMapCompositeNode', () => {
  const cases: [string, ArtMapNode, Partial<ArtMapState>][] = [
    ['no buffer yet', node({ radius: 40 }), { offscreen: null, _bufferScale: undefined }],
    [
      'a hidden node',
      node({ radius: 40, opacity: 0 }),
      { _bufferScale: 1, _bufferMinX: 0, _bufferMinY: 0 },
    ],
    [
      'a similar node while hideSimilar is on',
      node({ radius: 40 }),
      { _bufferScale: 1, _bufferMinX: 0, _bufferMinY: 0, _hideSimilar: true },
    ],
    [
      'a live-size node signals a blit without drawing',
      node({ radius: 40 }),
      { _bufferScale: 1, _bufferMinX: 0, _bufferMinY: 0, zoom: 1, _liveBuildZoom: 1 },
    ],
    [
      'a buffer-size node is composited',
      node({ radius: 4 }),
      { _bufferScale: 1, _bufferMinX: -100, _bufferMinY: -50, zoom: 1, _liveBuildZoom: 1 },
    ],
  ];

  for (const [label, n, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      let va: boolean | undefined;
      let mb: boolean | undefined;
      const withBuffer = { ...state } as Partial<ArtMapState>;
      const [a, b] = bothLogs(
        withBuffer,
        () => {
          if (withBuffer._bufferScale != null) {
            V._artMap.offscreen = document.createElement('canvas');
          }
          va = V._artMapCompositeNode(n);
        },
        () => {
          if (withBuffer._bufferScale != null) {
            artMap.offscreen = document.createElement('canvas');
          }
          mb = artMapCompositeNode(n);
        },
      );
      expect(b).toEqual(a);
      expect(mb).toBe(va);
      const logs = createdCanvases.map((c) => c.__log);
      if (logs.length === 2) expect(logs[1]).toEqual(logs[0]);
    });
  }
});

// ── The live overlay ─────────────────────────────────────────────────────────

describe('artMapDrawLiveLayer', () => {
  const big = (i: number, over: Partial<ArtMapNode> = {}) =>
    node({ id: i, x: i * 100, y: 0, radius: 40, ...over });

  const cases: [string, Partial<ArtMapState>][] = [
    ['nothing placed', { placed: [] }],
    ['big bubbles at zoom 1', { placed: [big(0), big(1)], zoom: 1, _liveBuildZoom: 1 }],
    [
      'small bubbles are left to the buffer',
      { placed: [big(0, { radius: 4 })], zoom: 1, _liveBuildZoom: 1 },
    ],
    [
      'while revealing, EVERYTHING draws',
      { placed: [big(0, { radius: 1 })], zoom: 1, _revealing: true },
    ],
    [
      'labels draw only while revealing',
      { placed: [big(0, { _isLabel: true })], zoom: 1, _revealing: true },
    ],
    [
      'hideSimilar culls the swarm',
      {
        placed: [big(0), big(1, { type: 'watchlist' })],
        zoom: 1,
        _liveBuildZoom: 1,
        _hideSimilar: true,
      },
    ],
    [
      'off-screen bubbles are culled',
      {
        placed: [big(0), node({ id: 9, x: 100000, y: 0, radius: 40 })],
        zoom: 1,
        _liveBuildZoom: 1,
      },
    ],
    [
      'a bubble just inside the margin survives the cull',
      {
        placed: [node({ id: 0, x: -700 - 40 - 79, y: 0, radius: 40 })],
        zoom: 1,
        _liveBuildZoom: 1,
      },
    ],
    // 160px past the left edge: culled by the real 80px margin, kept by a wider
    // one. Nothing else here lands between the two.
    [
      'a bubble just OUTSIDE the margin is culled',
      { placed: [node({ id: 0, x: -900, y: 0, radius: 40 })], zoom: 1, _liveBuildZoom: 1 },
    ],
    [
      'a bobbing bubble is offset',
      { placed: [big(0, { _bobAmp: 8, _bobPhase: 1.2 })], zoom: 1, _liveBuildZoom: 1, _now: 12345 },
    ],
    [
      'a ripple shove displaces it',
      {
        placed: [big(0, { x: 300 })],
        zoom: 1,
        _liveBuildZoom: 1,
        _now: 1300,
        _ripples: [
          { cx: 0, cy: 0, hue: 270, maxR: 832, t0: 1000, dur: 900, push: 70.4, width: 192 },
        ] as ArtMapRipple[],
      },
    ],
    [
      'a mid-bloom bubble is scaled and faded',
      {
        placed: [big(0, { aScale: 0.4, aAlpha: 0.3, _revealRise: 12 })],
        zoom: 1,
        _revealing: true,
      },
    ],
    [
      'a fully-collapsed bubble draws nothing',
      { placed: [big(0, { aScale: 0 })], zoom: 1, _revealing: true },
    ],
    [
      'more bubbles than the cap',
      {
        placed: Array.from({ length: 700 }, (_, i) => node({ id: i, x: 0, y: 0, radius: 40 })),
        zoom: 1,
        _liveBuildZoom: 1,
      },
    ],
  ];

  for (const [label, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const [a, b] = bothLogs(
        state,
        (ctx) => V._artMapDrawLiveLayer(ctx),
        (ctx) => artMapDrawLiveLayer(ctx),
      );
      expect(b).toEqual(a);
      expect(artMap._liveCount).toBe(V._artMap._liveCount);
    });
  }

  it('caps at 600 live bubbles at rest', () => {
    reset(artMap, {
      placed: Array.from({ length: 3000 }, (_, i) => node({ id: i, x: 0, y: 0, radius: 40 })),
      zoom: 1,
      _liveBuildZoom: 1,
    });
    artMapDrawLiveLayer(makeRecorder().ctx);
    expect(artMap._liveCount).toBe(600);
  });

  it('raises the cap to 2200 while revealing, so the whole map can bloom', () => {
    // `_liveCount` is forced to 0 while revealing, so the cap is only observable
    // through how many bubbles actually got drawn. Comparing both sides' logs
    // is what pins the number.
    const placed = Array.from({ length: 2400 }, (_, i) => node({ id: i, x: 0, y: 0, radius: 40 }));
    const [a, b] = bothLogs(
      { placed, zoom: 1, _revealing: true },
      (ctx) => V._artMapDrawLiveLayer(ctx),
      (ctx) => artMapDrawLiveLayer(ctx),
    );
    expect(b).toEqual(a);
    // One fillText per bubble (its name) — 2200 drawn, not 2400 and not 1200.
    expect(a.filter((l) => l.startsWith('fillText(')).length).toBe(2200);
  });
});

describe('artMapDrawLiveNode + hover pop', () => {
  const cases: [string, ArtMapNode, Partial<ArtMapState>][] = [
    ['a plain node', node({ radius: 40 }), { zoom: 1 }],
    ['a scaled node', node({ radius: 40, aScale: 0.5 }), { zoom: 1 }],
    ['a faded node', node({ radius: 40, aAlpha: 0.25 }), { zoom: 1 }],
    ['a collapsed node draws nothing', node({ radius: 40, aScale: 0.0005 }), { zoom: 1 }],
    [
      'a rising node during the reveal',
      node({ radius: 40, _revealRise: 30 }),
      { zoom: 1, _revealing: true },
    ],
    [
      'a bobbing node at rest',
      node({ radius: 40, _bobAmp: 8, _bobPhase: 0.3 }),
      { zoom: 1, _now: 9999 },
    ],
  ];

  for (const [label, n, state] of cases) {
    it(`artMapDrawLiveNode matches the vanilla — ${label}`, () => {
      const [a, b] = bothLogs(
        state,
        (ctx) => V._artMapDrawLiveNode(ctx, n),
        (ctx) => artMapDrawLiveNode(ctx, n),
      );
      expect(b).toEqual(a);
    });
  }

  for (const [label, n, state] of cases.slice(0, 3)) {
    it(`artMapDrawHoverPop matches the vanilla — ${label}`, () => {
      const [a, b] = bothLogs(
        state,
        (ctx) => V._artMapDrawHoverPop(ctx, n),
        (ctx) => artMapDrawHoverPop(ctx, n),
      );
      expect(b).toEqual(a);
    });
  }

  it('artMapDrawHoverPop uses the cached bitmap when there is one', () => {
    const bitmap = { __label: 'bitmap', width: 128 } as unknown as CanvasImageSource;
    const n = node({ id: 5, radius: 40 });
    const [a, b] = bothLogs(
      { zoom: 1, images: { 5: bitmap } },
      (ctx) => V._artMapDrawHoverPop(ctx, n),
      (ctx) => artMapDrawHoverPop(ctx, n),
    );
    expect(b).toEqual(a);
    expect(a.join('\n')).toContain('drawImage(bitmap');
  });
});

// ── Ripples ──────────────────────────────────────────────────────────────────

describe('artMapDrawRipples', () => {
  const rip = (over: Partial<ArtMapRipple> = {}): ArtMapRipple => ({
    cx: 0,
    cy: 0,
    hue: 270,
    maxR: 800,
    t0: 4500,
    dur: 900,
    ...over,
  });

  const cases: [string, Partial<ArtMapState>][] = [
    ['no ripples', { _ripples: null }],
    ['an empty list', { _ripples: [] }],
    ['one mid-flight', { _ripples: [rip()] }],
    ['one not yet started', { _ripples: [rip({ t0: 6000 })] }],
    ['one already finished', { _ripples: [rip({ t0: 0 })] }],
    ['exactly at the start', { _ripples: [rip({ t0: 5000 })] }],
    ['exactly at the end', { _ripples: [rip({ t0: 4100 })] }],
    ['several at once', { _ripples: [rip(), rip({ cx: 400, hue: 30, t0: 4800 })] }],
    ['at a different zoom the line width compensates', { _ripples: [rip()], zoom: 0.25 }],
  ];

  for (const [label, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const [a, b] = bothLogs(
        state,
        (ctx) => V._artMapDrawRipples(ctx),
        (ctx) => artMapDrawRipples(ctx),
      );
      expect(b).toEqual(a);
    });
  }
});

describe('artMapEmitRipple', () => {
  for (const [label, hue] of [
    ['with a hue', 42],
    ['with no hue', null],
    ['with hue 0', 0],
  ] as [string, number | null][]) {
    it(`matches the vanilla ${label}`, () => {
      reset(V._artMap, {});
      reset(artMap, {});
      V._artMapEmitRipple(100, -50, hue);
      artMapEmitRipple(100, -50, hue);
      expect(artMap._ripples).toEqual(V._artMap._ripples);
    });
  }

  it('appends rather than replacing', () => {
    reset(artMap, {});
    artMapEmitRipple(0, 0, 1);
    artMapEmitRipple(10, 10, 2);
    expect(artMap._ripples).toHaveLength(2);
  });
});

// ── The reveal ───────────────────────────────────────────────────────────────

describe('artMapBeginReveal', () => {
  const islands: ArtMapIsland[] = [
    { name: 'Rock', cx: 0, cy: 0, r: 500, hue: 42, count: 9 },
    { name: 'Jazz', cx: 2000, cy: 0, r: 300, hue: 180, count: 4 },
  ];
  const placed = (): ArtMapNode[] => [
    node({ id: 'label_Rock', name: 'Rock', _isLabel: true, x: 0, y: -600, _island: undefined }),
    node({ id: 0, x: 0, y: 0, _island: 'Rock' }),
    node({ id: 1, x: 400, y: 0, _island: 'Rock' }),
    node({ id: 2, x: 2000, y: 0, _island: 'Jazz' }),
    node({ id: 3, x: 5000, y: 0, _island: 'Nowhere' }), //  an island that no longer exists
  ];

  const cases: [string, Partial<ArtMapState>][] = [
    ['two islands', { placed: placed(), _islands: islands }],
    ['no islands at all', { placed: placed(), _islands: [] }],
    ['a zero-radius island', { placed: placed(), _islands: [{ ...islands[0], r: 0 }] }],
    [
      'a node beyond its island radius clamps to 1',
      { placed: [node({ id: 0, x: 99999, _island: 'Rock' })], _islands: islands },
    ],
  ];

  for (const [label, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      reset(V._artMap, state);
      reset(artMap, state);
      V._artMapBeginReveal();
      artMapBeginReveal();
      expect(artMap.placed).toEqual(V._artMap.placed);
      expect(artMap._ripples).toEqual(V._artMap._ripples);
      expect(artMap._islands).toEqual(V._artMap._islands);
      expect(artMap._revealing).toBe(V._artMap._revealing);
      expect(artMap._ambient).toBe(V._artMap._ambient);
      expect(artMap._fieldAlpha).toBe(V._artMap._fieldAlpha);
    });
  }

  it('matches a genre LABEL to its island by name and delays it', () => {
    reset(artMap, { placed: placed(), _islands: islands });
    artMapBeginReveal();
    const label = artMap.placed[0];
    const firstBubble = artMap.placed[1];
    // Both sit on the Rock island (order 0); the label is pushed 90ms later.
    expect((label._revealAt as number) - (firstBubble._revealAt as number)).toBeCloseTo(
      90 + 430,
      5,
    );
  });
});

describe('artMapBloomIsland', () => {
  const isl: ArtMapIsland = { name: 'Rock', cx: 0, cy: 0, r: 500, hue: 42, count: 9 };

  const cases: [string, Partial<ArtMapState>, ArtMapIsland][] = [
    [
      'visible bubbles bloom',
      { placed: [node({ id: 0, x: 0, y: 0 }), node({ id: 1, x: 400, y: 0 })] },
      isl,
    ],
    [
      'hidden bubbles are skipped entirely',
      { placed: [node({ id: 0, opacity: 0 }), node({ id: 1, x: 400, y: 0 })] },
      isl,
    ],
    ['a zero-radius island', { placed: [node({ id: 0 })] }, { ...isl, r: 0 }],
    ['a string node id still yields a jitter', { placed: [node({ id: 'label_Rock' })] }, isl],
    [
      'a node with no radius uses the 20 fallback',
      { placed: [node({ id: 3, radius: undefined as unknown as number })] },
      isl,
    ],
  ];

  for (const [label, state, island] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      reset(V._artMap, state);
      reset(artMap, state);
      V._artMapBloomIsland(island);
      artMapBloomIsland(island);
      expect(artMap.placed).toEqual(V._artMap.placed);
      expect(artMap._ripples).toEqual(V._artMap._ripples);
    });
  }
});

// ── The whole frame ──────────────────────────────────────────────────────────

describe('artMapDraw', () => {
  const world = () => [
    node({ id: 0, x: 0, y: 0, radius: 40 }),
    node({ id: 1, x: 300, y: 0, radius: 40, type: 'watchlist' }),
    node({ id: 2, x: -300, y: 100, radius: 4 }),
  ];
  const islands: ArtMapIsland[] = [{ name: 'Rock', cx: 0, cy: 0, r: 500, hue: 42, count: 3 }];

  const cases: [string, Partial<ArtMapState>][] = [
    ['an empty map', { placed: [] }],
    ['a plain frame', { placed: world() }],
    ['while revealing, the buffer is bypassed', { placed: world(), _revealing: true }],
    [
      'one-island mode draws the halo',
      { placed: world(), _islands: islands, _oneIsland: true, _focusIdx: 0 },
    ],
    ['a partly-faded field', { placed: world(), _fieldAlpha: 0.4 }],
    ['a fully-opaque field skips the alpha set', { placed: world(), _fieldAlpha: 1 }],
    ['a hovered node pops', { placed: world(), hoveredNode: world()[0] }],
    [
      'a constellation with connections',
      {
        placed: world(),
        edges: [
          { source: 1, target: 0 },
          { source: 1, target: 2 },
        ],
        hoveredNode: world()[1],
        _constellationFade: 1,
        _constellationActive: true,
      },
    ],
    [
      'a constellation from a SIMILAR node walks back through its sources',
      {
        placed: world(),
        edges: [
          { source: 1, target: 0 },
          { source: 1, target: 2 },
        ],
        hoveredNode: world()[0],
        _constellationFade: 1,
        _constellationActive: true,
      },
    ],
    [
      'a lone node mid-constellation just pops',
      {
        placed: world(),
        edges: [],
        hoveredNode: world()[0],
        _constellationFade: 1,
        _constellationActive: true,
      },
    ],
    [
      'a half-faded constellation',
      {
        placed: world(),
        edges: [{ source: 1, target: 0 }],
        hoveredNode: world()[1],
        _constellationFade: 0.5,
        _constellationActive: true,
      },
    ],
    // The blit divides by the buffer scale. At zoom 1 the scale is also 1, so
    // multiply and divide agree and the whole expression is untested.
    ['a zoomed-out frame, where the buffer scale is not 1', { placed: world(), zoom: 0.3 }],
    ['a zoomed-in frame', { placed: world(), zoom: 0.75 }],
    // A cache left over from a DIFFERENT node must be rebuilt, not reused.
    [
      'a stale constellation cache is discarded',
      {
        placed: world(),
        edges: [{ source: 1, target: 0 }],
        hoveredNode: world()[1],
        _constellationFade: 1,
        _constellationActive: true,
        _constellationCache: { nodeId: 2, nodes: [world()[2]] },
      },
    ],
    // Fade is 0 while the constellation is ARMED: the pre-fade hover pop must
    // not fire, or the bubble double-draws for a frame.
    [
      'an armed but unfaded constellation suppresses the hover pop',
      {
        placed: world(),
        hoveredNode: world()[0],
        _constellationFade: 0,
        _constellationActive: true,
      },
    ],
  ];

  for (const [label, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const [a, b] = bothLogs(
        state,
        () => V._artMapDraw(),
        () => artMapDraw(),
      );
      expect(b).toEqual(a);
      expect(artMap._constellationCache).toEqual(V._artMap._constellationCache);
    });
  }

  it('caches the background gradient across frames', () => {
    reset(artMap, { placed: world() });
    const rec = makeRecorder();
    artMap.ctx = rec.ctx;
    artMap.canvas = { width: 2800, height: 1600 } as HTMLCanvasElement;
    artMapDraw();
    const firstGradients = rec.log.filter((l) => l.startsWith('createRadialGradient')).length;
    artMapDraw();
    const total = rec.log.filter((l) => l.startsWith('createRadialGradient')).length;
    expect(firstGradients).toBe(1);
    expect(total).toBe(1); //  the second frame reuses it
  });

  it('rebuilds the gradient when the canvas resizes', () => {
    reset(artMap, { placed: world() });
    const rec = makeRecorder();
    artMap.ctx = rec.ctx;
    artMap.canvas = { width: 2800, height: 1600 } as HTMLCanvasElement;
    artMapDraw();
    artMap.width = 900;
    artMapDraw();
    expect(rec.log.filter((l) => l.startsWith('createRadialGradient')).length).toBe(2);
  });
});

// ── The perf HUD ─────────────────────────────────────────────────────────────

describe('the perf overlay', () => {
  it('draws the same HUD as the vanilla', () => {
    const fetchSpy = vi.fn(() => Promise.resolve({ ok: true }));
    vi.stubGlobal('fetch', fetchSpy);
    const state: Partial<ArtMapState> = {
      placed: [node({ id: 0 }), node({ id: 1 })],
      edges: [{ source: 0, target: 1 }],
      zoom: 0.4321,
      _bufferScale: 0.8642,
      _rebuildMs: 12.34,
      _lastPerfTs: 4900,
    };
    const [a, b] = bothLogs(
      state,
      (ctx) => V._artMapDrawPerf(ctx, 4990),
      (ctx) => artMapDrawPerf(ctx, 4990),
    );
    expect(b).toEqual(a);
    vi.unstubAllGlobals();
  });

  it('posts the numbers at the precision the vanilla used', () => {
    const fetchSpy = vi.fn((_u: string, _i?: unknown) => Promise.resolve({ ok: true }));
    vi.stubGlobal('fetch', fetchSpy);
    reset(artMap, {
      placed: [node({ id: 0 }), node({ id: 1 })],
      edges: [{ source: 0, target: 1 }],
      zoom: 0.43217,
      _bufferScale: 0.86428,
      _rebuildMs: 12.345,
    });
    const rec = makeRecorder();
    artMapDrawPerf(rec.ctx, 4990);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const init = fetchSpy.mock.calls[0][1] as unknown as { body: string };
    const body = JSON.parse(init.body);
    // Asserted against LITERALS, not against artMapPerfReport — comparing the
    // payload to the same function that produced it is a tautology, and a
    // toFixed(3)→toFixed(2) mutation survived exactly that.
    expect(body.zoom).toBe(0.432);
    expect(body.scale).toBe(0.864);
    expect(body.rebuildMs).toBe(12.3);
    expect(body.nodes).toBe(2);
    expect(body.edges).toBe(1);
    expect(body.drawMs).toBe(10);
    expect(artMapPerfReport(10, body.fps).payload).toEqual(body);
    vi.unstubAllGlobals();
  });

  it('throttles the POST to roughly 1.5 a second', () => {
    const fetchSpy = vi.fn(() => Promise.resolve({ ok: true }));
    vi.stubGlobal('fetch', fetchSpy);
    reset(artMap, { placed: [node({ id: 0 })] });
    const rec = makeRecorder();
    artMapDrawPerf(rec.ctx, 5000);
    artMapDrawPerf(rec.ctx, 5000); //  same clock — suppressed
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    // 300ms later is still inside the window. Without this the interval could be
    // shortened to a fifth and nothing would notice.
    vi.spyOn(performance, 'now').mockReturnValue(5300);
    artMapDrawPerf(rec.ctx, 5300);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    vi.spyOn(performance, 'now').mockReturnValue(5701);
    artMapDrawPerf(rec.ctx, 5701);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    vi.unstubAllGlobals();
  });
});
