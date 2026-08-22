/**
 * Keep an anchored popover on screen.
 *
 * Every popover on this page is `position: fixed` at its trigger — the card's
 * overflow menu, the schedule picker, the Add-playlist sheet. Anchoring alone
 * is fine in the middle of the grid and wrong at its edges: a card in the last
 * column opens a menu that runs off the right, and one near the bottom opens a
 * tall menu whose lower half is below the fold. The content is rendered, just
 * unreachable, which is the same class of bug as the hover veil that swallowed
 * the schedule pill.
 *
 * CLAMP, DO NOT FLIP. Flipping above the trigger is the other common answer,
 * but it moves the menu somewhere the cursor is not, and for a menu taller than
 * the space above it, it fails anyway. Sliding it back inside the viewport
 * keeps it beside the thing it belongs to.
 *
 * Measured after layout, so it uses the popover's REAL size — these menus grow
 * and shrink (the custom-interval field expands, the weekly section appears),
 * and a guessed height would clamp against the wrong number.
 */

import type { RefObject } from 'react';

import { useLayoutEffect, useState } from 'react';

/** Breathing room between the popover and the viewport edge. */
const MARGIN = 8;

export interface AnchorPoint {
  top: number;
  left: number;
}

export function clampToViewport(
  anchor: AnchorPoint,
  size: { width: number; height: number },
  viewport: { width: number; height: number },
  margin = MARGIN,
): AnchorPoint {
  // max() after min() on purpose: on a viewport shorter than the popover the
  // min() would push it negative, and being clipped at the BOTTOM is better
  // than losing the top, which is where a menu's first items are.
  const left = Math.max(margin, Math.min(anchor.left, viewport.width - size.width - margin));
  const top = Math.max(margin, Math.min(anchor.top, viewport.height - size.height - margin));
  return { top, left };
}

export function usePopoverPosition(
  anchor: AnchorPoint,
  ref: RefObject<HTMLElement | null>,
): AnchorPoint {
  const [position, setPosition] = useState<AnchorPoint>(anchor);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;

    const measure = () => {
      const rect = el.getBoundingClientRect();
      const next = clampToViewport(
        anchor,
        { width: rect.width, height: rect.height },
        { width: window.innerWidth, height: window.innerHeight },
      );
      // Bail when nothing moved. setPosition with a fresh object re-renders
      // unconditionally, and a caller holding its ref anywhere but useRef would
      // then re-run this effect and set again — an infinite loop that appears
      // at one call site and nowhere else.
      setPosition((prev) => (prev.top === next.top && prev.left === next.left ? prev : next));
    };

    measure();

    // The popover changes size WHILE open — the custom-interval field expands,
    // an error line appears — and a menu already clamped to the bottom edge
    // grows straight back off screen. Measuring only on open would miss it.
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => {
      observer.disconnect();
    };
  }, [anchor.top, anchor.left, ref]);

  return position;
}
