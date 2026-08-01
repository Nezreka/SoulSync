import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { DiscoverMix } from './-discover.mixes';

import { defaultLazySource, useMixModal } from './-discover.use-mix-modal';

const track = (name: string) => ({ track_name: name, artist_name: 'A', album_name: 'B' });

const registry: Record<string, DiscoverMix> = {
  popular_picks: {
    key: 'popular_picks',
    title: 'Popular Picks',
    subtitle: 's',
    syncKey: 'popular_picks',
    tracks: [track('t0'), track('t1'), track('t2')],
  },
  decade_1980: { key: 'decade_1980', title: '1980s', subtitle: '1980s Classics', trackCount: 2 },
};

let decadeHits = 0;

function stubDecade(tracks: unknown[] | 'error' = [track('d0'), track('d1')]) {
  decadeHits = 0;
  server.use(
    http.get('/api/discover/decade/1980', () => {
      decadeHits += 1;
      if (tracks === 'error') return HttpResponse.json({}, { status: 500 });
      return HttpResponse.json({ success: true, tracks });
    }),
  );
}

afterEach(() => {
  server.resetHandlers();
  vi.useRealTimers();
});

describe('useMixModal', () => {
  it('opens an eager mix with its own tracks, closed by default', () => {
    const { result } = renderHook(() => useMixModal(registry));
    expect(result.current.mix).toBeNull();
    act(() => result.current.open('popular_picks'));
    expect(result.current.mix!.key).toBe('popular_picks');
    expect(result.current.tracks).toHaveLength(3);
    expect(result.current.loading).toBe(false);
    act(() => result.current.close());
    expect(result.current.mix).toBeNull();
  });

  it('ignores a key the registry cannot resolve', () => {
    const { result } = renderHook(() => useMixModal(registry));
    act(() => result.current.open('nope'));
    expect(result.current.mix).toBeNull();
  });

  it('lazily fetches decade tracks on open, and caches across reopen', async () => {
    stubDecade();
    const { result } = renderHook(() => useMixModal(registry));
    act(() => result.current.open('decade_1980'));
    expect(result.current.loading).toBe(true);
    expect(result.current.tracks).toBeUndefined();
    await waitFor(() => expect(result.current.tracks).toHaveLength(2));
    expect(result.current.loading).toBe(false);
    // Reopen: instant, no second request — the vanilla mutates mix.tracks
    // (5012) for the same effect.
    act(() => result.current.close());
    act(() => result.current.open('decade_1980'));
    expect(result.current.tracks).toHaveLength(2);
    expect(decadeHits).toBe(1);
  });

  it('marks a failed lazy fetch as error, not as an empty list', async () => {
    stubDecade('error');
    const { result } = renderHook(() => useMixModal(registry));
    act(() => result.current.open('decade_1980'));
    await waitFor(() => expect(result.current.error).toBe(true));
    expect(result.current.tracks).toBeUndefined();
  });

  it('resets the selection on every open, and toggles per index', () => {
    const { result } = renderHook(() => useMixModal(registry));
    act(() => result.current.open('popular_picks'));
    act(() => result.current.toggleTrack(0));
    act(() => result.current.toggleTrack(2));
    expect(result.current.selected).toEqual([0, 2]);
    act(() => result.current.toggleTrack(0));
    expect(result.current.selected).toEqual([2]);
    act(() => result.current.selectAll([0, 1, 2]));
    expect(result.current.selected).toEqual([0, 1, 2]);
    act(() => result.current.clearSelection());
    expect(result.current.selected).toEqual([]);
    act(() => result.current.toggleTrack(1));
    act(() => result.current.open('popular_picks'));
    expect(result.current.selected).toEqual([]);
  });

  it('refuses an empty download with the vanilla info toast', () => {
    const { result } = renderHook(() => useMixModal(registry));
    act(() => result.current.open('popular_picks'));
    const out = result.current.downloadSelection();
    expect(out).toEqual({
      kind: 'none-selected',
      toast: 'Select at least one track first',
      level: 'info',
    });
  });

  it('builds the subset download: collision-proof id, "(N selected)" name', () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_753_000_000_000);
    const { result } = renderHook(() => useMixModal(registry));
    act(() => result.current.open('popular_picks'));
    act(() => result.current.toggleTrack(2));
    act(() => result.current.toggleTrack(0));
    const out = result.current.downloadSelection();
    expect(out.kind).toBe('ok');
    if (out.kind !== 'ok') return;
    // Keyed by the mix's OWN idBase + a timestamp, so a subset download never
    // collides with the whole-playlist download's state (4818).
    expect(out.virtualId).toBe('popular_picks_sel_1753000000000');
    expect(out.name).toBe('Popular Picks (2 selected)');
    expect(out.tracks).toHaveLength(2);
    expect(out.tracks[0]).toHaveProperty('name', 't2');
  });

  it('reports stale when every selected index points past the tracks', () => {
    const { result } = renderHook(() => useMixModal(registry));
    act(() => result.current.open('popular_picks'));
    act(() => result.current.selectAll([7, 8]));
    const out = result.current.downloadSelection();
    expect(out).toEqual({
      kind: 'stale',
      toast: 'Selected tracks are no longer available',
      level: 'error',
    });
  });

  it('lets a section register its own lazy family via extraLazy', async () => {
    const extra = (mix: DiscoverMix) =>
      mix.key === 'popular_picks' ? null : () => Promise.resolve([track('x')]);
    const bare: Record<string, DiscoverMix> = {
      lb_1: { key: 'lb_1', title: 'Weekly Jams', subtitle: 's' },
    };
    const { result } = renderHook(() => useMixModal(bare, extra));
    act(() => result.current.open('lb_1'));
    await waitFor(() => expect(result.current.tracks).toHaveLength(1));
  });

  it('drops a lazy result that lands after close', async () => {
    let resolve!: (t: unknown[]) => void;
    const extra = () => () => new Promise<unknown[]>((r) => (resolve = r));
    const bare: Record<string, DiscoverMix> = { lb_1: { key: 'lb_1', title: 'W', subtitle: 's' } };
    const { result } = renderHook(() => useMixModal(bare, extra));
    act(() => result.current.open('lb_1'));
    act(() => result.current.close());
    await act(async () => {
      resolve([track('late')]);
      await Promise.resolve();
    });
    act(() => result.current.open('lb_1'));
    // The stale resolution was discarded; this open fetches afresh.
    expect(result.current.loading).toBe(true);
  });

  it('defaultLazySource recognises ONLY decade keys', () => {
    expect(defaultLazySource({ key: 'decade_1980', title: '', subtitle: '' })).not.toBeNull();
    expect(defaultLazySource({ key: 'popular_picks', title: '', subtitle: '' })).toBeNull();
    expect(defaultLazySource({ key: 'decade_x', title: '', subtitle: '' })).toBeNull();
  });
});
