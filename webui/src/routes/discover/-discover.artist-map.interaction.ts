/**
 * Artist Map — pointer, touch, keyboard and resize handling.
 *
 * Transcribed from `webui/static/discover.js` 9983-10254.
 *
 * ONE DELIBERATE DIVERGENCE, and it is required rather than cosmetic. The
 * vanilla guards against stacking listeners with a flag on the canvas ELEMENT
 * (`canvas._artMapListenersAttached`), which works because index.html declares
 * that canvas once and it outlives every open/close. Its `resize` and
 * `visibilitychange` listeners are therefore never removed — harmless there,
 * because they are never added twice either.
 *
 * Under React the canvas is created per mount, so that guard stops guarding
 * anything: each mount would attach a fresh set and the old ones would keep
 * firing against a dead canvas. `attachArtMapInteraction` therefore returns a
 * dispose function that removes everything it added. Same behaviour while
 * mounted; the difference only shows on unmount, where the vanilla had nothing
 * to unmount from.
 */

import {
  type ArtMapNode,
  artMap,
  artMapHitTest,
  artMapScreenToWorld,
} from './-discover.artist-map';

// ── Timings, transcribed ─────────────────────────────────────────────────────

/** How long a bubble must be hovered before its card replaces the top list (10113). */
export const ARTMAP_PANEL_HOVER_MS = 800;
/** How long before the connection constellation lights up (10133). */
export const ARTMAP_CONSTELLATION_MS = 220;
/** How long after zooming stops before the buffer is rebuilt at the new zoom (10011). */
export const ARTMAP_ZOOM_REBUILD_MS = 300;
/** How long after the last resize event before the map re-frames (10252). */
export const ARTMAP_RESIZE_MS = 160;
/** Past this many pixels a mouseup is a drag, not a click (10142). */
export const ARTMAP_DRAG_SLOP = 5;
/** Past this many pixels a touchend is a drag, not a tap (10216). */
export const ARTMAP_TAP_SLOP = 8;

// ── Zoom math ────────────────────────────────────────────────────────────────

export interface ArtMapZoomResult {
  zoom: number;
  offsetX: number;
  offsetY: number;
}

/**
 * One wheel notch (9995-10005).
 *
 * Zooms toward the CURSOR, not the canvas centre — the point under the pointer
 * stays put. Note the clamp runs to 5 here while the toolbar buttons stop at 3;
 * the wheel is allowed further in.
 */
export function artMapWheelZoom(deltaY: number, mx: number, my: number): ArtMapZoomResult {
  const delta = deltaY > 0 ? 0.9 : 1.1;
  const newZoom = Math.max(0.02, Math.min(5, artMap.zoom * delta));
  return {
    zoom: newZoom,
    offsetX: mx - (mx - artMap.offsetX) * (newZoom / artMap.zoom),
    offsetY: my - (my - artMap.offsetY) * (newZoom / artMap.zoom),
  };
}

/**
 * A pinch step (10192-10200).
 *
 * The same "keep the anchor point still" transform as the wheel, anchored on the
 * midpoint between the two fingers — but clamped to 3, matching the buttons
 * rather than the wheel.
 */
export function artMapPinchZoom(
  prevTouches: { clientX: number; clientY: number }[],
  touches: { clientX: number; clientY: number }[],
): ArtMapZoomResult {
  const prevDist = Math.hypot(
    prevTouches[1].clientX - prevTouches[0].clientX,
    prevTouches[1].clientY - prevTouches[0].clientY,
  );
  const curDist = Math.hypot(
    touches[1].clientX - touches[0].clientX,
    touches[1].clientY - touches[0].clientY,
  );
  const factor = curDist / prevDist;
  const cx = (touches[0].clientX + touches[1].clientX) / 2;
  const cy = (touches[0].clientY + touches[1].clientY) / 2;
  const newZoom = Math.max(0.02, Math.min(3, artMap.zoom * factor));
  return {
    zoom: newZoom,
    offsetX: cx - (cx - artMap.offsetX) * (newZoom / artMap.zoom),
    offsetY: cy - (cy - artMap.offsetY) * (newZoom / artMap.zoom),
  };
}

// ── Keyboard ─────────────────────────────────────────────────────────────────

export type ArtMapKeyAction =
  | 'close'
  | 'zoom-in'
  | 'zoom-out'
  | 'fit'
  | 'perf'
  | 'focus-search'
  | 'toggle-similar'
  | 'island-prev'
  | 'island-next'
  | null;

/**
 * Which shortcut a keypress means (10017-10038), or null for none.
 *
 * Typing in an input is never intercepted — otherwise 'f' in the search box
 * would refit the map instead of typing an f.
 *
 * Note 'h' (toggle similar) and the arrow keys are the only ones that do NOT
 * preventDefault in the vanilla; the arrows are also gated on one-island mode,
 * so they keep scrolling the page on a multi-island map.
 */
