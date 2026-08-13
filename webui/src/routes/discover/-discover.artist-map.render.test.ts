import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { type ArtMapNode, artMap } from './-discover.artist-map';
import {
  ARTMAP_CAMERA_MS,
  ARTMAP_IMAGE_CONCURRENCY,
  ARTMAP_REDRAW_THROTTLE_MS,
  artMapAnimateConstellation,
  artMapAnimateTo,
  artMapCircleMask,
  artMapDecodeSmall,
  artMapEnsureAmbient,
  artMapFocusIsland,
  artMapIslandNavStep,
  artMapLoadImage,
  artMapRender,
  artMapStartLoop,
  artMapStreamImages,
} from './-discover.artist-map.render';

/**
 * The parts of the render pipeline the recording-context differential cannot
 * reach: the rAF loop's scheduling, the camera easing, and the image stream's
 * ordering, concurrency and cancellation.
 *
 * These are about WHEN work happens rather than what gets drawn, so they drive a
 * manual frame queue and fake timers instead of comparing canvas logs.
 */

// ── A hand-cranked frame queue ───────────────────────────────────────────────

let frames: ((t: number) => void)[] = [];
let nextRaf = 1;
const cancelled = new Set<number>();

function flushFrame(t: number) {
  const due = frames;
  frames = [];
  due.forEach((f) => f(t));
}

/** A context that answers every call without recording — the loop's draws are noise here. */
function noopCtx(): CanvasRenderingContext2D {
  const gradient = { addColorStop() {} };
  return new Proxy({} as Record<string, unknown>, {
    get: (_t, prop: string) => {
      if (prop === 'then') return undefined;
      return () => (prop.startsWith('create') ? gradient : undefined);
    },
    set: () => true,
  }) as unknown as CanvasRenderingContext2D;
}

/**
 * jsdom has no 2D context, so anything that builds its own canvas (the offscreen
 * buffer, the sprites, the circle mask) throws on `getContext(...).scale`. The
 * stub hands out a working no-op context instead — without it the loop's draws
 * blow up and `artMapCircleMask` silently takes its error fallback, which makes
 * the tests pass for the wrong reason.
 */
const realCreateElement = document.createElement.bind(document);
function stubCanvasFactory() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (document as any).createElement = (tag: string, ...rest: unknown[]) => {
    if (String(tag).toLowerCase() !== 'canvas') {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return (realCreateElement as any)(tag, ...rest);
    }
    return { width: 0, height: 0, getContext: () => noopCtx() };
  };
}

function resetMap(over: Partial<typeof artMap> = {}) {
  Object.assign(artMap, {
    placed: [],
    edges: [],
    images: {},
    width: 1400,
    height: 800,
    offsetX: 0,
    offsetY: 0,
    zoom: 1,
    dirty: false,
    offscreen: null,
    hoveredNode: null,
    ctx: noopCtx(),
    canvas: { width: 1400, height: 800 } as HTMLCanvasElement,
    _anim: { running: false, raf: null, last: 0 },
    _ambient: false,
    _revealing: false,
    _ripples: null,
    _liveCount: 0,
    _islands: undefined,
    _nodeById: null,
    _bgGrad: undefined,
    _constellationFade: 0,
    _constellationCache: null,
    _animating: null,
    _rafPending: null,
    _loadToken: 0,
    _gloss: undefined,
    _halos: undefined,
    ...over,
  });
}

