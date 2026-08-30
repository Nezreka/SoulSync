/**
 * The vertical registry. The point of these tests is drift: the table and the
 * registry must not be able to disagree, and the one per-source option that
 * silently empties two pages when missed must be provably reaching its source.
 */

import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { SourceVerticalConfig } from './-sync.sources';
import type { SourceVerticalOptions } from './-sync.use-vertical';

import { SYNC_SOURCES } from './-sync.sources';
import { SYNC_VERTICAL_IDS, useSyncVerticals } from './-sync.verticals';

describe('the id list cannot drift from the table', () => {
  it('covers every source in SYNC_SOURCES, and nothing else', () => {
    // A source added to the table but not here would never get a vertical —
    // its tab would render against an undefined and the failure would land
    // somewhere unrelated.
    expect([...SYNC_VERTICAL_IDS].sort()).toEqual(Object.keys(SYNC_SOURCES).sort());
  });

  it('is TEN, not sixteen — tabs and verticals are different counts', () => {
    // Last.fm rides ListenBrainz's vertical, Deezer-link rides Deezer's. If
    // this number ever equals the tab count, someone has conflated the two.
    expect(SYNC_VERTICAL_IDS).toHaveLength(10);
  });

  it('has no duplicates, which would break hook order', () => {
    expect(new Set(SYNC_VERTICAL_IDS).size).toBe(SYNC_VERTICAL_IDS.length);
  });
});

describe('the registry', () => {
  it('returns one vertical per id, each with the vertical contract', () => {
    const { result } = renderHook(() => useSyncVerticals());
    expect(Object.keys(result.current).sort()).toEqual([...SYNC_VERTICAL_IDS].sort());
    for (const id of SYNC_VERTICAL_IDS) {
      const vertical = result.current[id];
      expect(typeof vertical.seed, id).toBe('function');
      expect(typeof vertical.startDiscovery, id).toBe('function');
      expect(vertical.states, id).toEqual({});
    }
  });

  it('gives each source its OWN state, not one shared bag', () => {
    // The bug this forbids: a single vertical reused across sources, so
    // seeding a Tidal playlist makes it appear in Qobuz's states.
    const { result } = renderHook(() => useSyncVerticals());
    expect(result.current.tidal).not.toBe(result.current.qobuz);
    expect(result.current.tidal.states).not.toBe(result.current.qobuz.states);
  });

  it('keeps a stable vertical per id across re-renders', () => {
    // The WHOLE vertical, not just its states. The page hands each one down as
    // a prop, and it re-renders on every selection toggle, tab switch, sync
    // progress step and 3s log frame — a fresh object literal each time would
    // hand every tab a new `vertical` and re-run any effect keyed on it.
    const { result, rerender } = renderHook(() => useSyncVerticals());
    const before = result.current.mirrored;
    rerender();
    rerender();
    expect(result.current.mirrored).toBe(before);
    expect(result.current.mirrored.states).toBe(before.states);
  });

  it('but DOES hand out a new object once its state actually changes', () => {
    // The memo must not be so sticky that consumers miss real updates.
    const { result } = renderHook(() => useSyncVerticals());
    const before = result.current.tidal;
    act(() => {
      result.current.tidal.seed('p1');
    });
    expect(result.current.tidal).not.toBe(before);
    expect(Object.keys(result.current.tidal.states)).toEqual(['p1']);
    // ...and its neighbours are untouched.
    expect(result.current.qobuz.states).toEqual({});
  });
});

describe('the per-source options', () => {
  /**
   * Asserted against the REAL call, not inferred from the returned object —
   * the option is invisible on `SourceVertical`, so a version that dropped
   * `perSource` entirely, or handed it to the wrong source, would look
   * identical from outside. Mocking the vertical is the only way to see it.
   */
  it("hands each source ITS options and no one else's", async () => {
    vi.resetModules();
    const useSourceVertical = vi.fn(
      (_config: SourceVerticalConfig, _options: SourceVerticalOptions) => ({ states: {} }),
    );
    vi.doMock('./-sync.use-vertical', () => ({ useSourceVertical }));

    const { SYNC_SOURCES: TABLE } = await import('./-sync.sources');
    const { useSyncVerticals: build, SYNC_VERTICAL_IDS: IDS } = await import('./-sync.verticals');

    const onDiscoveryComplete = vi.fn();
    renderHook(() => build({ perSource: { listenbrainz: { onDiscoveryComplete } } }));

    expect(useSourceVertical).toHaveBeenCalledTimes(IDS.length);
    const byConfig = new Map(
      useSourceVertical.mock.calls.map(([config, options]) => [config, options]),
    );
    // The named source got its options...
    expect(byConfig.get(TABLE.listenbrainz)).toEqual({ onDiscoveryComplete });
    // ...and every other source got an empty object, not a shared reference to
    // listenbrainz's, and not undefined.
    for (const id of IDS) {
      if (id === 'listenbrainz') continue;
      expect(byConfig.get(TABLE[id]), id).toEqual({});
    }
    vi.doUnmock('./-sync.use-vertical');
  });

  it('passes each source its OWN config from the table', async () => {
    vi.resetModules();
    const useSourceVertical = vi.fn(
      (_config: SourceVerticalConfig, _options: SourceVerticalOptions) => ({ states: {} }),
    );
    vi.doMock('./-sync.use-vertical', () => ({ useSourceVertical }));

    const { SYNC_SOURCES: TABLE } = await import('./-sync.sources');
    const { useSyncVerticals: build, SYNC_VERTICAL_IDS: IDS } = await import('./-sync.verticals');
    renderHook(() => build());

    // Order matters as much as membership: it is what keeps hook order stable.
    expect(useSourceVertical.mock.calls.map(([config]) => config)).toEqual(
      IDS.map((id) => TABLE[id]),
    );
    vi.doUnmock('./-sync.use-vertical');
  });

  it('builds every source when no options are given at all', () => {
    const { result } = renderHook(() => useSyncVerticals());
    for (const id of SYNC_VERTICAL_IDS) {
      expect(result.current[id], id).toBeDefined();
    }
  });
});
