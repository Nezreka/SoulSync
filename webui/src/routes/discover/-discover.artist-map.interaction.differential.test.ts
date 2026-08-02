import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { loadVanilla } from '../../test/vanilla-extract';
import { type ArtMapNode, type ArtMapState, artMap } from './-discover.artist-map';
import {
  ARTMAP_CONSTELLATION_MS,
  ARTMAP_PANEL_HOVER_MS,
  ARTMAP_RESIZE_MS,
  ARTMAP_ZOOM_REBUILD_MS,
  type ArtMapInteractionHost,
  artMapKeyAction,
  artMapKeyPreventsDefault,
  artMapPinchZoom,
  artMapWasDrag,
  artMapWasTouchDrag,
  artMapWheelZoom,
  attachArtMapInteraction,
} from './-discover.artist-map.interaction';

/**
 * Differential parity for the Artist Map's interaction layer.
 *
 * Both sides get a real jsdom canvas, the SAME synthetic events, and a shared
 * recorder standing in for everything downstream (render, ripple, tooltip,
 * panel, close, …). Comparing the two effect logs is what proves a gesture
 * produces the same consequences in the same order — a hover that renders twice,
 * or a click that ripples before it pins the card, shows up immediately.
 *
 * The resulting camera state is compared too, so the zoom arithmetic is pinned
 * alongside the sequencing.
 */

const effects: string[] = [];
const record =
  (name: string) =>
  (...args: unknown[]) => {
    effects.push(args.length ? `${name}(${args.map(fmtArg).join(',')})` : name);
  };

function fmtArg(v: unknown): string {
  if (v == null) return String(v);
  if (typeof v === 'object') {
    const n = v as ArtMapNode;
    if (n.id !== undefined) return `node:${String(n.id)}`;
    const e = v as { clientX?: number; clientY?: number };
    if (e.clientX !== undefined) return `evt(${e.clientX},${e.clientY})`;
    return '[obj]';
  }
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(4);
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  return typeof v === 'string' ? v : typeof v;
}

const PREAMBLE = `
const _artMap = {
  placed: [], edges: [], images: {},
  canvas: null, ctx: null, offscreen: null, offCtx: null,
  width: 1400, height: 800, offsetX: 700, offsetY: 400, zoom: 1,
  hoveredNode: null, animFrame: null, dirty: false,
  WATCHLIST_R: 320, BUFFER: 8, MAX_BUFFER_PX: 4096, LIVE_PX: 12,
  _anim: { running: false, raf: null, last: 0 },
  _fieldAlpha: 1, _revealT0: 0, _panelW: 320,
};
const effects = [];
function fmtArg(v) {
  if (v == null) return String(v);
  if (typeof v === 'object') {
    if (v.id !== undefined) return 'node:' + String(v.id);
    if (v.clientX !== undefined) return 'evt(' + v.clientX + ',' + v.clientY + ')';
    return '[obj]';
  }
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(4);
  return String(v);
}
function rec(name) {
  return function () {
    const args = Array.prototype.slice.call(arguments);
    effects.push(args.length ? name + '(' + args.map(fmtArg).join(',') + ')' : name);
  };
}
const _artMapRender = rec('render');
const _artMapEnsureAmbient = rec('ensureAmbient');
const _artMapEmitRipple = rec('emitRipple');
const _artMapShowTooltip = rec('showTooltip');
const _artMapPanelArtist = rec('showPanelArtist');
const _artMapAnimateConstellation = rec('animateConstellation');
const _artMapHideContextMenu = rec('hideContextMenu');
const closeArtistMap = rec('close');
const artMapZoom = rec('zoom');
const artMapFitToView = rec('fitToView');
const _artMapIslandNav = rec('islandNav');
const _artMapEnsurePanel = function () {};
const _artMapFocusIsland = rec('focusIsland');
const _artMapFitToContent = rec('fitToContent');
const _artMapRefreshPanel = rec('refreshPanel');
function buildArtistDetailPath(id, source) { return '/artist-detail/' + source + '/' + id; }
const escapeForInlineJs = (s) => String(s);
function openYourArtistInfoModal_direct() {}
function toggleYourArtistWatchlist() {}
`;

interface Vanilla {
  _artMap: ArtMapState;
  effects: string[];
  _artMapSetupInteraction: (canvas: HTMLCanvasElement) => void;
  _artMapScreenToWorld: (e: unknown, c: unknown) => { nx: number; ny: number };
  _artMapHitTest: (wx: number, wy: number) => ArtMapNode | null;
}

