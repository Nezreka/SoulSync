/**
 * The page controller. These tests cover the WIRING: one pipeline controller,
 * and the mirrored refetch reaching every tab that registered one.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useSyncPage } from './-sync.use-page';

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('what it assembles', () => {
  it('builds the mirrored vertical and ONE pipeline controller', () => {
    const { result } = renderHook(() => useSyncPage());
    expect(result.current.mirrored.states).toEqual({});
    expect(typeof result.current.pipeline.run).toBe('function');
    expect(typeof result.current.pipeline.resume).toBe('function');
  });

  it('keeps the pipeline controller stable across renders', () => {
    // Two controllers would poll the same playlist twice; so would one that
    // was rebuilt on every render.
    const { result, rerender } = renderHook(() => useSyncPage());
    const before = result.current.pipeline;
    rerender();
    rerender();
    expect(result.current.pipeline).toBe(before);
  });

  it('keeps the mirrored vertical stable until its state changes', () => {
    // three tabs take it as a prop; a new object every render would re-run
    // every effect keyed on it
    const { result, rerender } = renderHook(() => useSyncPage());
    const before = result.current.mirrored;
    rerender();
    expect(result.current.mirrored).toBe(before);
    act(() => {
      result.current.mirrored.seed('mirrored_1');
    });
    expect(result.current.mirrored).not.toBe(before);
    expect(Object.keys(result.current.mirrored.states)).toEqual(['mirrored_1']);
  });

  it('routes reloadMirrored to whatever the mirrored tab registered', () => {
    // The import tab needs this refetch (sync-services.js 449-455) and the
    // pipeline controller needs the same one. A no-op here looks fine until a
    // file import leaves the mirrored list showing yesterday's rows.
    const reload = vi.fn();
    const { result } = renderHook(() => useSyncPage());
    result.current.reloadMirrored(); // before registration: must not throw
    expect(reload).not.toHaveBeenCalled();
    act(() => {
      result.current.registerMirroredReload(reload);
    });
    result.current.reloadMirrored();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('reloads both mirrored tabs, and a re-register replaces rather than stacks', () => {
    // a pipeline run from My Playlists has to refresh that tab too, not just
    // Mirrored. a remount registering again must not double the refetch.
    const mirrored = vi.fn();
    const mine = vi.fn();
    const { result } = renderHook(() => useSyncPage());
    act(() => {
      result.current.registerMirroredReload(mirrored);
      result.current.registerMirroredReload(mine, 'my-playlists');
      result.current.registerMirroredReload(mine, 'my-playlists');
    });
    result.current.reloadMirrored();
    expect(mirrored).toHaveBeenCalledTimes(1);
    expect(mine).toHaveBeenCalledTimes(1);
  });

  it('starts with no modal open', () => {
    const { result } = renderHook(() => useSyncPage());
    expect(result.current.modals.open).toBeNull();
    expect(result.current.modals.openIdFor('tidal')).toBeNull();
  });

  it('exposes the mirrored refetch on window for the vanilla match tool (#1289)', () => {
    const reload = vi.fn();
    const { result, unmount } = renderHook(() => useSyncPage());
    // Set on mount
    expect(window.reloadMirroredTab).toBe(result.current.reloadMirrored);
    // Delegates to the registered tab reload
    act(() => {
      result.current.registerMirroredReload(reload);
    });
    window.reloadMirroredTab?.();
    expect(reload).toHaveBeenCalledTimes(1);
    // Removed on unmount
    unmount();
    expect(window.reloadMirroredTab).toBeUndefined();
  });
});