beforeEach(() => {
  frames = [];
  nextRaf = 1;
  cancelled.clear();
  stubCanvasFactory();
  resetMap();
  vi.stubGlobal('requestAnimationFrame', (cb: (t: number) => void) => {
    const id = nextRaf++;
    frames.push((t) => {
      if (!cancelled.has(id)) cb(t);
    });
    return id;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => cancelled.add(id));
});

afterEach(() => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (document as any).createElement = realCreateElement;
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const node = (over: Partial<ArtMapNode> = {}): ArtMapNode =>
  ({
    id: 1,
    name: 'A',
    x: 0,
    y: 0,
    radius: 40,
    opacity: 1,
    type: 'similar',
    image_url: '',
    ...over,
  }) as ArtMapNode;

// ── The animation loop ───────────────────────────────────────────────────────

describe('artMapStartLoop', () => {
  it('starts once — a second call while running is a no-op', () => {
    resetMap({ _ambient: true, _liveCount: 5 });
    artMapStartLoop();
    expect(frames).toHaveLength(1);
    artMapStartLoop();
    expect(frames).toHaveLength(1);
    expect(artMap._anim.running).toBe(true);
  });

  it('parks itself when nothing is animating', () => {
    resetMap({ _ambient: false });
    artMapStartLoop();
    flushFrame(1000);
    expect(artMap._anim.running).toBe(false);
    expect(artMap._anim.raf).toBeNull();
    expect(frames).toHaveLength(0);
  });

  it('keeps running while bubbles are live and buoyancy is on', () => {
    resetMap({ _ambient: true, placed: [node({ aScale: 1 })], _liveBuildZoom: 1 });
    artMapStartLoop();
    // A REAL rAF timestamp. At t=0 the `t - _lastDraw >= 31` throttle is false on
    // the very first frame, so the loop ticks without ever drawing.
    flushFrame(1000);
    // The draw sets _liveCount from what it actually drew — one big bubble.
    expect(artMap._liveCount).toBeGreaterThan(0);
    expect(artMap._anim.running).toBe(true);
    expect(frames).toHaveLength(1);
  });

  it('parks when the tab is hidden even with buoyancy on', () => {
    resetMap({ _ambient: true, placed: [node({ aScale: 1 })], _liveBuildZoom: 1 });
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    artMapStartLoop();
    flushFrame(1000);
    expect(artMap._anim.running).toBe(false);
  });

  it('throttles drawing to ~30fps but never skips a dirty frame', () => {
    resetMap({ _ambient: true, placed: [node({ aScale: 1 })], _liveBuildZoom: 1 });
    const drawn: number[] = [];
    const ctx = noopCtx();
    artMap.ctx = new Proxy(ctx as unknown as Record<string, unknown>, {
      get: (t, prop: string) => {
        if (prop === 'fillRect') {
          return (...args: number[]) => {
            if (args[2] === artMap.width) drawn.push(artMap._now || 0);
          };
        }
        return (t as Record<string, unknown>)[prop];
      },
      set: () => true,
    }) as unknown as CanvasRenderingContext2D;

    artMapStartLoop();
    flushFrame(1000); //  draws (no _lastDraw yet)
    flushFrame(1010); //  10ms later — under 31ms, and not dirty, so no draw
    flushFrame(1020); //  still under
    flushFrame(1040); //  past 31ms — draws
    const throttled = drawn.length;
    artMap.dirty = true;
    flushFrame(1041); //  dirty always draws regardless of the throttle
    expect(throttled).toBe(4); //  two frames × two fillRects (background + vignette)
    expect(drawn.length).toBeGreaterThan(throttled);
  });

  it('keeps ticking while a reveal is still running, even with nothing live', () => {
    resetMap({
      _ambient: false,
      placed: [node({ aScale: 0, aAlpha: 0, _revealAt: 5000, _revealDur: 400 })],
    });
    artMapStartLoop();
    flushFrame(1000); //  before the node's start time — still "active"
    expect(artMap._anim.running).toBe(true);
  });
});

describe('artMapRender', () => {
  it('coalesces a burst of requests into ONE frame', () => {
    // Pan, hover and animation all call this; without the guard a single
    // mousemove burst would draw several times per frame.
    resetMap();
    artMapRender();
    artMapRender();
    artMapRender();
    expect(frames).toHaveLength(1);
  });

  it('re-arms once the frame has run', () => {
    resetMap();
    artMapRender();
    flushFrame(1000);
    expect(artMap._rafPending).toBeNull();
    artMapRender();
    expect(frames).toHaveLength(1);
  });
});

describe('artMapAnimateConstellation', () => {
  it('fades IN by 0.08 a frame and stops at 1', () => {
    resetMap({ _constellationActive: true, _constellationFade: 0 });
    artMapAnimateConstellation();
    expect(artMap._constellationFade).toBeCloseTo(0.08, 6);
    resetMap({ _constellationActive: true, _constellationFade: 0.97 });
    artMapAnimateConstellation();
    expect(artMap._constellationFade).toBe(1);
  });

  it('fades OUT by 0.1 a frame — deliberately faster than it lights up', () => {
    resetMap({ _constellationActive: false, _constellationFade: 1 });
    artMapAnimateConstellation();
    expect(artMap._constellationFade).toBeCloseTo(0.9, 6);
    // The two rates differ on purpose; equalising them is a visible change.
    expect(0.1).not.toBe(0.08);
  });

  it('keeps the cache until the fade actually reaches zero', () => {
    resetMap({
      _constellationActive: false,
      _constellationFade: 0.2,
      _constellationCache: { nodeId: 1, nodes: [] },
    });
    artMapAnimateConstellation();
    expect(artMap._constellationFade).toBeCloseTo(0.1, 6);
    expect(artMap._constellationCache).not.toBeNull(); //  still fading

    artMapAnimateConstellation();
    expect(artMap._constellationFade).toBe(0);
    expect(artMap._constellationCache).toBeNull(); //  dropped only at zero
  });

  it('does nothing once a fade has settled at either end', () => {
    resetMap({ _constellationActive: true, _constellationFade: 1 });
    artMapAnimateConstellation();
    expect(frames).toHaveLength(0);
    resetMap({ _constellationActive: false, _constellationFade: 0 });
    artMapAnimateConstellation();
    expect(frames).toHaveLength(0);
  });
});

describe('artMapEnsureAmbient', () => {
  it('starts the loop only when buoyancy is on', () => {
    resetMap({ _ambient: false });
    artMapEnsureAmbient();
    expect(frames).toHaveLength(0);
    resetMap({ _ambient: true });
    artMapEnsureAmbient();
    expect(frames).toHaveLength(1);
  });

  it('does not double-start an already-running loop', () => {
    resetMap({ _ambient: true });
    artMap._anim.running = true;
    artMapEnsureAmbient();
    expect(frames).toHaveLength(0);
  });

  it('stays parked while the tab is hidden', () => {
    resetMap({ _ambient: true });
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    artMapEnsureAmbient();
    expect(frames).toHaveLength(0);
  });
});

// ── The camera ───────────────────────────────────────────────────────────────

describe('artMapAnimateTo', () => {
  it('eases out cubic and lands exactly on the target', () => {
    resetMap({ zoom: 1, offsetX: 0, offsetY: 0 });
    vi.spyOn(performance, 'now').mockReturnValue(1000);
    artMapAnimateTo(2, 100, -50);

    flushFrame(1000 + ARTMAP_CAMERA_MS / 2);
    // Half way through, ease-out-cubic is 1-(0.5)^3 = 0.875 — well past linear.
    expect(artMap.zoom).toBeCloseTo(1 + 1 * 0.875, 6);
    expect(artMap.offsetX).toBeCloseTo(87.5, 6);

    flushFrame(1000 + ARTMAP_CAMERA_MS);
    expect([artMap.zoom, artMap.offsetX, artMap.offsetY]).toEqual([2, 100, -50]);
  });

  it('marks the buffer dirty only on the FINAL frame', () => {
    resetMap({ zoom: 1 });
    vi.spyOn(performance, 'now').mockReturnValue(0);
    artMapAnimateTo(2, 0, 0);
    flushFrame(100);
    expect(artMap.dirty).toBe(false); //  intermediate frames blit only
    flushFrame(1000);
    expect(artMap.dirty).toBe(true);
    expect(artMap._animating).toBeNull();
  });

  it('cancels a camera move already in flight', () => {
    resetMap({ zoom: 1 });
    vi.spyOn(performance, 'now').mockReturnValue(0);
    artMapAnimateTo(2, 0, 0);
    const firstId = artMap._animating;
    artMapAnimateTo(3, 0, 0);
    expect(cancelled.has(firstId as number)).toBe(true);
  });
});

// ── Image loading ────────────────────────────────────────────────────────────

describe('artMapCircleMask', () => {
  it('returns null for nothing', () => {
    expect(artMapCircleMask(null)).toBeNull();
  });

  it('passes a zero-width source straight through', () => {
    const src = { width: 0 } as unknown as CanvasImageSource;
    expect(artMapCircleMask(src)).toBe(src);
  });

  it('closes the source bitmap once it has been copied', () => {
    const close = vi.fn();
    const src = { width: 128, close } as unknown as CanvasImageSource;
    const masked = artMapCircleMask(src);
    expect(close).toHaveBeenCalledTimes(1);
    expect(masked).not.toBe(src);
  });

  it('falls back to the raw bitmap if masking throws', () => {
    const src = { width: 128 } as unknown as CanvasImageSource;
    vi.spyOn(document, 'createElement').mockImplementation(() => {
      throw new Error('no canvas');
    });
    expect(artMapCircleMask(src)).toBe(src);
  });
});

describe('artMapDecodeSmall', () => {
  it('resolves null for a missing blob without touching createImageBitmap', async () => {
    const spy = vi.fn();
    vi.stubGlobal('createImageBitmap', spy);
    await expect(artMapDecodeSmall(null, 200)).resolves.toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });

  it('clamps the decode size into 112..384', async () => {
    const calls: unknown[] = [];
    vi.stubGlobal('createImageBitmap', (_b: Blob, opts?: unknown) => {
      calls.push(opts);
      return Promise.resolve({ width: 128 });
    });
    const blob = new Blob(['x']);
    await artMapDecodeSmall(blob, 10);
    await artMapDecodeSmall(blob, 5000);
    expect(calls).toEqual([
      { resizeWidth: 112, resizeHeight: 112, resizeQuality: 'high' },
      { resizeWidth: 384, resizeHeight: 384, resizeQuality: 'high' },
    ]);
  });

  it('retries at full size when the resized decode rejects', async () => {
    let n = 0;
    vi.stubGlobal('createImageBitmap', () => {
      n++;
      return n === 1 ? Promise.reject(new Error('nope')) : Promise.resolve({ width: 64 });
    });
    const out = await artMapDecodeSmall(new Blob(['x']), 200);
    expect(n).toBe(2);
    expect(out).not.toBeNull();
  });

  it('resolves null when both decodes fail', async () => {
    vi.stubGlobal('createImageBitmap', () => Promise.reject(new Error('nope')));
    await expect(artMapDecodeSmall(new Blob(['x']), 200)).resolves.toBeNull();
  });
});

describe('artMapLoadImage', () => {
  it('tries a direct CORS fetch first', async () => {
    const fetchSpy = vi.fn(() => Promise.resolve({ ok: true, blob: () => Promise.resolve(null) }));
    vi.stubGlobal('fetch', fetchSpy);
    await artMapLoadImage('https://cdn/x.jpg', 200);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0]).toEqual(['https://cdn/x.jpg', { mode: 'cors' }]);
  });

  it('falls back to the server proxy when CORS fails', async () => {
    const fetchSpy = vi
      .fn()
      .mockRejectedValueOnce(new Error('cors'))
      .mockResolvedValueOnce({ ok: true, blob: () => Promise.resolve(null) });
    vi.stubGlobal('fetch', fetchSpy);
    await artMapLoadImage('https://cdn/a b.jpg', 200);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(fetchSpy.mock.calls[1][0]).toBe(
      '/api/image-proxy?url=' + encodeURIComponent('https://cdn/a b.jpg'),
    );
  });

  it('falls back on a non-ok response too, not just a thrown one', async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce({ ok: false })
      .mockResolvedValueOnce({ ok: true, blob: () => Promise.resolve(null) });
    vi.stubGlobal('fetch', fetchSpy);
    await artMapLoadImage('https://cdn/x.jpg', 200);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it('resolves null when even the proxy fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('down'))),
    );
    await expect(artMapLoadImage('https://cdn/x.jpg', 200)).resolves.toBeNull();
  });
});