const V = loadVanilla<Vanilla>(
  ['_artMapSetupInteraction', '_artMapScreenToWorld', '_artMapHitTest'],
  PREAMBLE,
  ['_artMap', 'effects'],
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
  dirty: false,
  hoveredNode: null,
  _oneIsland: undefined,
  _hideSimilar: undefined,
  _perf: undefined,
  _constellationActive: undefined,
  _constellationFade: undefined,
  _constellationCache: undefined,
};

function seed(target: ArtMapState, state: Partial<ArtMapState>) {
  const merged = { ...BASE, ...state };
  for (const k of Object.keys(merged)) {
    const v = (merged as Record<string, unknown>)[k];
    (target as Record<string, unknown>)[k] = Array.isArray(v) ? structuredClone(v) : v;
  }
}

/**
 * My side answers "am I visible" from a flag rather than the DOM.
 *
 * This is not a shortcut — it is what keeps the two phases apart. The vanilla's
 * `window` keydown/resize listeners can never be removed, so they also fire
 * while MY side is being driven. Removing `#artist-map-container` for my phase
 * makes every one of them bail at its own visibility guard, which is the only
 * way to observe my handlers in isolation.
 */
let mineVisible = true;

const host: ArtMapInteractionHost = {
  isVisible: () => mineVisible,
  render: record('render'),
  ensureAmbient: record('ensureAmbient'),
  emitRipple: record('emitRipple'),
  showTooltip: record('showTooltip'),
  showPanelArtist: record('showPanelArtist'),
  animateConstellation: record('animateConstellation'),
  showContextMenu: (_e, node) => record('showContextMenu')(node),
  hideContextMenu: record('hideContextMenu'),
  close: record('close'),
  zoom: record('zoom'),
  fitToView: record('fitToView'),
  focusSearch: () => {
    // Mirrors the vanilla: only a real input counts, and it really is focused
    // (10026-10029).
    const input = document.getElementById('artist-map-search') as HTMLInputElement | null;
    if (!input) return false;
    input.focus();
    record('focusSearch')();
    return true;
  },
  toggleSimilar: record('toggleSimilar'),
  islandNav: record('islandNav'),
  resized: record('resized'),
};

/** A canvas whose rect is at the origin, so client coords are canvas coords. */
function makeCanvas(): HTMLCanvasElement {
  const c = document.createElement('canvas');
  c.getBoundingClientRect = () =>
    ({
      left: 0,
      top: 0,
      width: 1400,
      height: 800,
      right: 1400,
      bottom: 800,
      x: 0,
      y: 0,
    }) as DOMRect;
  document.body.appendChild(c);
  return c;
}

/** The keyboard handler is gated on the container being present and visible. */
function mountContainer() {
  if (document.getElementById('artist-map-container')) return;
  const container = document.createElement('div');
  container.id = 'artist-map-container';
  container.style.display = 'flex';
  document.body.appendChild(container);
}

/** The toolbar search box, which the 's' shortcut needs in order to do anything. */
function mountSearchInput() {
  if (document.getElementById('artist-map-search')) return;
  const input = document.createElement('input');
  input.id = 'artist-map-search';
  document.body.appendChild(input);
}

/**
 * ONE vanilla canvas for the whole suite, wired ONCE.
 *
 * `_artMapSetupInteraction` guards re-entry with a flag on the canvas ELEMENT,
 * but its `window` keydown/resize and `document` visibilitychange listeners are
 * never removed. In the real app that is harmless — index.html declares the
 * canvas once, so the guard makes every reopen a no-op. Handing the vanilla a
 * fresh canvas per test defeats the guard and stacks a listener set per test,
 * which is what made the first run report ten 'close' effects for one Escape.
 * Reusing one canvas reproduces the app's actual lifecycle.
 */
const vanillaCanvas = makeCanvas();
V._artMapSetupInteraction(vanillaCanvas);

/**
 * Clear the gesture state the vanilla holds in its setup closure.
 *
 * `isPanning`, `clickStart` and `lastTouches` were captured once and outlive
 * every test. A panning case that ends without a mouseup leaves `isPanning`
 * true, and the next case's mousemove silently takes the pan branch instead of
 * the hover branch — which is exactly how "ignores a non-left button" first
 * reported a camera that had moved.
 */
