import { beforeEach, describe, expect, it, vi } from 'vitest';

import { artMap } from './-discover.artist-map';
import {
  ARTMAP_TOOLBAR_FALLBACK,
  artMapAttachCanvas,
  artMapMeasureFull,
  artMapMeasureResize,
  artMapMeasureWithSidebar,
  artMapResetWorld,
  artMapTeardown,
} from './-discover.artist-map.lifecycle';
import { artMapStartLoop } from './-discover.artist-map.render';

/**
 * The canvas lifecycle.
 *
 * These are the failures that are SILENT: a backing store at the wrong scale
 * just looks blurry, and a missed teardown leaks a rAF loop into a page whose
 * canvas has been unmounted. So each one is asserted directly rather than
 * inferred from a render.
 */

const cancelled: number[] = [];

beforeEach(() => {
  cancelled.length = 0;
  vi.stubGlobal('cancelAnimationFrame', (id: number) => cancelled.push(id));
  vi.stubGlobal('requestAnimationFrame', () => 42);
  Object.assign(artMap, {
    canvas: null,
    ctx: null,
    offscreen: null,
    width: 0,
    height: 0,
    offsetX: 0,
    offsetY: 0,
    placed: [],
    images: {},
    animFrame: null,
    _anim: { running: false, raf: null, last: 0 },
    _ambient: false,
    _nodeById: null,
    _rafPending: null,
    _animating: null,
    _loadToken: 0,
    _bufferScale: null,
    _oneIsland: false,
    _revealing: false,
    hoveredNode: null,
    _constellationActive: false,
    _constellationFade: 0,
    _constellationCache: null,
    _ripples: null,
  });
});

// ── Measuring ────────────────────────────────────────────────────────────────

describe('the three measurements', () => {
  it('measures the full-width maps against the toolbar', () => {
    expect(artMapMeasureFull(1400, 900, 56)).toEqual({ width: 1400, height: 844 });
  });

  it('assumes a 50px toolbar only when it cannot measure one', () => {
    // A LITERAL 850. Asserting `900 - ARTMAP_TOOLBAR_FALLBACK` moves with the
    // constant and lets the fallback drift silently.
    expect(artMapMeasureFull(1400, 900, null).height).toBe(850);
    expect(ARTMAP_TOOLBAR_FALLBACK).toBe(50);
    // A measured 0 is a real answer and must NOT trip the fallback.
    expect(artMapMeasureFull(1400, 900, 0).height).toBe(900);
  });

  it('prefers the canvas’s own size for the genre map', () => {
    // 1180 is deliberately NOT `containerWidth - sidebarWidth` (1100). With a
    // fixture where the two agree, dropping the preference is invisible.
    expect(artMapMeasureWithSidebar(1180, 820, 1400, 900, 300)).toEqual({
      width: 1180,
      height: 820,
    });
  });

  it('falls back to container-minus-sidebar when the canvas has no size yet', () => {
    // Before layout the canvas reads 0; the sidebar must still be subtracted or
    // the map is pushed underneath it.
    expect(artMapMeasureWithSidebar(0, 0, 1400, 900, 300)).toEqual({
      width: 1100,
      height: 850,
    });
  });

  it('floors a resize at 120 in both axes', () => {
    expect(artMapMeasureResize(100, 90, 300, 56)).toEqual({ width: 120, height: 120 });
    expect(artMapMeasureResize(1400, 900, 0, 56)).toEqual({ width: 1400, height: 844 });
  });

  it('subtracts only the sidebar that is actually showing', () => {
    expect(artMapMeasureResize(1400, 900, 0, 56).width).toBe(1400);
    expect(artMapMeasureResize(1400, 900, 260, 56).width).toBe(1140);
  });
});

// ── Sizing ───────────────────────────────────────────────────────────────────

describe('artMapAttachCanvas', () => {
  function fakeCanvas() {
    const scale = vi.fn();
    const canvas = {
      width: 0,
      height: 0,
      style: {} as CSSStyleDeclaration,
      getContext: () => ({ scale }) as unknown as CanvasRenderingContext2D,
    } as unknown as HTMLCanvasElement;
    return { canvas, scale };
  }

  it('sizes the backing store by dpr and the CSS box in px', () => {
    const { canvas, scale } = fakeCanvas();
    artMapAttachCanvas(canvas, { width: 1400, height: 800 }, 2);
    expect(canvas.width).toBe(2800);
    expect(canvas.height).toBe(1600);
    expect(canvas.style.width).toBe('1400px');
    expect(canvas.style.height).toBe('800px');
    expect(scale).toHaveBeenCalledWith(2, 2);
  });

  it('records the CSS size on the map, not the backing size', () => {
    // Everything downstream draws in CSS pixels; storing the scaled size here
    // would double every coordinate.
    const { canvas } = fakeCanvas();
    artMapAttachCanvas(canvas, { width: 1400, height: 800 }, 2);
    expect([artMap.width, artMap.height]).toEqual([1400, 800]);
  });

  it('re-applies the scale every time, because setting width resets the transform', () => {
    const { canvas, scale } = fakeCanvas();
    artMapAttachCanvas(canvas, { width: 100, height: 100 }, 3);
    artMapAttachCanvas(canvas, { width: 200, height: 200 }, 3);
    expect(scale).toHaveBeenCalledTimes(2);
  });

  it('handles a dpr of 1 without special-casing it', () => {
    const { canvas, scale } = fakeCanvas();
    artMapAttachCanvas(canvas, { width: 800, height: 600 }, 1);
    expect(canvas.width).toBe(800);
    expect(scale).toHaveBeenCalledWith(1, 1);
  });
});

