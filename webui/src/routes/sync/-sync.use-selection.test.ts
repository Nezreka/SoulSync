/**
 * The selection store — core.js 34 and sync-spotify.js 1800-1810.
 */

import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { syncOrderedSelection } from './-sync.sequential';
import { useSyncSelection } from './-sync.use-selection';

describe('the store', () => {
  it('starts empty', () => {
    const { result } = renderHook(() => useSyncSelection());
    expect(result.current.count).toBe(0);
    expect([...result.current.selected]).toEqual([]);
  });

  it('toggles an id in and back out', () => {
    const { result } = renderHook(() => useSyncSelection());
    act(() => {
      result.current.toggle('p1');
    });
    expect([...result.current.selected]).toEqual(['p1']);
    expect(result.current.count).toBe(1);

    act(() => {
      result.current.toggle('p1');
    });
    expect([...result.current.selected]).toEqual([]);
    expect(result.current.count).toBe(0);
  });

  it('holds several at once', () => {
    const { result } = renderHook(() => useSyncSelection());
    act(() => {
      result.current.toggle('a');
      result.current.toggle('b');
      result.current.toggle('c');
    });
    expect(result.current.count).toBe(3);
    act(() => {
      result.current.toggle('b');
    });
    expect([...result.current.selected].sort()).toEqual(['a', 'c']);
  });

  it('gives a NEW set each change, so consumers re-render', () => {
    // The sidebar's count and the Spotify tab's checkboxes both key off this.
    // Mutating the same Set in place would leave both stale.
    const { result } = renderHook(() => useSyncSelection());
    const before = result.current.selected;
    act(() => {
      result.current.toggle('p1');
    });
    expect(result.current.selected).not.toBe(before);
  });

  it('keeps a STABLE toggle across renders', () => {
    // It is handed to every playlist row; a new identity each render would
    // re-render the whole list on any unrelated state change.
    const { result, rerender } = renderHook(() => useSyncSelection());
    const before = result.current.toggle;
    rerender();
    expect(result.current.toggle).toBe(before);
  });
});

describe('what the vanilla deliberately does NOT do', () => {
  it('exposes no clear — nothing in the vanilla ever empties the set', () => {
    // Every mutation app-wide is the add/delete pair in
    // togglePlaylistSelection; the other four references only read .size/.has.
    const { result } = renderHook(() => useSyncSelection());
    expect(Object.keys(result.current).sort()).toEqual(['count', 'selected', 'toggle']);
  });

  it('survives a finished sync still selected', () => {
    // The manager's complete() resets the QUEUE, never the selection, so the
    // sidebar goes straight back to "1 playlist selected" with Start Sync live.
    const { result } = renderHook(() => useSyncSelection());
    act(() => {
      result.current.toggle('a');
    });
    // Nothing in the store is called on completion — the count simply stands.
    expect(result.current.count).toBe(1);
  });

  it('keeps a stale id rather than pruning it, and the QUEUE drops it', () => {
    // Refreshing the playlists does not prune ids whose cards are gone. That is
    // safe only because the queue keeps solely what the page still lists —
    // these two behaviours are a pair and must not be changed apart.
    const { result } = renderHook(() => useSyncSelection());
    act(() => {
      result.current.toggle('gone');
      result.current.toggle('here');
    });
    expect(result.current.count).toBe(2);
    expect(syncOrderedSelection(['here'], result.current.selected)).toEqual(['here']);
  });
});