function resetVanillaGestures() {
  vanillaCanvas.dispatchEvent(new MouseEvent('mouseup', { button: 0, clientX: 0, clientY: 0 }));
  vanillaCanvas.dispatchEvent(touchEvent('touchend', [], []));
}

function touch(clientX: number, clientY: number) {
  return { clientX, clientY } as Touch;
}

function touchEvent(type: string, touches: Touch[], changed: Touch[] = touches) {
  // jsdom has no TouchEvent constructor, so the lists are plain arrays. The
  // handlers only spread them and read `.length`/`[0]`, which arrays satisfy.
  const e = new Event(type, { bubbles: true, cancelable: true }) as unknown as Record<
    string,
    unknown
  >;
  e.touches = touches;
  e.changedTouches = changed;
  return e as unknown as TouchEvent;
}

/**
 * Drive the same gesture against both sides and return the two effect logs plus
 * the two resulting camera states.
 */
function bothEffects(
  state: Partial<ArtMapState>,
  drive: (canvas: HTMLCanvasElement) => void,
): {
  vanilla: string[];
  mine: string[];
  vanillaCam: number[];
  mineCam: number[];
} {
  // Vanilla — the shared canvas, re-attached to a clean body.
  document.body.innerHTML = '';
  mountContainer();
  document.body.appendChild(vanillaCanvas);
  resetVanillaGestures();
  V.effects.length = 0;
  seed(V._artMap, state);
  drive(vanillaCanvas);
  const vanilla = [...V.effects];
  const vanillaCam = [V._artMap.zoom, V._artMap.offsetX, V._artMap.offsetY];

  // Mine — a fresh canvas each time, disposed after; that is the whole point of
  // returning a dispose, and it is why my side cannot stack the way the vanilla
  // would if its guard were defeated. The container is deliberately NOT mounted,
  // so the vanilla's undetachable window listeners all bail.
  document.body.innerHTML = '';
  effects.length = 0;
  seed(artMap, state);
  const mc = makeCanvas();
  const dispose = attachArtMapInteraction(mc, host);
  let mine: string[];
  let mineCam: number[];
  try {
    drive(mc);
  } finally {
    // A failing expectation inside `drive` must not leak the listener set into
    // every later test — that turned one wheel-timing failure into five.
    mine = [...effects];
    mineCam = [artMap.zoom, artMap.offsetX, artMap.offsetY];
    dispose();
  }

  return { vanilla, mine, vanillaCam, mineCam };
}

const node = (over: Partial<ArtMapNode> = {}): ArtMapNode =>
  ({
    id: 1,
    name: 'A',
    x: 0,
    y: 0,
    radius: 60,
    opacity: 1,
    type: 'similar',
    image_url: '',
    _hue: 200,
    ...over,
  }) as ArtMapNode;

/** A bubble sitting under screen (700,400) given offset 700/400 and zoom 1. */
const centreNode = node({ id: 7, x: 0, y: 0, radius: 60 });

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = '';
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// ── Zoom arithmetic ──────────────────────────────────────────────────────────

describe('artMapWheelZoom', () => {
  const cases: [number, number, number, Partial<ArtMapState>][] = [
    [-100, 700, 400, {}],
    [100, 700, 400, {}],
    [-100, 0, 0, {}],
    [-100, 1400, 800, {}],
    [-100, 700, 400, { zoom: 4.9 }], //  clamps at 5, not 3 — the wheel goes further than the buttons
    [100, 700, 400, { zoom: 0.021 }], // clamps at 0.02
    [0, 700, 400, {}], //                a zero delta reads as "up"
  ];
  for (const [deltaY, mx, my, state] of cases) {
    it(`matches the vanilla for delta=${deltaY} at (${mx},${my}) zoom=${state.zoom ?? 1}`, () => {
      const { vanillaCam, mineCam } = bothEffects(state, (canvas) => {
        const e = new Event('wheel', { bubbles: true, cancelable: true }) as WheelEvent & {
          deltaY: number;
          clientX: number;
          clientY: number;
        };
        e.deltaY = deltaY;
        e.clientX = mx;
        e.clientY = my;
        canvas.dispatchEvent(e);
      });
      expect(mineCam).toEqual(vanillaCam);
    });
  }

  it('lets the wheel past the toolbar buttons’ ceiling of 3', () => {
    seed(artMap, { zoom: 2.9 });
    expect(artMapWheelZoom(-1, 0, 0).zoom).toBeCloseTo(3.19, 6);
  });

  it('keeps the point under the cursor fixed', () => {
    seed(artMap, { zoom: 1, offsetX: 700, offsetY: 400 });
    const worldBefore = (300 - artMap.offsetX) / artMap.zoom;
    const next = artMapWheelZoom(-1, 300, 0);
    const worldAfter = (300 - next.offsetX) / next.zoom;
    expect(worldAfter).toBeCloseTo(worldBefore, 9);
  });
});