export function artMapKeyAction(
  key: string,
  targetTag: string | undefined,
  oneIsland: boolean | undefined,
): ArtMapKeyAction {
  if (targetTag === 'INPUT') return null;
  if (key === 'Escape') return 'close';
  if (key === '=' || key === '+') return 'zoom-in';
  if (key === '-') return 'zoom-out';
  if (key === '0') return 'fit';
  if (key === 'f' || key === 'F') return 'fit';
  if (key === 'd' || key === 'D') return 'perf';
  if (key === 's' || key === 'S') return 'focus-search';
  if (key === 'h' || key === 'H') return 'toggle-similar';
  if (oneIsland && key === 'ArrowLeft') return 'island-prev';
  if (oneIsland && key === 'ArrowRight') return 'island-next';
  return null;
}

/**
 * Which actions consume the event.
 *
 * Every branch except 'h' and the arrows — and 'focus-search', whose
 * preventDefault is conditional on actually finding the input, so the caller
 * decides that one.
 */
export function artMapKeyPreventsDefault(action: ArtMapKeyAction): boolean {
  return (
    action === 'close' ||
    action === 'zoom-in' ||
    action === 'zoom-out' ||
    action === 'fit' ||
    action === 'perf' ||
    action === 'island-prev' ||
    action === 'island-next'
  );
}

// ── Hit resolution for a tap ─────────────────────────────────────────────────

/**
 * The world point a touch lands on (10213-10214).
 *
 * The touch path does its own transform rather than reusing `artMapScreenToWorld`
 * — same arithmetic, but it reads the changed touch rather than a mouse event.
 */
export function artMapTouchToWorld(
  t: { clientX: number; clientY: number },
  rect: { left: number; top: number },
): { wx: number; wy: number } {
  return {
    wx: (t.clientX - rect.left - artMap.offsetX) / artMap.zoom,
    wy: (t.clientY - rect.top - artMap.offsetY) / artMap.zoom,
  };
}

/** Did the pointer move far enough between down and up to count as a drag (10142)? */
export function artMapWasDrag(
  start: { x: number; y: number } | null,
  end: { clientX: number; clientY: number },
): boolean {
  return !!(
    start &&
    (Math.abs(end.clientX - start.x) > ARTMAP_DRAG_SLOP ||
      Math.abs(end.clientY - start.y) > ARTMAP_DRAG_SLOP)
  );
}

/** Did the finger move far enough to count as a drag rather than a tap (10216)? */
export function artMapWasTouchDrag(
  start: { clientX: number; clientY: number },
  end: { clientX: number; clientY: number },
): boolean {
  return (
    Math.abs(end.clientX - start.clientX) > ARTMAP_TAP_SLOP ||
    Math.abs(end.clientY - start.clientY) > ARTMAP_TAP_SLOP
  );
}

// ── Wiring ───────────────────────────────────────────────────────────────────

/**
 * Everything the interaction layer needs from the rest of the map. Passed in
 * rather than imported so the wiring can be driven in a test without a canvas
 * renderer, and so a React component can route these to its own state.
 */
export interface ArtMapInteractionHost {
  /**
   * Whether the map is on screen (10018).
   *
   * The vanilla asks the DOM directly — `#artist-map-container` exists and is not
   * `display:none` — because its keydown listener lives on `window` and outlives
   * a close. It is a predicate here so the React component can answer from its
   * own state instead of a hard-coded element id, and so the shortcuts stay dead
   * while the Artist Web overlay is on top of a merely-hidden map.
   */
  isVisible(): boolean;
  render(): void;
  ensureAmbient(): void;
  emitRipple(wx: number, wy: number, hue?: number | null): void;
  showTooltip(e: { clientX: number; clientY: number } | null, node: ArtMapNode | null): void;
  showPanelArtist(node: ArtMapNode): void;
  animateConstellation(): void;
  showContextMenu(e: MouseEvent, node: ArtMapNode): void;
  hideContextMenu(): void;
  close(): void;
  zoom(factor: number): void;
  fitToView(): void;
  /**
   * Focus the toolbar search box; returns whether there was one to focus.
   *
   * The vanilla looks the input up and only calls preventDefault INSIDE the
   * `if (input)` (10026-10029) — so with no search box on the page, 's' still
   * types an s. The return value carries that.
   */
  focusSearch(): boolean;
  toggleSimilar(): void;
  islandNav(dir: number): void;
  /** Re-measure and re-frame after a resize settles. */
  resized(): void;
}

