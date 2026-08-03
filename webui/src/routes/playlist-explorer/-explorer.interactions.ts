/**
 * The explorer's input handling (explorerToggleAlbum :553, explorerZoom :1038,
 * attachExplorerWheelZoom :1075, and the document pan listeners :1089-1128).
 *
 * Three separable things: the click discriminator that tells a selection from
 * a tracklist request, the zoom controller, and the drag-to-pan.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  clampExplorerZoom,
  explorerFitScrollLeft,
  explorerFitZoom,
  explorerWheelStep,
  isRealAlbumId,
} from './-explorer.core';

/** explorerToggleAlbum (:587) — how long a single click waits to see whether a second one lands. */
export const ALBUM_CLICK_DELAY_MS = 250;

export interface AlbumClickHandlers {
  onSelect: (albumId: string) => void;
  /** Only ever called for a real Spotify id; a positional key has no album to
   *  fetch, and the vanilla's fetch returned immediately for one (explorerExpandAlbumTracks :509). */
  onExpandTracks: (albumId: string) => void;
}

export interface AlbumClickController {
  click: (albumId: string) => void;
  dispose: () => void;
}

/**
 * Single click selects, double click opens the tracklist (explorerToggleAlbum :553).
 *
 * The pending click is a SINGLE slot, exactly as the vanilla's two module
 * globals were. Clicking a different album before the first one's timer fires
 * does not cancel it — that first selection still lands — and the first
 * timer's cleanup then clears the second album's pending state, so an
 * immediate double click on the second album reads as two single clicks. That
 * is the vanilla's behaviour, quirk and all.
 */
export function createAlbumClickController(
  handlers: AlbumClickHandlers,
  delayMs: number = ALBUM_CLICK_DELAY_MS,
): AlbumClickController {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let lastId: string | null = null;

  return {
    click(albumId: string) {
      if (lastId === albumId && timer) {
        clearTimeout(timer);
        timer = null;
        lastId = null;
        if (isRealAlbumId(albumId)) handlers.onExpandTracks(albumId);
        return;
      }
      lastId = albumId;
      timer = setTimeout(() => {
        timer = null;
        lastId = null;
        handlers.onSelect(albumId);
      }, delayMs);
    },
    dispose() {
      if (timer) clearTimeout(timer);
      timer = null;
      lastId = null;
    },
  };
}

export interface ExplorerZoomControls {
  zoom: number;
  zoomBy: (delta: number) => void;
  resetZoom: () => void;
  fitToView: () => void;
}

/**
 * Zoom state plus the wheel handler (explorerZoom :1038, explorerFitToView :1043,
 * attachExplorerWheelZoom :1075).
 *
 * The wheel listener is attached to the VIEWPORT, never to document. A
 * non-passive wheel listener on document disables the browser's compositor
 * scrolling for the whole app, so every scroll anywhere would then run through
 * the main thread. The vanilla also had to check whether the explorer page was
 * active; React does not, because the listener only exists while the route is
 * mounted.
 */
export function useExplorerZoom(
  viewportRef: React.RefObject<HTMLDivElement | null>,
  treeRef: React.RefObject<HTMLDivElement | null>,
): ExplorerZoomControls {
  const [zoom, setZoom] = useState(1);

  const zoomBy = useCallback((delta: number) => {
    setZoom((current) => clampExplorerZoom(current + delta));
  }, []);

  const resetZoom = useCallback(() => setZoom(1), []);

  const fitToView = useCallback(() => {
    const viewport = viewportRef.current;
    const tree = treeRef.current;
    if (!viewport || !tree) return;

    // scrollWidth/scrollHeight are LAYOUT sizes: a CSS transform does not
    // change them. The vanilla reset the transform to scale(1) before
    // measuring, which was belt and braces — the numbers are the same either
    // way, and not touching the DOM keeps React the only writer of that style.
    const next = explorerFitZoom(
      tree.scrollWidth,
      tree.scrollHeight,
      viewport.clientWidth,
      viewport.clientHeight,
    );
    setZoom(next);

    const scrollLeft = explorerFitScrollLeft(tree.scrollWidth, next, viewport.clientWidth);
    // After the new scale renders, so the viewport actually has the room.
    requestAnimationFrame(() => {
      viewport.scrollTop = 0;
      viewport.scrollLeft = scrollLeft;
    });
  }, [treeRef, viewportRef]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      zoomBy(explorerWheelStep(event.deltaY));
    };
    viewport.addEventListener('wheel', onWheel, { passive: false });
    return () => viewport.removeEventListener('wheel', onWheel);
  }, [viewportRef, zoomBy]);

  return { zoom, zoomBy, resetZoom, fitToView };
}

/**
 * Middle- or right-drag to pan (the document listeners at :1089-1128).
 *
 * The move and release listeners live on document only while a drag is in
 * flight, so a pointer that leaves the viewport mid-drag still pans and still
 * releases. The context menu is suppressed inside the viewport because
 * right-drag is the pan gesture.
 */
export function useExplorerPan(viewportRef: React.RefObject<HTMLDivElement | null>): void {
  const state = useRef<{ x: number; y: number; scrollX: number; scrollY: number } | null>(null);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    const onMouseMove = (event: MouseEvent) => {
      const start = state.current;
      if (!start) return;
      viewport.scrollLeft = start.scrollX - (event.clientX - start.x);
      viewport.scrollTop = start.scrollY - (event.clientY - start.y);
    };

    const stop = () => {
      if (!state.current) return;
      state.current = null;
      viewport.style.cursor = '';
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', stop);
    };

    const onMouseDown = (event: MouseEvent) => {
      // Middle (1) or right (2). Left-drag stays free for selection.
      if (event.button !== 1 && event.button !== 2) return;
      event.preventDefault();
      state.current = {
        x: event.clientX,
        y: event.clientY,
        scrollX: viewport.scrollLeft,
        scrollY: viewport.scrollTop,
      };
      viewport.style.cursor = 'grabbing';
      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', stop);
    };

    const onContextMenu = (event: MouseEvent) => event.preventDefault();

    viewport.addEventListener('mousedown', onMouseDown);
    viewport.addEventListener('contextmenu', onContextMenu);
    return () => {
      stop();
      viewport.removeEventListener('mousedown', onMouseDown);
      viewport.removeEventListener('contextmenu', onContextMenu);
    };
  }, [viewportRef]);
}