describe('artMapPinchZoom', () => {
  it('anchors on the midpoint and clamps at 3, unlike the wheel', () => {
    seed(artMap, { zoom: 2.9, offsetX: 700, offsetY: 400 });
    const out = artMapPinchZoom([touch(0, 0), touch(100, 0)], [touch(0, 0), touch(300, 0)]);
    expect(out.zoom).toBe(3);
  });

  it('matches the vanilla over a pinch gesture', () => {
    const { vanillaCam, mineCam, vanilla, mine } = bothEffects({}, (canvas) => {
      canvas.dispatchEvent(touchEvent('touchstart', [touch(100, 100), touch(200, 100)]));
      canvas.dispatchEvent(touchEvent('touchmove', [touch(80, 100), touch(260, 100)]));
    });
    expect(mineCam).toEqual(vanillaCam);
    expect(mine).toEqual(vanilla);
    // A pinch rebuilds the buffer every step rather than debouncing like the
    // wheel does — transcribed as-is, and only visible in `dirty`.
    expect(artMap.dirty).toBe(V._artMap.dirty);
    expect(artMap.dirty).toBe(true);
  });
});

// ── Keyboard ─────────────────────────────────────────────────────────────────

describe('the keyboard shortcuts', () => {
  // 's'/'S' are covered separately: the vanilla focuses a real input rather than
  // calling any collaborator, so there is nothing for the recorder to see.
  const keys = [
    'Escape',
    '=',
    '+',
    '-',
    '0',
    'f',
    'F',
    'd',
    'D',
    'h',
    'H',
    'ArrowLeft',
    'ArrowRight',
    'q',
  ];

  for (const oneIsland of [false, true]) {
    for (const key of keys) {
      it(`matches the vanilla for "${key}" (oneIsland=${oneIsland})`, () => {
        const { vanilla, mine } = bothEffects({ _oneIsland: oneIsland }, () => {
          mountSearchInput();
          window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
        });
        expect(mine).toEqual(vanilla);
      });
    }
  }

  for (const key of ['s', 'S']) {
    it(`focuses the search box on "${key}", exactly as the vanilla does`, () => {
      const focused: string[] = [];
      bothEffects({}, () => {
        mountSearchInput();
        window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
        focused.push((document.activeElement as HTMLElement)?.id || '');
      });
      // Both phases must have landed focus on the same element.
      expect(focused).toEqual(['artist-map-search', 'artist-map-search']);
    });
  }

  it('leaves "s" alone when there is no search box to focus', () => {
    // The vanilla's preventDefault lives INSIDE `if (input)`, so with no input
    // the key still types an s. Nothing is focused on either side.
    document.body.innerHTML = '';
    seed(artMap, {});
    const c = makeCanvas();
    const dispose = attachArtMapInteraction(c, host);
    effects.length = 0;
    const e = new KeyboardEvent('keydown', { key: 's', bubbles: true, cancelable: true });
    window.dispatchEvent(e);
    expect(effects).toEqual([]);
    expect(e.defaultPrevented).toBe(false);
    dispose();
  });

  it('never intercepts typing in an input', () => {
    expect(artMapKeyAction('f', 'INPUT', true)).toBeNull();
    expect(artMapKeyAction('Escape', 'INPUT', true)).toBeNull();
    // …but a textarea is NOT excluded, matching the vanilla's `tagName === 'INPUT'`.
    expect(artMapKeyAction('f', 'TEXTAREA', true)).toBe('fit');
  });

  it('leaves the arrows alone outside one-island mode', () => {
    expect(artMapKeyAction('ArrowLeft', undefined, false)).toBeNull();
    expect(artMapKeyAction('ArrowLeft', undefined, true)).toBe('island-prev');
  });

  it('lets "h" and the arrows through without preventDefault', () => {
    // Every other branch consumes the event; these are the exceptions.
    expect(artMapKeyPreventsDefault('toggle-similar')).toBe(false);
    expect(artMapKeyPreventsDefault(null)).toBe(false);
    expect(artMapKeyPreventsDefault('close')).toBe(true);
    expect(artMapKeyPreventsDefault('perf')).toBe(true);
  });

  it('toggles the same flags the vanilla did', () => {
    bothEffects({}, () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'h' }));
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'd' }));
    });
    expect(artMap._hideSimilar).toBe(V._artMap._hideSimilar);
    expect(artMap._perf).toBe(V._artMap._perf);
    expect(artMap.dirty).toBe(V._artMap.dirty);
  });
});

