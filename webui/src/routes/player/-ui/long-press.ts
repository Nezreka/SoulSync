/**
 * press and hold on a phone opens the same menu a right-click does. iOS
 * Safari never fires contextmenu on a long press, so download, watchlist
 * and add-to-playlist on cards couldn't be reached on an iPhone. touch and
 * pen only: a mouse has its right-click.
 */

import type React from 'react';

const HOLD_MS = 500;
const SLOP_PX = 10;

export interface PressPoint {
  clientX: number;
  clientY: number;
  preventDefault: () => void;
}

export function longPress(onHold: (at: PressPoint) => void) {
  let timer: number | undefined;
  let start: { x: number; y: number } | null = null;
  let fired = false;
  const cancel = () => {
    window.clearTimeout(timer);
    timer = undefined;
    start = null;
  };
  return {
    onPointerDown: (e: React.PointerEvent) => {
      if (e.pointerType === 'mouse') return;
      fired = false;
      start = { x: e.clientX, y: e.clientY };
      const at = { clientX: e.clientX, clientY: e.clientY, preventDefault: () => undefined };
      timer = window.setTimeout(() => {
        fired = true;
        start = null;
        onHold(at);
      }, HOLD_MS);
    },
    onPointerMove: (e: React.PointerEvent) => {
      if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > SLOP_PX) cancel();
    },
    onPointerUp: cancel,
    onPointerCancel: cancel,
    // the hold opened the menu: the tap that ends it doesn't also open the card
    onClickCapture: (e: React.MouseEvent) => {
      if (!fired) return;
      fired = false;
      e.preventDefault();
      e.stopPropagation();
    },
  };
}