// ── The image stream ─────────────────────────────────────────────────────────

describe('artMapStreamImages', () => {
  /** Resolve every load with a stub bitmap, recording the order requested. */
  function stubLoads() {
    const requested: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        requested.push(url);
        return Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob(['x'])) });
      }),
    );
    vi.stubGlobal('createImageBitmap', () => Promise.resolve({ width: 64 }));
    return requested;
  }

  it('fetches the biggest bubbles first', async () => {
    const requested = stubLoads();
    resetMap();
    artMapStreamImages([
      node({ id: 1, radius: 10, image_url: '/small.jpg' }),
      node({ id: 2, radius: 90, image_url: '/big.jpg' }),
      node({ id: 3, radius: 50, image_url: '/mid.jpg' }),
    ]);
    await vi.waitFor(() => expect(requested).toHaveLength(3));
    expect(requested).toEqual(['/big.jpg', '/mid.jpg', '/small.jpg']);
  });

  it('skips nodes with no image url at all', async () => {
    const requested = stubLoads();
    resetMap();
    artMapStreamImages([node({ id: 1, image_url: '' }), node({ id: 2, image_url: '/a.jpg' })]);
    await vi.waitFor(() => expect(requested).toHaveLength(1));
    expect(requested).toEqual(['/a.jpg']);
  });

  it('skips nodes whose bitmap is already cached', async () => {
    const requested = stubLoads();
    resetMap({ images: { 1: {} as CanvasImageSource } });
    artMapStreamImages([
      node({ id: 1, image_url: '/cached.jpg' }),
      node({ id: 2, image_url: '/new.jpg' }),
    ]);
    await vi.waitFor(() => expect(requested).toHaveLength(1));
    expect(requested).toEqual(['/new.jpg']);
  });

  it('holds concurrency at the configured ceiling', () => {
    const requested: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        requested.push(url);
        return new Promise(() => {}); //  never settles — everything stays in flight
      }),
    );
    resetMap();
    artMapStreamImages(
      Array.from({ length: 100 }, (_, i) => node({ id: i, image_url: `/${i}.jpg` })),
    );
    expect(requested).toHaveLength(24);
    expect(ARTMAP_IMAGE_CONCURRENCY).toBe(24);
  });

  it('drops bitmaps from a superseded stream', async () => {
    let resolveFirst: (v: unknown) => void = () => {};
    const gate = new Promise((r) => {
      resolveFirst = r;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(() => gate.then(() => ({ ok: true, blob: () => Promise.resolve(new Blob(['x'])) }))),
    );
    vi.stubGlobal('createImageBitmap', () => Promise.resolve({ width: 64 }));
    resetMap();

    artMapStreamImages([node({ id: 1, image_url: '/old.jpg' })]);
    // Opening another map bumps the token — the in-flight bitmap belongs to a
    // world that no longer exists and must not be painted into the new one.
    artMapStreamImages([]);
    resolveFirst(null);
    await new Promise((r) => setTimeout(r, 0));
    expect(artMap.images[1]).toBeUndefined();
  });

  it('caches a hidden bubble’s art without scheduling a redraw', async () => {
    vi.useFakeTimers();
    const requested = stubLoads();
    resetMap();
    artMapStreamImages([node({ id: 1, image_url: '/a.jpg', opacity: 0 })]);
    await vi.waitFor(() => expect(requested).toHaveLength(1));
    await vi.advanceTimersByTimeAsync(ARTMAP_REDRAW_THROTTLE_MS + 50);
    expect(artMap.images[1]).toBeDefined();
    expect(artMap.dirty).toBe(false); //  no redraw for something off-screen
  });

  it('coalesces arrivals into one throttled rebuild', async () => {
    vi.useFakeTimers();
    const requested = stubLoads();
    resetMap();
    artMapStreamImages(
      Array.from({ length: 5 }, (_, i) => node({ id: i, image_url: `/${i}.jpg` })),
    );
    await vi.waitFor(() => expect(requested).toHaveLength(5));
    expect(artMap.dirty).toBe(false); //  nothing yet — the redraw is deferred
    await vi.advanceTimersByTimeAsync(ARTMAP_REDRAW_THROTTLE_MS + 10);
    expect(artMap.dirty).toBe(true);
  });
});