// ── Pan, hover, click ────────────────────────────────────────────────────────

describe('panning', () => {
  it('moves the camera by the pointer delta', () => {
    const { vanillaCam, mineCam, vanilla, mine } = bothEffects({}, (canvas) => {
      canvas.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 100, clientY: 100 }));
      canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: 140, clientY: 130 }));
      canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: 150, clientY: 120 }));
    });
    expect(mineCam).toEqual(vanillaCam);
    expect(mine).toEqual(vanilla);
    expect(artMap.offsetX).toBe(750);
  });

  it('ignores a non-left button', () => {
    const { vanillaCam, mineCam } = bothEffects({}, (canvas) => {
      canvas.dispatchEvent(new MouseEvent('mousedown', { button: 2, clientX: 100, clientY: 100 }));
      canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: 300, clientY: 300 }));
    });
    expect(mineCam).toEqual(vanillaCam);
  });
});

describe('hovering', () => {
  it('matches the vanilla over a hover, both debounces, and a leave', () => {
    const drive = (canvas: HTMLCanvasElement) => {
      canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: 700, clientY: 400 }));
      vi.advanceTimersByTime(ARTMAP_CONSTELLATION_MS);
      vi.advanceTimersByTime(ARTMAP_PANEL_HOVER_MS);
      canvas.dispatchEvent(new MouseEvent('mouseleave'));
    };
    const { vanilla, mine } = bothEffects({ placed: [centreNode] }, drive);
    expect(mine).toEqual(vanilla);
    expect(mine.join('\n')).toContain('showPanelArtist(node:7)');
  });

  it('drops the panel swap when the pointer moves on before the debounce', () => {
    const drive = (canvas: HTMLCanvasElement) => {
      canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: 700, clientY: 400 }));
      vi.advanceTimersByTime(300);
      canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: 20, clientY: 20 }));
      vi.advanceTimersByTime(ARTMAP_PANEL_HOVER_MS);
    };
    const { vanilla, mine } = bothEffects({ placed: [centreNode] }, drive);
    expect(mine).toEqual(vanilla);
    expect(mine.join('\n')).not.toContain('showPanelArtist');
  });

  it('suppresses the panel swap for a bubble you have already left', () => {
    // mouseleave clears the CONSTELLATION timer but not the panel one (10162-10171),
    // so the panel timer still fires ~800ms after the pointer left the canvas.
    // The `hoveredNode === target` guard is what stops it showing a card for a
    // bubble that is no longer hovered — the only case where that guard bites.
    const drive = (canvas: HTMLCanvasElement) => {
      canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: 700, clientY: 400 }));
      canvas.dispatchEvent(new MouseEvent('mouseleave'));
      vi.advanceTimersByTime(ARTMAP_PANEL_HOVER_MS + 50);
    };
    const { vanilla, mine } = bothEffects({ placed: [centreNode] }, drive);
    expect(mine).toEqual(vanilla);
    expect(mine.join('\n')).not.toContain('showPanelArtist');
  });

  it('resets the constellation fade to 0 when it re-arms', () => {
    const drive = (canvas: HTMLCanvasElement) => {
      canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: 700, clientY: 400 }));
      vi.advanceTimersByTime(ARTMAP_CONSTELLATION_MS + 10);
    };
    bothEffects({ placed: [centreNode], _constellationFade: 0.7 }, drive);
    expect(artMap._constellationFade).toBe(V._artMap._constellationFade);
    expect(artMap._constellationFade).toBe(0);
    expect(artMap._constellationActive).toBe(true);
    expect(artMap._constellationCache).toBeNull();
  });

  it('clears the hovered node on leave', () => {
    const drive = (canvas: HTMLCanvasElement) => {
      canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: 700, clientY: 400 }));
      canvas.dispatchEvent(new MouseEvent('mouseleave'));
    };
    bothEffects({ placed: [centreNode] }, drive);
    expect(artMap.hoveredNode).toBe(V._artMap.hoveredNode);
    expect(artMap.hoveredNode).toBeNull();
  });

  it('sets the cursor to a pointer over a bubble and a grab over water', () => {
    document.body.innerHTML = '';
    seed(artMap, { placed: [centreNode] });
    const c = makeCanvas();
    const dispose = attachArtMapInteraction(c, host);
    c.dispatchEvent(new MouseEvent('mousemove', { clientX: 700, clientY: 400 }));
    expect(c.style.cursor).toBe('pointer');
    c.dispatchEvent(new MouseEvent('mousemove', { clientX: 20, clientY: 20 }));
    expect(c.style.cursor).toBe('grab');
    dispose();
  });
});

