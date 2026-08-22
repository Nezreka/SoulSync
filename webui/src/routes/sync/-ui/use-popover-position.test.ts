/**
 * Keeping anchored popovers on screen.
 *
 * A card in the last column opened a menu that ran off the right edge, and one
 * near the bottom opened a tall menu whose lower half was below the fold — the
 * content was rendered, just unreachable.
 */

import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { clampToViewport, usePopoverPosition } from './use-popover-position';

const VIEWPORT = { width: 1000, height: 800 };
const SIZE = { width: 200, height: 300 };

describe('clampToViewport', () => {
  it('leaves a popover with room exactly where it was anchored', () => {
    expect(clampToViewport({ top: 100, left: 100 }, SIZE, VIEWPORT)).toEqual({
      top: 100,
      left: 100,
    });
  });

  it('slides it back in from the right edge', () => {
    // The last column of the grid: anchored at 950, 200 wide, so 150 off.
    expect(clampToViewport({ top: 100, left: 950 }, SIZE, VIEWPORT)).toEqual({
      top: 100,
      left: 792, // 1000 - 200 - 8
    });
  });

  it('slides it up from the bottom edge rather than flipping', () => {
    // Flipping above the trigger moves the menu away from the cursor, and for
    // a menu taller than the space above it, it fails anyway.
    expect(clampToViewport({ top: 700, left: 100 }, SIZE, VIEWPORT)).toEqual({
      top: 492, // 800 - 300 - 8
      left: 100,
    });
  });

  it('handles both edges at once — the bottom-right card', () => {
    expect(clampToViewport({ top: 780, left: 980 }, SIZE, VIEWPORT)).toEqual({
      top: 492,
      left: 792,
    });
  });

  it('never lets it escape the top or left', () => {
    expect(clampToViewport({ top: -50, left: -50 }, SIZE, VIEWPORT)).toEqual({
      top: 8,
      left: 8,
    });
  });

  it('keeps the TOP visible when the popover is taller than the viewport', () => {
    // Clipped at the bottom beats losing the top, which is where a menu's
    // first items are.
    const tall = { width: 200, height: 2000 };
    expect(clampToViewport({ top: 300, left: 100 }, tall, VIEWPORT)).toEqual({
      top: 8,
      left: 100,
    });
  });

  it('keeps the LEFT visible when the popover is wider than the viewport', () => {
    const wide = { width: 2000, height: 300 };
    expect(clampToViewport({ top: 100, left: 400 }, wide, VIEWPORT).left).toBe(8);
  });

  it('takes a custom margin', () => {
    expect(clampToViewport({ top: 100, left: 990 }, SIZE, VIEWPORT, 20).left).toBe(780);
  });
});

describe('usePopoverPosition', () => {
  /** The hook reads the REAL window, so the viewport is pinned here. */
  function setViewport(width: number, height: number) {
    Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: height, configurable: true });
  }

  function measuring(width: number, height: number) {
    const el = document.createElement('div');
    el.getBoundingClientRect = () =>
      ({
        width,
        height,
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }) as DOMRect;
    return { current: el };
  }

  it('measures the popover and moves it inside the viewport', () => {
    // Measured after layout on purpose: these menus grow and shrink — the
    // custom-interval field expands, the weekly section appears — so a guessed
    // height would clamp against the wrong number.
    setViewport(1000, 800);
    const ref = measuring(200, 300);
    const { result } = renderHook(() => usePopoverPosition({ top: 780, left: 980 }, ref));
    expect(result.current).toEqual({ top: 492, left: 792 });
  });

  it('leaves the anchor alone when it already fits — and settles instead of looping', () => {
    // A fresh ref object every render, which is what a caller not using useRef
    // gives you. Re-running the effect must not re-set an equal position, or
    // this renders until React gives up with "Maximum update depth exceeded".
    setViewport(1000, 800);
    const { result } = renderHook(() =>
      usePopoverPosition({ top: 100, left: 100 }, measuring(200, 300)),
    );
    expect(result.current).toEqual({ top: 100, left: 100 });
  });

  it('re-clamps when the popover GROWS while open', () => {
    // Opening "Custom interval…" adds a field, and a menu already sitting on
    // the bottom edge grows straight back off screen.
    setViewport(1000, 800);
    let height = 300;
    let fire = () => {};
    const observed: Element[] = [];
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(cb: () => void) {
          fire = cb;
        }
        observe(el: Element) {
          observed.push(el);
        }
        disconnect() {}
      },
    );

    const el = document.createElement('div');
    el.getBoundingClientRect = () => ({ width: 200, height }) as DOMRect;
    const { result } = renderHook(() =>
      usePopoverPosition({ top: 460, left: 100 }, { current: el }),
    );
    expect(result.current).toEqual({ top: 460, left: 100 }); // 800 - 300 - 8

    height = 500;
    act(() => {
      fire();
    });
    expect(observed).toContain(el);
    expect(result.current).toEqual({ top: 292, left: 100 }); // 800 - 500 - 8
    vi.unstubAllGlobals();
  });

  it('falls back to the raw anchor when there is nothing to measure', () => {
    const { result } = renderHook(() =>
      usePopoverPosition({ top: 42, left: 43 }, { current: null }),
    );
    expect(result.current).toEqual({ top: 42, left: 43 });
  });
});
