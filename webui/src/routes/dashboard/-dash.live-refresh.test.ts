import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useLiveRefresh } from './-dash.live-refresh';

/**
 * Reported: "if i listen to something it doesn't appear on recently played
 * unless i refresh page."
 *
 * The band already polled every 60s, but skipped while the tab was hidden and
 * had no catch-up on return — so listening in another tab and coming back
 * meant waiting out the rest of the interval. Pausing while hidden is right;
 * pausing without catching up just moves the staleness to the moment the user
 * is actually looking.
 */

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    get: () => hidden,
  });
}

describe('useLiveRefresh', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setHidden(false);
  });

  afterEach(() => {
    vi.useRealTimers();
    setHidden(false);
  });

  it('loads immediately on mount', () => {
    const load = vi.fn();

    renderHook(() => useLiveRefresh(load, { intervalMs: 1000 }));

    expect(load).toHaveBeenCalledTimes(1);
  });

  it('polls on the interval while visible', () => {
    const load = vi.fn();
    renderHook(() => useLiveRefresh(load, { intervalMs: 1000 }));

    act(() => void vi.advanceTimersByTime(3000));

    expect(load).toHaveBeenCalledTimes(4); // mount + 3 ticks
  });

  it('does no work while the tab is hidden', () => {
    const load = vi.fn();
    renderHook(() => useLiveRefresh(load, { intervalMs: 1000 }));
    load.mockClear();
    setHidden(true);

    act(() => void vi.advanceTimersByTime(5000));

    expect(load).not.toHaveBeenCalled();
  });

  it('catches up the moment the tab becomes visible again', () => {
    // The half that was missing, and the whole reported bug.
    const load = vi.fn();
    renderHook(() => useLiveRefresh(load, { intervalMs: 60_000 }));
    setHidden(true);
    act(() => void vi.advanceTimersByTime(120_000));
    load.mockClear();

    setHidden(false);
    act(() => void document.dispatchEvent(new Event('visibilitychange')));

    expect(load).toHaveBeenCalledTimes(1);
  });

  it('catches up on window focus too', () => {
    // Some browsers fire focus without a visibilitychange when switching
    // between windows rather than tabs.
    const load = vi.fn();
    renderHook(() => useLiveRefresh(load, { intervalMs: 60_000 }));
    load.mockClear();

    act(() => void window.dispatchEvent(new Event('focus')));

    expect(load).toHaveBeenCalledTimes(1);
  });

  it('does not refetch when a visibility event fires while still hidden', () => {
    const load = vi.fn();
    renderHook(() => useLiveRefresh(load, { intervalMs: 60_000 }));
    load.mockClear();
    setHidden(true);

    act(() => void document.dispatchEvent(new Event('visibilitychange')));

    expect(load).not.toHaveBeenCalled();
  });

  it('keeps polling on a stable timer when the callback is redefined', () => {
    // The callback is a new function every render. If the effect depended on
    // it, the interval would be torn down and rebuilt each time and — at a
    // short interval — never fire at all.
    const load = vi.fn();
    const { rerender } = renderHook(() => useLiveRefresh(() => load(), { intervalMs: 1000 }));
    rerender();
    rerender();
    load.mockClear();

    act(() => void vi.advanceTimersByTime(2000));

    expect(load).toHaveBeenCalledTimes(2);
  });

  it('calls the LATEST callback, not the one from mount', () => {
    const first = vi.fn();
    const second = vi.fn();
    let current = first;
    const { rerender } = renderHook(() => useLiveRefresh(() => current(), { intervalMs: 1000 }));
    current = second;
    rerender();

    act(() => void vi.advanceTimersByTime(1000));

    expect(second).toHaveBeenCalled();
    expect(first).toHaveBeenCalledTimes(1); // the mount call only
  });

  it('stops entirely once unmounted', () => {
    const load = vi.fn();
    const { unmount } = renderHook(() => useLiveRefresh(load, { intervalMs: 1000 }));
    unmount();
    load.mockClear();

    act(() => void vi.advanceTimersByTime(5000));
    act(() => void document.dispatchEvent(new Event('visibilitychange')));

    expect(load).not.toHaveBeenCalled();
  });

  it('does nothing at all when disabled', () => {
    const load = vi.fn();

    renderHook(() => useLiveRefresh(load, { intervalMs: 1000, enabled: false }));
    act(() => void vi.advanceTimersByTime(5000));

    expect(load).not.toHaveBeenCalled();
  });

  it('can opt out of the visibility catch-up', () => {
    const load = vi.fn();
    renderHook(() => useLiveRefresh(load, { intervalMs: 60_000, refreshOnVisible: false }));
    load.mockClear();

    act(() => void document.dispatchEvent(new Event('visibilitychange')));

    expect(load).not.toHaveBeenCalled();
  });
});