describe('clicking', () => {
  it('ripples the bubble and pins its card', () => {
    const drive = (canvas: HTMLCanvasElement) => {
      canvas.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 700, clientY: 400 }));
      canvas.dispatchEvent(new MouseEvent('mouseup', { button: 0, clientX: 700, clientY: 400 }));
    };
    const { vanilla, mine } = bothEffects({ placed: [centreNode] }, drive);
    expect(mine).toEqual(vanilla);
    expect(mine.join('\n')).toContain('emitRipple(0,0,200)');
    expect(mine.join('\n')).toContain('showPanelArtist(node:7)');
  });

  it('ripples empty water at the world point, with no hue and no card', () => {
    const drive = (canvas: HTMLCanvasElement) => {
      canvas.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 20, clientY: 20 }));
      canvas.dispatchEvent(new MouseEvent('mouseup', { button: 0, clientX: 20, clientY: 20 }));
    };
    const { vanilla, mine } = bothEffects({ placed: [centreNode] }, drive);
    expect(mine).toEqual(vanilla);
    expect(mine.join('\n')).not.toContain('showPanelArtist');
  });

  it('treats a drag as a pan, not a click', () => {
    const drive = (canvas: HTMLCanvasElement) => {
      canvas.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 700, clientY: 400 }));
      canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: 720, clientY: 400 }));
      canvas.dispatchEvent(new MouseEvent('mouseup', { button: 0, clientX: 720, clientY: 400 }));
    };
    const { vanilla, mine } = bothEffects({ placed: [centreNode] }, drive);
    expect(mine).toEqual(vanilla);
    expect(mine.join('\n')).not.toContain('emitRipple');
  });

  it('ripples the BUBBLE CENTRE, not the click point', () => {
    // Clicking 20px off the bubble's centre (still inside its 60px radius) is
    // the only way to tell node.x/node.y apart from the world point.
    const drive = (canvas: HTMLCanvasElement) => {
      canvas.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 720, clientY: 400 }));
      canvas.dispatchEvent(new MouseEvent('mouseup', { button: 0, clientX: 720, clientY: 400 }));
    };
    const { vanilla, mine } = bothEffects({ placed: [centreNode] }, drive);
    expect(mine).toEqual(vanilla);
    expect(mine.join('\n')).toContain('emitRipple(0,0,200)');
  });

  it('ignores a mouseup from a non-left button', () => {
    const drive = (canvas: HTMLCanvasElement) => {
      canvas.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 700, clientY: 400 }));
      canvas.dispatchEvent(new MouseEvent('mouseup', { button: 2, clientX: 700, clientY: 400 }));
    };
    const { vanilla, mine } = bothEffects({ placed: [centreNode] }, drive);
    expect(mine).toEqual(vanilla);
    expect(mine.join('\n')).not.toContain('emitRipple');
  });

  it('draws the drag threshold at 5px', () => {
    const start = { x: 100, y: 100, time: 0 };
    expect(artMapWasDrag(start, { clientX: 105, clientY: 100 })).toBe(false);
    expect(artMapWasDrag(start, { clientX: 106, clientY: 100 })).toBe(true);
    expect(artMapWasDrag(null, { clientX: 999, clientY: 999 })).toBe(false);
  });
});

