/**
 * Artist Map — measuring, sizing and tearing down the canvas.
 *
 * Split out of the component on purpose: this is the part where a mistake is
 * silent (a canvas at the wrong backing-store scale looks blurry, a missed
 * teardown leaks a rAF loop into a page that no longer has a canvas), so it is
 * plain functions with tests rather than effect bodies.
 *
 * Transcribed from `webui/static/discover.js` 8284-8299 (the watchlist open),
 * 9529-9546 (the genre open, which measures differently), 8422-8443
 * (closeArtistMap) and 10227-10253 (the resize handler).
 */

import { artMap } from './-discover.artist-map';

/** The toolbar height assumed when it cannot be measured (8289 / 10237). */
export const ARTMAP_TOOLBAR_FALLBACK = 50;

export interface ArtMapMeasurement {
  width: number;
  height: number;
}

/**
 * How the watchlist and explorer maps measure themselves (8287-8289).
 *
 * Full container width, and the container height minus the toolbar — measured
 * rather than assumed, because the toolbar wraps taller on mobile.
 */
export function artMapMeasureFull(
  containerWidth: number,
  containerHeight: number,
  toolbarHeight: number | null,
): ArtMapMeasurement {
  return {
    width: containerWidth,
    height: containerHeight - (toolbarHeight ?? ARTMAP_TOOLBAR_FALLBACK),
  };
}

/**
 * How the GENRE map measures itself (9533-9534) — differently, because it has a
 * sidebar.
 *
 * It prefers the canvas's own laid-out size and the content row's height, and
 * only falls back to container-minus-sidebar when those read zero. The two
 * measurements are not interchangeable: using the full-width one with a sidebar
 * open would push the map under it.
 */
export function artMapMeasureWithSidebar(
  canvasClientWidth: number,
  contentRowHeight: number,
  containerWidth: number,
  containerHeight: number,
  sidebarWidth: number,
): ArtMapMeasurement {
  return {
    width: canvasClientWidth || containerWidth - sidebarWidth,
    height: contentRowHeight || containerHeight - ARTMAP_TOOLBAR_FALLBACK,
  };
}

/**
 * How a RESIZE re-measures (10233-10237).
 *
 * A third measurement again: container width minus whatever the sidebar
 * currently occupies, container height minus the measured toolbar, and both
 * floored at 120 so a collapsed panel cannot produce a zero-sized canvas.
 */
export function artMapMeasureResize(
  containerWidth: number,
  containerHeight: number,
  sidebarWidth: number,
  toolbarHeight: number | null,
): ArtMapMeasurement {
  return {
    width: Math.max(120, containerWidth - sidebarWidth),
    height: Math.max(120, containerHeight - (toolbarHeight ?? ARTMAP_TOOLBAR_FALLBACK)),
  };
}

/**
 * Point the map at a canvas and size it (8290-8294).
 *
 * The backing store is devicePixelRatio times the CSS size, and the context is
 * scaled to match so every draw can stay in CSS pixels. Assigning `canvas.width`
 * RESETS the context transform, which is why the scale is re-applied here and
 * not once at open — the resize path depends on that.
 */
export function artMapAttachCanvas(
  canvas: HTMLCanvasElement,
  { width, height }: ArtMapMeasurement,
  dpr: number = window.devicePixelRatio,
): void {
  artMap.canvas = canvas;
  artMap.ctx = canvas.getContext('2d');
  artMap.width = width;
  artMap.height = height;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  canvas.style.width = width + 'px';
  canvas.style.height = height + 'px';
  artMap.ctx?.scale(dpr, dpr);
}

/**
 * Centre the camera and clear the world (8295-8299).
 *
 * Called on every open, NOT on resize — a resize keeps whatever the user was
 * looking at and re-frames it instead.
 */
export function artMapResetWorld(): void {
  artMap.offsetX = artMap.width / 2;
  artMap.offsetY = artMap.height / 2;
  artMap.placed = [];
  artMap.images = {};
  artMap._nodeById = null;
}

/**
 * Everything `closeArtistMap` unwinds (8422-8443), minus the DOM.
 *
 * The rAF handles matter most: `animFrame` is the legacy one and `_anim.raf` is
 * the v2 loop's. Both are cancelled and `_ambient` is cleared, or the loop keeps
 * running against a canvas that is gone — which under React means a canvas that
 * has been unmounted.
 *
 * `_loadToken` is bumped too. The vanilla does not do this because opening
 * another map bumps it anyway; under React an unmount is not an open, so
 * without it an in-flight image stream would keep writing bitmaps into a dead
 * world and scheduling redraws.
 */
export function artMapTeardown(): void {
  if (artMap.animFrame) cancelAnimationFrame(artMap.animFrame);
  artMap.animFrame = null;
  artMap._ambient = false;
  artMap._anim.running = false;
  if (artMap._anim.raf) {
    cancelAnimationFrame(artMap._anim.raf);
    artMap._anim.raf = null;
  }
  if (artMap._rafPending) {
    cancelAnimationFrame(artMap._rafPending);
    artMap._rafPending = null;
  }
  if (artMap._animating) {
    cancelAnimationFrame(artMap._animating);
    artMap._animating = null;
  }
  artMap._oneIsland = false;
  artMap._revealing = false;
  artMap.hoveredNode = null;
  artMap._constellationActive = false;
  artMap._constellationFade = 0;
  artMap._constellationCache = null;
  artMap._ripples = null;
  artMap._loadToken = ((artMap._loadToken as number) || 0) + 1;
  artMap.canvas = null;
  artMap.ctx = null;
  artMap.offscreen = null;
  artMap._bufferScale = null;
}
