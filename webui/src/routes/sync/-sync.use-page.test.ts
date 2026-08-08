/**
 * The page controller. These tests cover the WIRING — the part of this port
 * that has actually broken: which order the queue takes, which array the names
 * come from, and whether the sidebar's visibility follows the vanilla.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useSyncPage } from './-sync.use-page';
import { resetSequentialSyncStore } from './-sync.use-sequential';

const started: string[] = [];
const toasts: string[] = [];

beforeEach(() => {
  resetSequentialSyncStore();
  started.length = 0;
  toasts.length = 0;
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));
  window.startPlaylistSync = vi.fn((id: string) => {
    started.push(id);
  });
  // Nothing ever finishes syncing here — the queue's ORDER is what matters.
  window.isPlaylistSyncing = () => true;
  window.disablePlaylistSelection = vi.fn();
  window.updateRefreshButtonState = vi.fn();
  window.showToast = vi.fn((message: string) => {
    toasts.push(message);
  });
  window.getSyncAccountPlaylists = () => [
    { id: 'p1', name: 'Alpha' },
    { id: 99, name: 'Numeric' },
  ];
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.startPlaylistSync;
  delete window.isPlaylistSyncing;
  delete window.disablePlaylistSelection;
  delete window.updateRefreshButtonState;
  delete window.getSyncAccountPlaylists;
  delete window.showToast;
});

describe('the queue order', () => {
  it('comes from the REGISTERED rows, not the engine array', () => {
    // The engine's array lists p1 and 99. The tab has rendered only p1 — so a
    // selection of both must queue only p1. Queueing from the engine array
    // would sync a playlist whose card is gone.
    const { result } = renderHook(() => useSyncPage());
    act(() => {
      result.current.registerSpotifyRows(['p1']);
      result.current.selection.toggle('p1');
      result.current.selection.toggle('99');
    });
    act(() => {
      result.current.onStartSync();
    });
    expect(started).toEqual(['p1']);
  });

  it('keeps the registered order, not the click order', () => {
    const { result } = renderHook(() => useSyncPage());
    act(() => {
      result.current.registerSpotifyRows(['a', 'b']);
      result.current.selection.toggle('b');
      result.current.selection.toggle('a');
    });
    act(() => {
      result.current.onStartSync();
    });
    expect(started).toEqual(['a']); // 'a' first; 'b' waits on it
  });

  it('refuses, with the vanilla toast, when nothing is selected', () => {
    const { result } = renderHook(() => useSyncPage());
    act(() => {
      result.current.registerSpotifyRows(['a']);
    });
    act(() => {
      result.current.onStartSync();
    });
    expect(toasts).toContain('No playlists selected for sync');
    expect(started).toEqual([]);
  });
});

describe('the sidebar label', () => {
  it('resolves names from the ENGINE array, including the id it coerces', () => {
    const { result } = renderHook(() => useSyncPage());
    act(() => {
      result.current.registerSpotifyRows(['99']);
      result.current.selection.toggle('99');
    });
    act(() => {
      result.current.onStartSync();
    });
    // The engine lists it as a NUMBER; the queue holds a string.
    expect(result.current.actions.currentName).toBe('Numeric');
  });

  it('carries the selected count through while idle', () => {
    const { result } = renderHook(() => useSyncPage());
    act(() => {
      result.current.selection.toggle('p1');
    });
    expect(result.current.actions).toMatchObject({ running: false, selectedCount: 1 });
  });
});

describe('sidebar visibility', () => {
  it('is hidden at rest and shown once a run starts', () => {
    const { result } = renderHook(() => useSyncPage());
    expect(result.current.sidebarVisible).toBe(false);
    act(() => {
      result.current.registerSpotifyRows(['a']);
      result.current.selection.toggle('a');
    });
    act(() => {
      result.current.onStartSync();
    });
    expect(result.current.sidebarVisible).toBe(true);
    expect(result.current.locked).toBe(true);
  });

  it('is hidden again by a tab switch, even mid-run', () => {
    // sync-services.js 3751 re-hides unconditionally. Transcribed.
    const { result } = renderHook(() => useSyncPage());
    act(() => {
      result.current.registerSpotifyRows(['a']);
      result.current.selection.toggle('a');
    });
    act(() => {
      result.current.onStartSync();
    });
    act(() => {
      result.current.onTabChange();
    });
    expect(result.current.sidebarVisible).toBe(false);
    // ...but the run itself is untouched.
    expect(result.current.actions.running).toBe(true);
  });

  it('a fresh start un-hides it again (4092)', () => {
    const { result } = renderHook(() => useSyncPage());
    act(() => {
      result.current.registerSpotifyRows(['a']);
      result.current.selection.toggle('a');
      result.current.onTabChange();
    });
    act(() => {
      result.current.onStartSync();
    });
    expect(result.current.sidebarVisible).toBe(true);
  });
});

describe('what it assembles', () => {
  it('builds nine verticals and ONE pipeline controller', () => {
    const { result } = renderHook(() => useSyncPage());
    expect(Object.keys(result.current.verticals)).toHaveLength(9);
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

  it('starts with no modal open', () => {
    const { result } = renderHook(() => useSyncPage());
    expect(result.current.modals.open).toBeNull();
    expect(result.current.modals.openIdFor('tidal')).toBeNull();
  });
});