describe('the right-click menu', () => {
  it('hides the menu over empty water', () => {
    const drive = (canvas: HTMLCanvasElement) =>
      canvas.dispatchEvent(new MouseEvent('contextmenu', { clientX: 20, clientY: 20 }));
    const { vanilla, mine } = bothEffects({ placed: [centreNode] }, drive);
    expect(mine).toEqual(vanilla);
    expect(mine).toContain('hideContextMenu');
  });

  it('hides the menu over a genre LABEL rather than offering artist actions', () => {
    const label = node({ id: 'label_Rock', _isLabel: true, radius: 60 });
    const drive = (canvas: HTMLCanvasElement) =>
      canvas.dispatchEvent(new MouseEvent('contextmenu', { clientX: 700, clientY: 400 }));
    const { mine } = bothEffects({ placed: [label] }, drive);
    expect(mine).toContain('hideContextMenu');
  });

  it('opens on a real bubble', () => {
    const drive = (canvas: HTMLCanvasElement) =>
      canvas.dispatchEvent(new MouseEvent('contextmenu', { clientX: 700, clientY: 400 }));
    const { mine } = bothEffects({ placed: [centreNode] }, drive);
    expect(mine).toContain('showContextMenu(node:7)');
    expect(mine).not.toContain('hideContextMenu');
  });
});

// ── Touch ────────────────────────────────────────────────────────────────────

describe('touch', () => {
  it('pans on one finger', () => {
    const { vanillaCam, mineCam, vanilla, mine } = bothEffects({}, (canvas) => {
      canvas.dispatchEvent(touchEvent('touchstart', [touch(100, 100)]));
      canvas.dispatchEvent(touchEvent('touchmove', [touch(150, 130)]));
    });
    expect(mineCam).toEqual(vanillaCam);
    expect(mine).toEqual(vanilla);
    expect(artMap.offsetX).toBe(750);
  });

  it('taps to select', () => {
    const { vanilla, mine } = bothEffects({ placed: [centreNode] }, (canvas) => {
      canvas.dispatchEvent(touchEvent('touchstart', [touch(700, 400)]));
      canvas.dispatchEvent(touchEvent('touchend', [], [touch(700, 400)]));
    });
    expect(mine).toEqual(vanilla);
    expect(mine.join('\n')).toContain('showPanelArtist(node:7)');
  });

  it('does not select after a drag', () => {
    const { vanilla, mine } = bothEffects({ placed: [centreNode] }, (canvas) => {
      canvas.dispatchEvent(touchEvent('touchstart', [touch(700, 400)]));
      canvas.dispatchEvent(touchEvent('touchend', [], [touch(730, 400)]));
    });
    expect(mine).toEqual(vanilla);
    expect(mine.join('\n')).not.toContain('showPanelArtist');
  });

  it('draws the tap threshold at 8px', () => {
    expect(artMapWasTouchDrag(touch(100, 100), touch(108, 100))).toBe(false);
    expect(artMapWasTouchDrag(touch(100, 100), touch(109, 100))).toBe(true);
  });
});

// ── Debounced follow-ups ─────────────────────────────────────────────────────