/**
 * Attach every listener the map needs, and return a dispose function.
 *
 * The hover handling is the subtle part. Two independent debounces run off the
 * SAME hover change: the side panel swaps its card only after ~0.8s, so sweeping
 * toward the panel does not keep rewriting it with every bubble passed en route;
 * the constellation lights up after ~0.22s, which is short enough to feel like a
 * response rather than a delay.
 */
export function attachArtMapInteraction(
  canvas: HTMLCanvasElement,
  host: ArtMapInteractionHost,
): () => void {
  let isPanning = false;
  let panStartX = 0;
  let panStartY = 0;
  let clickStart: { x: number; y: number; time: number } | null = null;
  let lastTouches: { clientX: number; clientY: number }[] | null = null;
  let zoomRebuild: ReturnType<typeof setTimeout> | undefined;
  let panelTimer: ReturnType<typeof setTimeout> | undefined;
  let constellationTimer: ReturnType<typeof setTimeout> | undefined;
  let resizeTimer: ReturnType<typeof setTimeout> | undefined;

  const onVisibility = () => {
    if (!document.hidden) host.ensureAmbient();
  };
  document.addEventListener('visibilitychange', onVisibility);

  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const next = artMapWheelZoom(e.deltaY, e.clientX - rect.left, e.clientY - rect.top);
    artMap.offsetX = next.offsetX;
    artMap.offsetY = next.offsetY;
    artMap.zoom = next.zoom;
    host.render(); // fast blit
    host.ensureAmbient(); // resume buoyancy if we zoomed bubbles into view
    // Debounce the hi-res rebuild until the zoom settles; the rebuild may flip
    // the live/overflow partition, so buoyancy is rechecked after it.
    clearTimeout(zoomRebuild);
    zoomRebuild = setTimeout(() => {
      artMap.dirty = true;
      host.render();
      host.ensureAmbient();
    }, ARTMAP_ZOOM_REBUILD_MS);
  };
  canvas.addEventListener('wheel', onWheel, { passive: false });

  const onKey = (e: KeyboardEvent) => {
    if (!host.isVisible()) return;
    const target = e.target as { tagName?: string } | null;
    const action = artMapKeyAction(e.key, target?.tagName, artMap._oneIsland);
    if (!action) return;
    // 'focus-search' is the one action whose preventDefault is conditional on
    // its own success, so it is handled before the blanket call.
    if (action === 'focus-search') {
      if (host.focusSearch()) e.preventDefault();
      return;
    }
    if (artMapKeyPreventsDefault(action)) e.preventDefault();
    if (action === 'close') host.close();
    else if (action === 'zoom-in') host.zoom(1.3);
    else if (action === 'zoom-out') host.zoom(0.7);
    else if (action === 'fit') host.fitToView();
    else if (action === 'perf') {
      artMap._perf = !artMap._perf;
      host.render();
    } else if (action === 'toggle-similar') {
      artMap._hideSimilar = !artMap._hideSimilar;
      artMap.dirty = true;
      host.render();
    } else if (action === 'island-prev') host.islandNav(-1);
    else if (action === 'island-next') host.islandNav(1);
  };
  window.addEventListener('keydown', onKey);

  const onContextMenu = (e: MouseEvent) => {
    e.preventDefault();
    const { nx, ny } = artMapScreenToWorld(e, canvas);
    const node = artMapHitTest(nx, ny);
    if (!node || node._isLabel) {
      host.hideContextMenu();
      return;
    }
    host.showContextMenu(e, node);
  };
  canvas.addEventListener('contextmenu', onContextMenu);

  const onMouseDown = (e: MouseEvent) => {
    if (e.button !== 0) return; // left button only
    clickStart = { x: e.clientX, y: e.clientY, time: Date.now() };
    isPanning = true;
    panStartX = e.clientX;
    panStartY = e.clientY;
  };
  canvas.addEventListener('mousedown', onMouseDown);

  const onMouseMove = (e: MouseEvent) => {
    if (isPanning) {
      artMap.offsetX += e.clientX - panStartX;
      artMap.offsetY += e.clientY - panStartY;
      panStartX = e.clientX;
      panStartY = e.clientY;
      host.render();
      return;
    }
    const { nx, ny } = artMapScreenToWorld(e, canvas);
    const prev = artMap.hoveredNode;
    artMap.hoveredNode = artMapHitTest(nx, ny);
    canvas.style.cursor = artMap.hoveredNode ? 'pointer' : 'grab';
    host.showTooltip(e, artMap.hoveredNode);
    if (prev !== artMap.hoveredNode) {
      clearTimeout(panelTimer);
      if (artMap.hoveredNode) {
        const target = artMap.hoveredNode;
        panelTimer = setTimeout(() => {
          if (artMap.hoveredNode === target) host.showPanelArtist(target);
        }, ARTMAP_PANEL_HOVER_MS);
      }
    }
    if (prev !== artMap.hoveredNode) {
      clearTimeout(constellationTimer);
      if (artMap._constellationActive) {
        artMap._constellationActive = false;
        host.animateConstellation(); // fade out
      }
      if (artMap.hoveredNode) {
        constellationTimer = setTimeout(() => {
          if (artMap.hoveredNode) {
            artMap._constellationActive = true;
            artMap._constellationFade = 0;
            artMap._constellationCache = null;
            host.animateConstellation();
          }
        }, ARTMAP_CONSTELLATION_MS);
      }
      host.render();
    }
  };
  canvas.addEventListener('mousemove', onMouseMove);

  const onMouseUp = (e: MouseEvent) => {
    if (e.button !== 0) return; // left button only
    const wasDrag = artMapWasDrag(clickStart, e);
    isPanning = false;

    if (!wasDrag && clickStart) {
      // A click is a deliberate select — ripple it and pin its card immediately,
      // bypassing the hover debounce. The card's Details button opens the full
      // modal; a click no longer auto-opens it.
      const { nx, ny } = artMapScreenToWorld(e, canvas);
      const node = artMapHitTest(nx, ny);
      host.emitRipple(node ? node.x : nx, node ? node.y : ny, node ? node._hue : null);
      if (node) {
        clearTimeout(panelTimer);
        host.showPanelArtist(node);
      }
    }

    clickStart = null;
    host.showTooltip(e, null);
  };
  canvas.addEventListener('mouseup', onMouseUp);

  const onMouseLeave = () => {
    host.showTooltip(null, null);
    clearTimeout(constellationTimer);
    if (artMap._constellationActive) {
      artMap._constellationActive = false;
      host.animateConstellation();
    }
    artMap.hoveredNode = null;
    host.render();
  };
  canvas.addEventListener('mouseleave', onMouseLeave);

  const onTouchStart = (e: TouchEvent) => {
    e.preventDefault();
    lastTouches = [...e.touches];
  };
  canvas.addEventListener('touchstart', onTouchStart, { passive: false });

  const onTouchMove = (e: TouchEvent) => {
    e.preventDefault();
    if (!lastTouches) return;
    const touches = [...e.touches];

    if (touches.length === 1 && lastTouches.length === 1) {
      artMap.offsetX += touches[0].clientX - lastTouches[0].clientX;
      artMap.offsetY += touches[0].clientY - lastTouches[0].clientY;
      host.render();
    } else if (touches.length === 2 && lastTouches.length === 2) {
      const next = artMapPinchZoom(lastTouches, touches);
      artMap.offsetX = next.offsetX;
      artMap.offsetY = next.offsetY;
      artMap.zoom = next.zoom;
      // Unlike the wheel, a pinch marks the buffer dirty every step rather than
      // debouncing — transcribed as-is.
      artMap.dirty = true;
      host.render();
    }
    lastTouches = touches;
  };
  canvas.addEventListener('touchmove', onTouchMove, { passive: false });

  const onTouchEnd = (e: TouchEvent) => {
    e.preventDefault();
    if (lastTouches && lastTouches.length === 1 && e.changedTouches.length === 1) {
      const t = e.changedTouches[0];
      const rect = canvas.getBoundingClientRect();
      const { wx, wy } = artMapTouchToWorld(t, rect);
      const node = artMapHitTest(wx, wy);
      const moved = artMapWasTouchDrag(lastTouches[0], t);
      if (!moved) {
        host.emitRipple(node ? node.x : wx, node ? node.y : wy, node ? node._hue : null);
        if (node) host.showPanelArtist(node); // a tap selects → card in the bottom sheet
      }
    }
    lastTouches = null;
  };
  canvas.addEventListener('touchend', onTouchEnd, { passive: false });

  const onResize = () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => host.resized(), ARTMAP_RESIZE_MS);
  };
  window.addEventListener('resize', onResize);

  return () => {
    clearTimeout(zoomRebuild);
    clearTimeout(panelTimer);
    clearTimeout(constellationTimer);
    clearTimeout(resizeTimer);
    document.removeEventListener('visibilitychange', onVisibility);
    window.removeEventListener('keydown', onKey);
    window.removeEventListener('resize', onResize);
    canvas.removeEventListener('wheel', onWheel);
    canvas.removeEventListener('contextmenu', onContextMenu);
    canvas.removeEventListener('mousedown', onMouseDown);
    canvas.removeEventListener('mousemove', onMouseMove);
    canvas.removeEventListener('mouseup', onMouseUp);
    canvas.removeEventListener('mouseleave', onMouseLeave);
    canvas.removeEventListener('touchstart', onTouchStart);
    canvas.removeEventListener('touchmove', onTouchMove);
    canvas.removeEventListener('touchend', onTouchEnd);
  };
}
