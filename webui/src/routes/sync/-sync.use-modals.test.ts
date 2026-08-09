/**
 * The modal open-state store.
 */

import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useSyncModals } from './-sync.use-modals';

describe('opening and closing', () => {
  it('starts with nothing open', () => {
    const { result } = renderHook(() => useSyncModals());
    expect(result.current.open).toBeNull();
    expect(result.current.openIdFor('tidal')).toBeNull();
  });

  it('reports the open id for ITS source only', () => {
    const { result } = renderHook(() => useSyncModals());
    act(() => {
      result.current.openModal('tidal', '123');
    });
    expect(result.current.openIdFor('tidal')).toBe('123');
    // Every other source must read null, or nine modals would all try to open
    // on the same id.
    for (const other of ['qobuz', 'deezer', 'mirrored', 'listenbrainz'] as const) {
      expect(result.current.openIdFor(other), other).toBeNull();
    }
  });

  it('closes', () => {
    const { result } = renderHook(() => useSyncModals());
    act(() => {
      result.current.openModal('qobuz', 'q1');
    });
    act(() => {
      result.current.close();
    });
    expect(result.current.open).toBeNull();
    expect(result.current.openIdFor('qobuz')).toBeNull();
  });
});

describe('one at a time', () => {
  it('opening a second source REPLACES the first', () => {
    // Two open slots would let a user open Tidal's modal, switch tabs, open
    // Qobuz's, and stack two overlays — something the vanilla cannot produce,
    // because only one tab is ever active.
    const { result } = renderHook(() => useSyncModals());
    act(() => {
      result.current.openModal('tidal', '123');
    });
    act(() => {
      result.current.openModal('qobuz', 'q1');
    });
    expect(result.current.openIdFor('tidal')).toBeNull();
    expect(result.current.openIdFor('qobuz')).toBe('q1');
    expect(result.current.open).toEqual({ source: 'qobuz', sourceId: 'q1' });
  });

  it('re-opening the SAME source with a different id switches playlist', () => {
    const { result } = renderHook(() => useSyncModals());
    act(() => {
      result.current.openModal('mirrored', 'mirrored_1');
    });
    act(() => {
      result.current.openModal('mirrored', 'mirrored_2');
    });
    expect(result.current.openIdFor('mirrored')).toBe('mirrored_2');
  });
});

describe('identity', () => {
  it('keeps openModal and close stable across renders', () => {
    // Both are handed to every card and to the modal's onClose; a new identity
    // each render would re-render the lot.
    const { result, rerender } = renderHook(() => useSyncModals());
    const before = { openModal: result.current.openModal, close: result.current.close };
    rerender();
    expect(result.current.openModal).toBe(before.openModal);
    expect(result.current.close).toBe(before.close);
  });

  it('gives a NEW openIdFor once the open modal changes', () => {
    // It closes over `open`; a stale one would keep reporting the old id and
    // the modal would never move.
    const { result } = renderHook(() => useSyncModals());
    const before = result.current.openIdFor;
    act(() => {
      result.current.openModal('deezer', 'd1');
    });
    expect(result.current.openIdFor).not.toBe(before);
    expect(result.current.openIdFor('deezer')).toBe('d1');
  });
});