describe('the debounced rebuilds', () => {
  it('rebuilds once after the wheel settles, not per notch', () => {
    const drive = (canvas: HTMLCanvasElement) => {
      for (let i = 0; i < 5; i++) {
        const e = new Event('wheel', { bubbles: true, cancelable: true }) as WheelEvent & {
          deltaY: number;
          clientX: number;
          clientY: number;
        };
        e.deltaY = -100;
        e.clientX = 700;
        e.clientY = 400;
        canvas.dispatchEvent(e);
        vi.advanceTimersByTime(50);
      }
      vi.advanceTimersByTime(ARTMAP_ZOOM_REBUILD_MS + 10);
    };
    const { vanilla, mine } = bothEffects({}, drive);
    expect(mine).toEqual(vanilla);
    // Five notches → five blits and five ambient checks, then ONE settle pass.
    expect(mine.filter((e) => e === 'render')).toHaveLength(6);
    expect(artMap.dirty).toBe(true);
  });

  it('waits the full 300ms after the last wheel notch before rebuilding', () => {
    // LITERAL times, not the exported constant: advancing by the constant moves
    // with it, so a lengthened debounce would pass unnoticed.
    document.body.innerHTML = '';
    seed(artMap, {});
    const c = makeCanvas();
    const dispose = attachArtMapInteraction(c, host);
    const wheel = () => {
      const e = new Event('wheel', { bubbles: true, cancelable: true }) as WheelEvent & {
        deltaY: number;
        clientX: number;
        clientY: number;
      };
      e.deltaY = -100;
      e.clientX = 700;
      e.clientY = 400;
      c.dispatchEvent(e);
    };
    wheel();
    vi.advanceTimersByTime(250);
    expect(artMap.dirty).toBe(false);
    wheel(); //  re-arms from zero
    vi.advanceTimersByTime(250);
    expect(artMap.dirty).toBe(false);
    vi.advanceTimersByTime(60);
    expect(artMap.dirty).toBe(true);
    dispose();
  });

  it('waits the full 160ms after the last resize', () => {
    document.body.innerHTML = '';
    effects.length = 0;
    seed(artMap, {});
    const c = makeCanvas();
    const dispose = attachArtMapInteraction(c, host);
    window.dispatchEvent(new Event('resize'));
    vi.advanceTimersByTime(120);
    expect(effects).toEqual([]);
    vi.advanceTimersByTime(50);
    expect(effects).toEqual(['resized']);
    dispose();
  });

  it('re-frames once after a burst of resizes', () => {
    const drive = () => {
      for (let i = 0; i < 4; i++) {
        window.dispatchEvent(new Event('resize'));
        vi.advanceTimersByTime(40);
      }
      vi.advanceTimersByTime(ARTMAP_RESIZE_MS + 10);
    };
    document.body.innerHTML = '';
    effects.length = 0;
    seed(artMap, {});
    const c = makeCanvas();
    const dispose = attachArtMapInteraction(c, host);
    drive();
    expect(effects.filter((e) => e === 'resized')).toHaveLength(1);
    dispose();
  });
});

// ── Teardown ─────────────────────────────────────────────────────────────────

describe('visibility', () => {
  it('ignores every shortcut while the map is off screen', () => {
    document.body.innerHTML = '';
    seed(artMap, { _oneIsland: true });
    const c = makeCanvas();
    const dispose = attachArtMapInteraction(c, host);
    mineVisible = false;
    effects.length = 0;
    for (const key of ['Escape', 'f', '+', '-', '0', 'd', 'ArrowLeft']) {
      window.dispatchEvent(new KeyboardEvent('keydown', { key }));
    }
    expect(effects).toEqual([]);
    mineVisible = true;
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(effects).toEqual(['close']);
    dispose();
  });
});

describe('dispose', () => {
  it('detaches every listener it attached', () => {
    document.body.innerHTML = '';
    effects.length = 0;
    seed(artMap, { placed: [centreNode] });
    const c = makeCanvas();
    const dispose = attachArtMapInteraction(c, host);
    c.dispatchEvent(new MouseEvent('mousemove', { clientX: 700, clientY: 400 }));
    expect(effects.length).toBeGreaterThan(0);

    dispose();
    effects.length = 0;
    c.dispatchEvent(new MouseEvent('mousemove', { clientX: 20, clientY: 20 }));
    c.dispatchEvent(new MouseEvent('mouseleave'));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    window.dispatchEvent(new Event('resize'));
    document.dispatchEvent(new Event('visibilitychange'));
    vi.advanceTimersByTime(2000);
    expect(effects).toEqual([]);
  });

  it('cancels the debounces still in flight', () => {
    document.body.innerHTML = '';
    effects.length = 0;
    seed(artMap, { placed: [centreNode] });
    const c = makeCanvas();
    const dispose = attachArtMapInteraction(c, host);
    c.dispatchEvent(new MouseEvent('mousemove', { clientX: 700, clientY: 400 }));
    dispose();
    effects.length = 0;
    vi.advanceTimersByTime(5000);
    // Neither the panel swap nor the constellation may fire into a dead map.
    expect(effects).toEqual([]);
  });

  it('a second mount does not double up on the first', () => {
    // This is the case the vanilla's per-ELEMENT guard cannot cover once React
    // recreates the canvas per mount.
    document.body.innerHTML = '';
    seed(artMap, { placed: [centreNode] });
    const c1 = makeCanvas();
    const d1 = attachArtMapInteraction(c1, host);
    d1();
    const c2 = makeCanvas();
    const d2 = attachArtMapInteraction(c2, host);
    effects.length = 0;
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(effects).toEqual(['close']);
    d2();
  });
});