describe('artMapResetWorld', () => {
  it('centres the camera on the measured size and empties the world', () => {
    artMap.width = 1400;
    artMap.height = 800;
    artMap.placed = [{ id: 1 } as never];
    artMap.images = { 1: {} as CanvasImageSource };
    artMap._nodeById = { 1: {} as never };
    artMapResetWorld();
    expect([artMap.offsetX, artMap.offsetY]).toEqual([700, 400]);
    expect(artMap.placed).toEqual([]);
    expect(artMap.images).toEqual({});
    expect(artMap._nodeById).toBeNull();
  });
});

// ── Teardown ─────────────────────────────────────────────────────────────────

describe('artMapTeardown', () => {
  it('cancels every rAF handle it knows about', () => {
    Object.assign(artMap, {
      animFrame: 1,
      _anim: { running: true, raf: 2, last: 0 },
      _rafPending: 3,
      _animating: 4,
    });
    artMapTeardown();
    expect([...cancelled].sort((a, b) => a - b)).toEqual([1, 2, 3, 4]);
    expect(artMap.animFrame).toBeNull();
    expect(artMap._anim.raf).toBeNull();
    expect(artMap._rafPending).toBeNull();
    expect(artMap._animating).toBeNull();
  });

  it('stops the ambient loop from restarting itself', () => {
    artMap._ambient = true;
    artMap._anim.running = true;
    artMapTeardown();
    expect(artMap._ambient).toBe(false);
    expect(artMap._anim.running).toBe(false);
  });

  it('really does stop the loop — a started loop does not tick after teardown', () => {
    // The end-to-end version of the two assertions above: start the real loop,
    // tear down, and confirm nothing is left armed.
    artMap._ambient = true;
    artMap.placed = [];
    artMapStartLoop();
    expect(artMap._anim.running).toBe(true);
    artMapTeardown();
    expect(artMap._anim.running).toBe(false);
    expect(cancelled).toContain(42); //  the handle the stubbed rAF handed out
  });

  it('bumps the load token so an in-flight image stream is orphaned', () => {
    // The vanilla relies on the next OPEN bumping this. Under React an unmount
    // is not an open, so without it a stream would keep writing bitmaps into a
    // dead world and scheduling redraws against a detached canvas.
    artMap._loadToken = 7;
    artMapTeardown();
    expect(artMap._loadToken).toBe(8);
  });

  it('drops the canvas, context and buffer references', () => {
    Object.assign(artMap, {
      canvas: {} as HTMLCanvasElement,
      ctx: {} as CanvasRenderingContext2D,
      offscreen: {} as HTMLCanvasElement,
      _bufferScale: 0.5,
    });
    artMapTeardown();
    expect(artMap.canvas).toBeNull();
    expect(artMap.ctx).toBeNull();
    expect(artMap.offscreen).toBeNull();
    expect(artMap._bufferScale).toBeNull();
  });

  it('clears the interaction state a reopen would otherwise inherit', () => {
    Object.assign(artMap, {
      _oneIsland: true,
      _revealing: true,
      hoveredNode: { id: 1 } as never,
      _constellationActive: true,
      _constellationFade: 0.6,
      _constellationCache: { nodeId: 1, nodes: [] },
      _ripples: [{ cx: 0, cy: 0, hue: 1, maxR: 1, t0: 0, dur: 1 }],
    });
    artMapTeardown();
    expect(artMap._oneIsland).toBe(false);
    expect(artMap._revealing).toBe(false);
    expect(artMap.hoveredNode).toBeNull();
    expect(artMap._constellationActive).toBe(false);
    expect(artMap._constellationFade).toBe(0);
    expect(artMap._constellationCache).toBeNull();
    expect(artMap._ripples).toBeNull();
  });

  it('is safe to call twice', () => {
    artMapTeardown();
    expect(() => artMapTeardown()).not.toThrow();
    expect(artMap._loadToken).toBe(2);
  });
});