describe('artMapFocusIsland', () => {
  const island = (name: string, over: Record<string, unknown> = {}) =>
    ({ name, cx: 100, cy: 50, r: 200, hue: 120, count: 2, ...over }) as never;

  function withIslands() {
    artMap.width = 1000;
    artMap.height = 600;
    artMap._panelW = 320;
    artMap._islands = [island('idm'), island('ambient', { cx: 900, cy: 400 })];
    artMap.placed = [
      node({ id: 1, _island: 'idm' } as never),
      node({ id: 2, _island: 'ambient' } as never),
      node({ id: 3, _island: 'idm', _isLabel: true } as never),
    ];
  }

  it('shows ONLY the focused island, labels always hidden, and frames it', () => {
    withIslands();
    const isl = artMapFocusIsland(0, { bloom: false });
    expect(isl!.name).toBe('idm');
    expect(artMap._focusIdx).toBe(0);
    expect(artMap.placed.map((n) => n.opacity)).toEqual([1, 0, 0]);
    // Framed in the width the panel leaves free (6097-6102).
    const usableW = 1000 - 320;
    const span = 200 * 2.3 + 120;
    const z = Math.min(usableW / span, 600 / span, 1.2);
    expect(artMap.zoom).toBeCloseTo(z);
    expect(artMap.offsetX).toBeCloseTo(usableW / 2 - 100 * z);
    expect(artMap.offsetY).toBeCloseTo(600 / 2 - 50 * z);
  });

  it('clamps the index instead of failing on an out-of-range step', () => {
    withIslands();
    expect(artMapFocusIsland(99, { bloom: false })!.name).toBe('ambient');
    expect(artMap._focusIdx).toBe(1);
    expect(artMapFocusIsland(-5, { bloom: false })!.name).toBe('idm');
  });

  it('blooms by default and renders statically only when told not to', () => {
    withIslands();
    artMapFocusIsland(0);
    // Bloom queues the reveal loop: bubbles get reveal timings + a ripple.
    expect(artMap._revealing).toBe(true);
    expect(artMap._ripples).toHaveLength(1);
    expect((artMap._ripples![0] as { hue: number }).hue).toBe(120);
  });

  it('returns null with no islands', () => {
    artMap._islands = [];
    expect(artMapFocusIsland(0)).toBeNull();
  });
});

describe('artMapIslandNavStep', () => {
  it('wraps both directions, and is INERT below two islands', () => {
    artMap.width = 1000;
    artMap.height = 600;
    artMap._panelW = 320;
    artMap._islands = [
      { name: 'a', cx: 0, cy: 0, r: 100, hue: 0, count: 1 } as never,
      { name: 'b', cx: 10, cy: 0, r: 100, hue: 0, count: 1 } as never,
    ];
    artMap.placed = [];
    artMap._focusIdx = 1;
    expect(artMapIslandNavStep(1)!.name).toBe('a'); // wrap forward
    expect(artMap._focusIdx).toBe(0);
    expect(artMapIslandNavStep(-1)!.name).toBe('b'); // wrap back
    artMap._islands = [{ name: 'only', cx: 0, cy: 0, r: 100, hue: 0, count: 1 } as never];
    artMap._focusIdx = 0;
    expect(artMapIslandNavStep(1)).toBeNull();
    expect(artMap._focusIdx).toBe(0);
  });
});
