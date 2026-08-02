import { beforeEach, describe, expect, it } from 'vitest';

import { loadVanilla } from '../../test/vanilla-extract';
import {
  type ArtMapEdge,
  type ArtMapIsland,
  type ArtMapNode,
  type ArtMapRawNode,
  type ArtMapRipple,
  type ArtMapState,
  artMap,
  artMapConnCount,
  artMapFitToContent,
  artMapFitToViewTarget,
  artMapGenreHue,
  artMapGroupByGenre,
  artMapHitTest,
  artMapImgPx,
  artMapIsLiveSize,
  artMapIsMobile,
  artMapIsWatched,
  artMapIslandCamera,
  artMapLayoutIslands,
  artMapNodeBest,
  artMapNodeDisplacement,
  artMapNodeImgPx,
  artMapPackDisc,
  artMapRemapEdges,
  artMapReservedW,
  artMapScreenToWorld,
  artMapStepAnimations,
  artMapZoomTarget,
} from './-discover.artist-map';

/**
 * Differential parity for the Artist Map's geometry core.
 *
 * The map's functions read and write a shared `_artMap` singleton rather than
 * taking everything by argument, so the preamble below recreates that object and
 * every case sets the SAME state on both sides before comparing. Where a vanilla
 * function's tail calls into the render/DOM layer (`_artMapRender`,
 * `_artMapRefreshPanel`, …) the preamble stubs it — a stub that RECORDS its
 * arguments, so the camera math those calls carry is still compared rather than
 * skipped.
 *
 * A mutation pass over the port raised 68 mutants; 66 die here. The two that
 * survive are EQUIVALENT, not gaps, and are recorded rather than papered over:
 *
 *   - `Math.max(1, Math.floor(circ / step))` in `artMapPackDisc`. The floor can
 *     never bind: `ringDist` starts AT `step` and only grows, so `circ / step`
 *     is `2π * ringDist / step ≥ 2π ≈ 6.28`. Dropping the `max` changes nothing
 *     for any reachable input.
 *   - `if (!r.push) continue;` in `artMapNodeDisplacement`. It is a fast path.
 *     Without it a push-less ripple computes `undefined * env * (1-p)` → NaN
 *     (or `0 * …` → 0), and neither clears the `> 0.05` gate, so no
 *     displacement is contributed either way.
 */
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
const animateToCalls = [];
function _artMapAnimateTo(z, ox, oy) { animateToCalls.push({ zoom: z, offsetX: ox, offsetY: oy }); }
function _artMapUpdateIslandNav() {}
function _artMapRefreshPanel() {}
function _artMapBloomIsland() {}
function _artMapRender() {}
function _artMapStartLoop() {}
`;

interface Vanilla {
  _artMap: ArtMapState;
  animateToCalls: { zoom: number; offsetX: number; offsetY: number }[];
  _artMapGenreHue: (name: unknown) => number;
  _artMapPackDisc: (
    members: unknown[],
    nodeR: number,
    gap: number,
  ) => { placements: unknown[]; islandR: number };
  _artMapGroupByGenre: (nodes: unknown[], maxIslands?: number) => unknown[];
  _artMapLayoutIslands: (groups: unknown[], opts?: unknown) => void;
  _artMapRemapEdges: (edges: unknown) => unknown[];
  _artMapFitToContent: (marginPx?: number) => void;
  _artMapFocusIsland: (idx: number, opts?: unknown) => void;
  _artMapHitTest: (wx: number, wy: number) => unknown;
  _artMapScreenToWorld: (e: unknown, canvas: unknown) => { nx: number; ny: number };
  _artMapNodeBest: (n: unknown) => { id: string; source: string };
  _artMapConnCount: (n: unknown) => number;
  _artMapIsWatched: (n: unknown) => boolean;
  _artMapIsLiveSize: (n: unknown) => boolean;
  _artMapStepAnimations: (t: number) => boolean;
  _artMapNodeDisplacement: (n: unknown) => { dx: number; dy: number } | null;
  _artMapImgPx: (px: unknown) => number;
  _artMapNodeImgPx: (n: unknown) => number;
  _artMapIsMobile: () => boolean;
  _artMapReservedW: () => number;
  artMapZoom: (factor: number) => void;
  artMapFitToView: () => void;
}

const V = loadVanilla<Vanilla>(
  [
    '_artMapGenreHue',
    '_artMapPackDisc',
    '_artMapGroupByGenre',
    '_artMapLayoutIslands',
    '_artMapRemapEdges',
    '_artMapFitToContent',
    '_artMapFocusIsland',
    '_artMapHitTest',
    '_artMapScreenToWorld',
    '_artMapNodeBest',
    '_artMapConnCount',
    '_artMapIsWatched',
    '_artMapIsLiveSize',
    '_artMapStepAnimations',
    '_artMapNodeDisplacement',
    '_artMapImgPx',
    '_artMapNodeImgPx',
    '_artMapIsMobile',
    '_artMapReservedW',
    'artMapZoom',
    'artMapFitToView',
  ],
  PREAMBLE,
  ['_artMap', 'animateToCalls'],
);

/** Put both singletons in the same state before a comparison. */
function sync(state: Partial<ArtMapState>) {
  const base: Partial<ArtMapState> = {
    placed: [],
    edges: [],
    width: 0,
    height: 0,
    offsetX: 0,
    offsetY: 0,
    zoom: 0.15,
    _islands: undefined,
    _nodeById: undefined,
    _ripples: undefined,
    _watchSet: undefined,
    _liveOverflow: undefined,
    _liveBuildZoom: undefined,
    _revealing: undefined,
    _now: undefined,
    _panelW: 320,
    ...state,
  };
  for (const k of Object.keys(base)) {
    const v = (base as Record<string, unknown>)[k];
    (artMap as Record<string, unknown>)[k] = v;
    // Deep-copy the mutable collections so a function that writes to one side
    // cannot leak into the other and hide a divergence.
    (V._artMap as Record<string, unknown>)[k] = Array.isArray(v)
      ? JSON.parse(JSON.stringify(v))
      : v instanceof Set
        ? new Set(v)
        : v;
  }
  V.animateToCalls.length = 0;
}

beforeEach(() => sync({}));

describe('artMapGenreHue', () => {
  const cases = [
    'Rock',
    'rock',
    'ROCK',
    'hip hop',
    'Hip-Hop',
    'Other',
    '',
    ' ',
    'a',
    'z',
    'électronique',
    '日本のロック',
    'Drum & Bass',
    'Rock ',
    ' Rock',
    'a'.repeat(200), //   long enough that a non-rolling modulus would overflow differently
    null,
    undefined,
  ];
  for (const input of cases) {
    it(`matches the vanilla for ${JSON.stringify(input)}`, () => {
      expect(artMapGenreHue(input as string)).toBe(V._artMapGenreHue(input));
    });
  }
});

describe('artMapPackDisc', () => {
  const mk = (n: number) => Array.from({ length: n }, (_, i) => ({ id: i, name: `a${i}` }));
  const cases: [ArtMapRawNode[], number, number][] = [
    [[], 70.4, 17.6],
    [mk(1), 70.4, 17.6],
    [mk(2), 70.4, 17.6],
    [mk(7), 70.4, 17.6],
    [mk(8), 70.4, 17.6],
    [mk(9), 70.4, 17.6],
    [mk(40), 70.4, 17.6],
    [mk(300), 70.4, 17.6],
    [mk(12), 1, 0], //       degenerate gap — the ring cap must not divide by zero
    [mk(12), 0.5, 0.5],
    [mk(3), 1000, 1000],
  ];
  for (const [members, nodeR, gap] of cases) {
    it(`matches the vanilla for ${members.length} members @ r=${nodeR} gap=${gap}`, () => {
      expect(artMapPackDisc(members, nodeR, gap)).toEqual(V._artMapPackDisc(members, nodeR, gap));
    });
  }
});

describe('artMapGroupByGenre', () => {
  const cases: [ArtMapRawNode[], number | undefined][] = [
    [[], undefined],
    [[{ name: 'a' }], undefined],
    [[{ name: 'a', genres: [] }], undefined],
    [[{ name: 'a', genres: ['rock'] }], undefined],
    // Title-casing must fold these into ONE island.
    [
      [
        { name: 'a', genres: ['hip hop'] },
        { name: 'b', genres: ['Hip Hop'] },
        { name: 'c', genres: ['HIP HOP'] },
      ],
      undefined,
    ],
    // Only genres[0] counts — the second tag must be ignored entirely.
    [[{ name: 'a', genres: ['rock', 'jazz'] }], undefined],
    // Ties keep insertion order through a stable sort.
    [
      [
        { name: 'a', genres: ['rock'] },
        { name: 'b', genres: ['jazz'] },
      ],
      undefined,
    ],
    // Long tail folds into "Other" — including the case where a real "Other"
    // island already survived into the head.
    [
      [
        ...Array.from({ length: 20 }, (_, i) => ({ name: `x${i}`, genres: ['Other'] })),
        ...Array.from({ length: 30 }, (_, i) => ({ name: `g${i}`, genres: [`genre${i}`] })),
      ],
      undefined,
    ],
    [Array.from({ length: 30 }, (_, i) => ({ name: `g${i}`, genres: [`genre${i}`] })), 3],
    [Array.from({ length: 30 }, (_, i) => ({ name: `g${i}`, genres: [`genre${i}`] })), 1],
    // Non-string genre entries reach String() before the title-case regex.
    [[{ name: 'a', genres: [5 as unknown as string] }], undefined],
  ];
  cases.forEach(([nodes, maxIslands], i) => {
    it(`matches the vanilla for case ${i}`, () => {
      const mine =
        maxIslands === undefined
          ? artMapGroupByGenre(nodes)
          : artMapGroupByGenre(nodes, maxIslands);
      const theirs =
        maxIslands === undefined
          ? V._artMapGroupByGenre(nodes)
          : V._artMapGroupByGenre(nodes, maxIslands);
      expect(mine).toEqual(theirs);
    });
  });
});

describe('artMapLayoutIslands', () => {
  const artist = (i: number, over: Partial<ArtMapRawNode> = {}): ArtMapRawNode => ({
    id: `id${i}`,
    name: `Artist ${i}`,
    popularity: i,
    genres: ['rock'],
    image_url: `/i${i}.jpg`,
    spotify_id: `sp${i}`,
    ...over,
  });

  const cases: [string, { name: string; nodes: ArtMapRawNode[]; count?: number }[], unknown][] = [
    ['no groups', [], undefined],
    ['one artist', [{ name: 'Rock', nodes: [artist(1)], count: 1 }], undefined],
    [
      'two islands push apart',
      [
        { name: 'Rock', nodes: Array.from({ length: 12 }, (_, i) => artist(i)), count: 12 },
        { name: 'Jazz', nodes: Array.from({ length: 9 }, (_, i) => artist(i)), count: 9 },
      ],
      undefined,
    ],
    [
      'five islands, the full push loop',
      Array.from({ length: 5 }, (_, g) => ({
        name: `G${g}`,
        nodes: Array.from({ length: 20 + g * 7 }, (_, i) => artist(i)),
        count: 20 + g * 7,
      })),
      undefined,
    ],
    [
      'focal nodes sort to the centre and size up',
      [
        {
          name: 'Rock',
          nodes: [artist(1), artist(2, { _focal: true }), artist(3), artist(4, { _focal: true })],
          count: 4,
        },
      ],
      undefined,
    ],
    [
      'count comes from the GROUP, not the capped members',
      [{ name: 'Rock', nodes: Array.from({ length: 40 }, (_, i) => artist(i)), count: 900 }],
      { maxPerIsland: 10 },
    ],
    [
      'a group with no count falls back to the member count',
      [{ name: 'Rock', nodes: [artist(1), artist(2)] }],
      undefined,
    ],
    [
      'explicit nodeR/gap override the WATCHLIST_R defaults',
      [{ name: 'Rock', nodes: Array.from({ length: 15 }, (_, i) => artist(i)), count: 15 }],
      { nodeR: 10, gap: 2 },
    ],
    [
      'missing optional fields default rather than leak undefined',
      [{ name: 'Sparse', nodes: [{ name: 'Bare' }], count: 1 }],
      undefined,
    ],
    [
      'a node carrying an explicit type keeps it',
      [{ name: 'Rock', nodes: [artist(1, { type: 'watchlist' })], count: 1 }],
      undefined,
    ],
    // A count of ZERO is reported honestly rather than falling back to the
    // member tally — the test is `!= null`, not truthiness. A mutation to
    // `g.count ? …` survives every other case in this list.
    [
      'a group whose count is 0 keeps the 0',
      [{ name: 'Rock', nodes: [artist(1), artist(2)], count: 0 }],
      undefined,
    ],
    // The default cap is 300; without a group larger than 200 a mutation to
    // `|| 200` is invisible.
    [
      'a group over 200 members still places all of them under the default cap',
      [{ name: 'Rock', nodes: Array.from({ length: 250 }, (_, i) => artist(i)), count: 250 }],
      undefined,
    ],
    [
      'a group over the default cap is trimmed to 300',
      [{ name: 'Rock', nodes: Array.from({ length: 340 }, (_, i) => artist(i)), count: 340 }],
      undefined,
    ],
  ];

  for (const [label, groups, opts] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      if (opts === undefined) {
        artMapLayoutIslands(groups);
        V._artMapLayoutIslands(JSON.parse(JSON.stringify(groups)));
      } else {
        artMapLayoutIslands(groups, opts as Record<string, number>);
        V._artMapLayoutIslands(JSON.parse(JSON.stringify(groups)), opts);
      }
      expect(artMap.placed).toEqual(V._artMap.placed);
      expect(artMap._islands).toEqual(V._artMap._islands);
      expect(Object.keys(artMap._nodeById || {})).toEqual(Object.keys(V._artMap._nodeById || {}));
    });
  }
});

describe('artMapRemapEdges', () => {
  const placed = [
    { id: 0, _origId: 'a' },
    { id: 1, _origId: 'b' },
    { id: 2, _origId: 'c' },
    { id: 3, _origId: 'a' }, //  a duplicate original — the FIRST placement wins
    { id: 'label_Rock', name: 'Rock' }, // labels carry no _origId
  ] as unknown as ArtMapNode[];

  const cases: [string, unknown][] = [
    ['null edges', null],
    ['undefined edges', undefined],
    ['empty', []],
    ['a straightforward pair', [{ source: 'a', target: 'b', weight: 3 }]],
    ['weight defaults to 1', [{ source: 'a', target: 'b' }]],
    ['an unplaced endpoint drops the edge', [{ source: 'a', target: 'zzz' }]],
    ['a self-edge after remapping is dropped', [{ source: 'a', target: 'a' }]],
    [
      'two originals that map to the SAME placed node collapse to a self-edge and drop',
      [{ source: 'a', target: 'a' }],
    ],
    [
      'a mix',
      [
        { source: 'a', target: 'b', weight: 2 },
        { source: 'b', target: 'c' },
        { source: 'c', target: 'nope' },
      ],
    ],
  ];

  for (const [label, edges] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      sync({ placed });
      expect(artMapRemapEdges(edges as ArtMapEdge[])).toEqual(V._artMapRemapEdges(edges));
    });
  }

  it('maps a duplicated original id to its FIRST placement', () => {
    sync({ placed });
    // id 3 also carries _origId 'a'; both sides must pick 0, not 3.
    expect(artMapRemapEdges([{ source: 'a', target: 'b' }])).toEqual(
      V._artMapRemapEdges([{ source: 'a', target: 'b' }]),
    );
    expect(artMapRemapEdges([{ source: 'a', target: 'b' }])[0].source).toBe(0);
  });
});

describe('camera math', () => {
  const nodes = (spec: [number, number, number, number][]) =>
    spec.map(([x, y, radius, opacity], i) => ({
      id: i,
      name: `n${i}`,
      x,
      y,
      radius,
      opacity,
      type: 'similar',
      image_url: '',
    })) as ArtMapNode[];

  const worlds: [string, ArtMapNode[]][] = [
    ['one node', nodes([[0, 0, 50, 1]])],
    [
      'a spread',
      nodes([
        [-500, -300, 40, 1],
        [500, 300, 40, 1],
        [0, 0, 90, 1],
      ]),
    ],
    [
      'some faded out',
      nodes([
        [-500, -300, 40, 0],
        [500, 300, 40, 1],
        [0, 0, 90, 1],
      ]),
    ],
    [
      'ALL faded out — the two fits disagree here on purpose',
      nodes([
        [-500, -300, 40, 0],
        [500, 300, 40, 0],
      ]),
    ],
    [
      'a tall thin world',
      nodes([
        [0, -2000, 10, 1],
        [0, 2000, 10, 1],
      ]),
    ],
    // A WIDE flat world is the only shape where the horizontal margin decides
    // the zoom; in every other case above the vertical span or the 1.0 cap wins,
    // so a changed `mapW` margin is invisible.
    [
      'a wide flat world',
      nodes([
        [-3000, 0, 10, 1],
        [3000, 0, 10, 1],
      ]),
    ],
  ];

  for (const [label, placed] of worlds) {
    it(`artMapFitToContent matches the vanilla — ${label}`, () => {
      sync({ placed, width: 1400, height: 800 });
      artMapFitToContent();
      V._artMapFitToContent();
      expect([artMap.zoom, artMap.offsetX, artMap.offsetY]).toEqual([
        V._artMap.zoom,
        V._artMap.offsetX,
        V._artMap.offsetY,
      ]);
    });

    it(`artMapFitToViewTarget matches the vanilla — ${label}`, () => {
      sync({ placed, width: 1400, height: 800, zoom: 0.4, offsetX: 12, offsetY: 34 });
      const mine = artMapFitToViewTarget();
      V.artMapFitToView();
      const theirs = V.animateToCalls[0] ?? null;
      expect(mine).toEqual(theirs);
    });
  }

  it('artMapFitToContent leaves the camera alone when nothing is placed', () => {
    sync({ placed: [], width: 1400, height: 800, zoom: 0.42, offsetX: 7, offsetY: 9 });
    artMapFitToContent();
    V._artMapFitToContent();
    expect([artMap.zoom, artMap.offsetX, artMap.offsetY]).toEqual([
      V._artMap.zoom,
      V._artMap.offsetX,
      V._artMap.offsetY,
    ]);
    expect(artMap.zoom).toBe(0.42);
  });

  it('artMapFitToViewTarget returns null with nothing placed, and the vanilla animates nothing', () => {
    sync({ placed: [], width: 1400, height: 800 });
    expect(artMapFitToViewTarget()).toBeNull();
    V.artMapFitToView();
    expect(V.animateToCalls).toHaveLength(0);
  });

  it('artMapFitToContent floors the usable width at 200 when the panel eats the viewport', () => {
    // On a narrow desktop the 320px panel reserves MORE than the container is
    // wide, so the subtraction goes negative and the 200px floor is what keeps
    // the zoom finite and positive.
    sync({ placed: worlds[1][1], width: 300, height: 800 });
    artMapFitToContent();
    V._artMapFitToContent();
    expect([artMap.zoom, artMap.offsetX, artMap.offsetY]).toEqual([
      V._artMap.zoom,
      V._artMap.offsetX,
      V._artMap.offsetY,
    ]);
    expect(artMap.offsetX).toBe(100); //  200 / 2, centred on a world centred at 0
  });

  it('artMapFitToContent honours a custom margin', () => {
    sync({ placed: worlds[1][1], width: 1400, height: 800 });
    artMapFitToContent(0);
    V._artMapFitToContent(0);
    expect([artMap.zoom, artMap.offsetX, artMap.offsetY]).toEqual([
      V._artMap.zoom,
      V._artMap.offsetX,
      V._artMap.offsetY,
    ]);
  });

  const zoomCases: [number, number][] = [
    [1.3, 0.15],
    [0.7, 0.15],
    [1.3, 2.9], //   clamps at 3
    [0.7, 0.021], // clamps at 0.02
    [100, 1],
    [0.0001, 1],
    [1, 0.5], //     a no-op factor must not drift the offsets
  ];
  for (const [factor, zoom] of zoomCases) {
    it(`artMapZoomTarget matches the vanilla for factor=${factor} @ zoom=${zoom}`, () => {
      sync({ width: 1400, height: 800, zoom, offsetX: 120, offsetY: -60 });
      const mine = artMapZoomTarget(factor);
      V.artMapZoom(factor);
      expect(mine).toEqual(V.animateToCalls[0]);
    });
  }

  const islands: ArtMapIsland[] = [
    { name: 'Rock', cx: 0, cy: 0, r: 500, hue: 10, count: 3 },
    { name: 'Jazz', cx: 2000, cy: -800, r: 90, hue: 200, count: 2 },
    { name: 'Tiny', cx: -50, cy: 40, r: 5, hue: 300, count: 1 },
  ];
  islands.forEach((isl, i) => {
    it(`artMapIslandCamera matches the vanilla for island ${isl.name}`, () => {
      const placed = [
        {
          id: 0,
          name: 'n',
          x: isl.cx,
          y: isl.cy,
          radius: 10,
          opacity: 1,
          type: 'similar',
          image_url: '',
          _island: isl.name,
        },
      ] as ArtMapNode[];
      sync({ placed, _islands: islands, width: 1400, height: 800 });
      const mine = artMapIslandCamera(isl);
      V._artMapFocusIsland(i, { bloom: false });
      expect(mine).toEqual({
        zoom: V._artMap.zoom,
        offsetX: V._artMap.offsetX,
        offsetY: V._artMap.offsetY,
      });
    });
  });

  it('reserves the panel width off-mobile and nothing on mobile', () => {
    expect(artMapIsMobile()).toBe(V._artMapIsMobile());
    expect(artMapReservedW()).toBe(V._artMapReservedW());
  });
});

describe('artMapScreenToWorld', () => {
  const canvas = { getBoundingClientRect: () => ({ left: 20, top: 60 }) };
  const cases: [number, number, number, number, number][] = [
    [0, 0, 1, 0, 0],
    [100, 200, 1, 0, 0],
    [100, 200, 0.5, 50, -30],
    [100, 200, 2, -400, 250],
    [-40, -80, 0.15, 700, 400],
  ];
  for (const [clientX, clientY, zoom, offsetX, offsetY] of cases) {
    it(`matches the vanilla at (${clientX},${clientY}) z=${zoom}`, () => {
      sync({ zoom, offsetX, offsetY });
      expect(artMapScreenToWorld({ clientX, clientY }, canvas)).toEqual(
        V._artMapScreenToWorld({ clientX, clientY }, canvas),
      );
    });
  }
});

describe('artMapHitTest', () => {
  const placed = [
    { id: 0, name: 'far', x: 1000, y: 1000, radius: 50, opacity: 1, type: 'similar' },
    { id: 1, name: 'faint', x: 0, y: 0, radius: 60, opacity: 0.29, type: 'similar' },
    { id: 2, name: 'similar-a', x: 0, y: 0, radius: 60, opacity: 1, type: 'similar' },
    { id: 3, name: 'similar-b', x: 5, y: 5, radius: 60, opacity: 1, type: 'similar' },
    { id: 4, name: 'watched', x: 10, y: 10, radius: 60, opacity: 1, type: 'watchlist' },
  ] as unknown as ArtMapNode[];

  const cases: [string, number, number][] = [
    ['dead centre, where a watchlist node also overlaps', 0, 0],
    ['inside only the far node', 1000, 1000],
    ['empty water', -5000, -5000],
    ['exactly on a rim', 60, 0],
    ['one pixel outside a rim', 61, 0],
  ];
  for (const [label, wx, wy] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      sync({ placed });
      expect(artMapHitTest(wx, wy)).toEqual(V._artMapHitTest(wx, wy));
    });
  }

  it('lets a watchlist node win a tie over an earlier similar one', () => {
    sync({ placed });
    expect(artMapHitTest(0, 0)?.name).toBe('watched');
  });

  it('counts a point exactly ON the rim as a hit', () => {
    // The comparison is `<=`. With the full fixture a rim point still lands
    // inside the overlapping watchlist bubble, so the boundary only shows with a
    // single isolated node — a `<` mutation survives otherwise.
    const lone = [
      { id: 9, name: 'lone', x: 0, y: 0, radius: 60, opacity: 1, type: 'similar' },
    ] as unknown as ArtMapNode[];
    sync({ placed: lone });
    expect(artMapHitTest(60, 0)).toEqual(V._artMapHitTest(60, 0));
    expect(artMapHitTest(60, 0)?.name).toBe('lone');
    expect(artMapHitTest(60.001, 0)).toEqual(V._artMapHitTest(60.001, 0));
    expect(artMapHitTest(60.001, 0)).toBeNull();
  });

  it('ignores a bubble faded below 0.3 even though the renderer still draws it', () => {
    sync({ placed: [placed[1]] });
    expect(artMapHitTest(0, 0)).toEqual(V._artMapHitTest(0, 0));
    expect(artMapHitTest(0, 0)).toBeNull();
  });
});

describe('node identity + watch state', () => {
  const cases: unknown[] = [
    null,
    undefined,
    {},
    { spotify_id: 'sp' },
    { itunes_id: 'it' },
    { deezer_id: 'dz' },
    { discogs_id: 'dc' },
    { musicbrainz_id: 'mb' },
    { spotify_id: '', itunes_id: 'it' }, //  empty strings are skipped
    { spotify_id: 'sp', itunes_id: 'it', deezer_id: 'dz' },
    { musicbrainz_id: 'mb', discogs_id: 'dc' },
  ];
  cases.forEach((n, i) => {
    it(`artMapNodeBest matches the vanilla for case ${i}`, () => {
      expect(artMapNodeBest(n as ArtMapNode)).toEqual(V._artMapNodeBest(n));
    });
  });

  const edges = [
    { source: 1, target: 2 },
    { source: 2, target: 3 },
    { source: 3, target: 1 },
    { source: 1, target: 1 },
  ] as ArtMapEdge[];
  for (const id of [1, 2, 3, 4]) {
    it(`artMapConnCount matches the vanilla for node ${id}`, () => {
      sync({ edges });
      const n = { id } as ArtMapNode;
      expect(artMapConnCount(n)).toBe(V._artMapConnCount(n));
    });
  }

  it('artMapConnCount counts a self-edge ONCE — the test is `source || target`', () => {
    sync({ edges });
    // Node 1 sits on 1→2, 3→1 and the self-edge 1→1. The self-edge satisfies
    // both halves of the or, but the loop increments once per edge, so it is
    // three connections rather than four.
    expect(artMapConnCount({ id: 1 } as ArtMapNode)).toBe(3);
  });

  const watchCases: [string, unknown, string[] | null][] = [
    ['null node', null, null],
    ['a watchlist-typed node with no ids at all', { type: 'watchlist' }, null],
    ['a similar node not in the set', { type: 'similar', spotify_id: 'sp' }, []],
    ['a similar node IN the set', { type: 'similar', spotify_id: 'sp' }, ['sp']],
    ['the set holds a DIFFERENT source id', { type: 'similar', spotify_id: 'sp' }, ['it']],
    ['no id and an empty set', { type: 'similar' }, []],
    ['no watch set at all', { type: 'similar', spotify_id: 'sp' }, null],
  ];
  for (const [label, node, set] of watchCases) {
    it(`artMapIsWatched matches the vanilla — ${label}`, () => {
      sync({ _watchSet: set ? new Set(set) : undefined });
      expect(artMapIsWatched(node as ArtMapNode)).toBe(V._artMapIsWatched(node));
    });
  }
});

describe('artMapIsLiveSize', () => {
  const cases: [string, unknown, Partial<ArtMapState>][] = [
    ['a label is never live', { _isLabel: true, radius: 9999 }, { zoom: 1 }],
    [
      'overflow forces everything into the buffer',
      { radius: 9999 },
      { _liveOverflow: true, zoom: 1 },
    ],
    ['exactly at the threshold', { radius: 12 }, { zoom: 1 }],
    ['just under the threshold', { radius: 11.9 }, { zoom: 1 }],
    ['a missing radius reads as 0', {}, { zoom: 1 }],
    [
      'the BUILD zoom decides, not the live zoom',
      { radius: 20 },
      { zoom: 0.001, _liveBuildZoom: 1 },
    ],
    [
      'a zero build zoom falls back to the live zoom',
      { radius: 20 },
      { zoom: 1, _liveBuildZoom: 0 },
    ],
  ];
  for (const [label, node, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      sync(state);
      expect(artMapIsLiveSize(node as ArtMapNode)).toBe(V._artMapIsLiveSize(node));
    });
  }
});

describe('artMapStepAnimations', () => {
  const revealing = (): ArtMapNode[] =>
    [
      { id: 0, aScale: 0, aAlpha: 0, _revealAt: 100, _revealDur: 500, _riseAmp: 30 },
      { id: 1, aScale: 0, aAlpha: 0, _revealAt: 400, _revealDur: 500, _riseAmp: 30 },
      { id: 2, aScale: 1, aAlpha: 1, _revealAt: 0, _revealDur: 500 },
      { id: 3 }, // never animated — aScale is undefined
    ] as unknown as ArtMapNode[];

  const cases: [string, number, Partial<ArtMapState>][] = [
    ['before anything starts', 0, {}],
    ['mid-bloom for the first node only', 300, {}],
    ['both mid-bloom', 500, {}],
    ['the first has landed', 650, {}],
    ['everything has landed', 2000, {}],
    ['exactly at a start time', 100, {}],
    ['exactly at the end of a node', 600, {}],
    [
      'a live ripple keeps it active',
      2000,
      { _ripples: [{ cx: 0, cy: 0, hue: 1, maxR: 10, t0: 1900, dur: 500 }] },
    ],
    [
      'an expired ripple is swept',
      2000,
      { _ripples: [{ cx: 0, cy: 0, hue: 1, maxR: 10, t0: 0, dur: 500 }] },
    ],
    ['a settled map still in reveal mode owes one more frame', 5000, { _revealing: true }],
    ['a settled map NOT in reveal mode is done', 5000, { _revealing: false }],
    ['_revealDur missing falls back to 480', 400, {}],
  ];

  for (const [label, t, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const mine = revealing();
      const theirs = revealing();
      if (label.includes('480')) {
        delete mine[0]._revealDur;
        delete theirs[0]._revealDur;
      }
      sync({ placed: mine, dirty: false, ...state });
      // sync() deep-copies arrays, so hand the vanilla its own live objects.
      V._artMap.placed = theirs;
      const a = artMapStepAnimations(t);
      const b = V._artMapStepAnimations(t);
      expect(a).toBe(b);
      expect(mine).toEqual(theirs);
      expect(artMap._ripples).toEqual(V._artMap._ripples);
      expect(artMap._revealing).toBe(V._artMap._revealing);
      expect(artMap.dirty).toBe(V._artMap.dirty);
    });
  }
});

describe('artMapNodeDisplacement', () => {
  const push = (over: Partial<ArtMapRipple> = {}): ArtMapRipple => ({
    cx: 0,
    cy: 0,
    hue: 270,
    maxR: 832,
    t0: 1000,
    dur: 900,
    push: 70.4,
    width: 192,
    ...over,
  });

  const cases: [string, ArtMapRipple[] | null, number, { x: number; y: number }][] = [
    ['no ripples', null, 1200, { x: 100, y: 0 }],
    ['an empty list', [], 1200, { x: 100, y: 0 }],
    ['a ripple with no push does nothing', [push({ push: undefined })], 1200, { x: 100, y: 0 }],
    ['before the ripple starts', [push()], 900, { x: 100, y: 0 }],
    ['after it ends', [push()], 2500, { x: 100, y: 0 }],
    ['riding the wavefront', [push()], 1300, { x: 300, y: 0 }],
    ['far outside the front', [push()], 1300, { x: 5000, y: 0 }],
    ['dead centre — the hypot guard', [push()], 1300, { x: 0, y: 0 }],
    ['a diagonal node', [push()], 1300, { x: 200, y: 200 }],
    [
      'no explicit width falls back to maxR*0.2',
      [push({ width: undefined })],
      1300,
      { x: 300, y: 0 },
    ],
    ['two ripples accumulate', [push(), push({ cx: 400, t0: 1100 })], 1400, { x: 200, y: 50 }],
  ];

  for (const [label, ripples, now, pos] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      sync({ _ripples: ripples, _now: now });
      const n = { x: pos.x, y: pos.y } as ArtMapNode;
      expect(artMapNodeDisplacement(n)).toEqual(V._artMapNodeDisplacement(n));
    });
  }
});

describe('image sizing', () => {
  for (const px of [null, undefined, 0, 1, 111, 112, 113, 144, 383, 384, 385, 5000, 200.4, 200.5]) {
    it(`artMapImgPx matches the vanilla for ${px}`, () => {
      expect(artMapImgPx(px)).toBe(V._artMapImgPx(px));
    });
  }

  const nodes: unknown[] = [
    {},
    { radius: 70.4, type: 'similar' },
    { radius: 70.4, type: 'watchlist' },
    { radius: 70.4, type: 'center' },
    { radius: 70.4, type: 'similar', ring: 1 },
    { radius: 70.4, type: 'similar', ring: 2 },
    { radius: 300, type: 'similar' },
    { radius: 300, type: 'watchlist' },
    { radius: 1000, type: 'watchlist' },
    { type: 'watchlist' },
  ];
  nodes.forEach((n, i) => {
    it(`artMapNodeImgPx matches the vanilla for case ${i}`, () => {
      expect(artMapNodeImgPx(n as ArtMapNode)).toBe(V._artMapNodeImgPx(n));
    });
  });
});
