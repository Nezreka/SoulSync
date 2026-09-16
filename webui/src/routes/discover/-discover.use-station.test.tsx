import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { StationSnapshot } from './-discover.stations';

import { useStationPreview } from './-discover.use-station';

/**
 * The station preview controller.
 *
 * Two acceptance criteria live here: opening a preview must not touch playback
 * at all, and a response for a station the user has moved on from must never
 * fill in the one that is open.
 */

const station = (id: string | number, name = 'Daft Punk') => ({
  artist_id: id,
  name,
  image_url: '',
  with: [] as string[],
});

function snapshot(id: string | number, revision = 1): StationSnapshot {
  return {
    snapshot_id: `${id}-r${revision}`,
    revision,
    station: { artist_id: id, name: 'Daft Punk', image_url: '' },
    tracks: [
      { id: '1', track_id: '1', track_name: 'A', artist_name: 'X', available: true },
      { id: '2', track_id: '2', track_name: 'B', artist_name: 'Y', available: true },
    ],
    counts: { returned: 2, available: 2, unavailable: 0 },
    status: 'ok',
  };
}

function stubOk(body: (url: string) => unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => ({ success: true, snapshot: body(url) }),
    })),
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('useStationPreview', () => {
  it('loads a snapshot without touching any playback global', async () => {
    const play = vi.fn();
    window.startArtistRadioById = play;
    window.playTrackList = vi.fn() as never;
    stubOk(() => snapshot(7));

    const { result } = renderHook(() => useStationPreview());
    act(() => result.current.open(station(7)));
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());

    expect(result.current.snapshot!.snapshot_id).toBe('7-r1');
    expect(play).not.toHaveBeenCalled();
    expect(window.playTrackList).not.toHaveBeenCalled();
  });

  it('marks only the clicked card pending', async () => {
    stubOk(() => snapshot(7));
    const { result } = renderHook(() => useStationPreview());
    act(() => result.current.open(station(7)));
    expect(result.current.pendingId).toBe('7');
    await waitFor(() => expect(result.current.pendingId).toBeNull());
  });

  it('ignores a second click on the same station while it resolves', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ success: true, snapshot: snapshot(7) }),
    }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStationPreview());
    act(() => result.current.open(station(7)));
    act(() => result.current.open(station(7)));
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('drops a response for a station the user has already left', async () => {
    let resolveFirst: ((v: unknown) => void) | null = null;
    const fetchMock = vi.fn((url: string) => {
      if (url.includes('/7/')) {
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ success: true, snapshot: snapshot(9) }),
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useStationPreview());
    act(() => result.current.open(station(7)));
    act(() => result.current.open(station(9, 'Justice')));
    await waitFor(() => expect(result.current.snapshot?.snapshot_id).toBe('9-r1'));

    // the first station's response lands late and must be discarded
    act(() => {
      resolveFirst?.({
        ok: true,
        status: 200,
        json: async () => ({ success: true, snapshot: snapshot(7) }),
      });
    });
    await waitFor(() => expect(result.current.snapshot?.snapshot_id).toBe('9-r1'));
  });

  it('a dismissed dialog is never reopened by a pending response', async () => {
    let resolve: ((v: unknown) => void) | null = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise((r) => {
            resolve = r;
          }),
      ),
    );
    const { result } = renderHook(() => useStationPreview());
    act(() => result.current.open(station(7)));
    act(() => result.current.close());
    act(() => {
      resolve?.({
        ok: true,
        status: 200,
        json: async () => ({ success: true, snapshot: snapshot(7) }),
      });
    });
    await waitFor(() => expect(result.current.station).toBeNull());
    expect(result.current.snapshot).toBeNull();
  });

  it('keeps the selection stable until a refresh cuts a new revision', async () => {
    let revision = 1;
    stubOk(() => snapshot(7, revision));
    const { result } = renderHook(() => useStationPreview());
    act(() => result.current.open(station(7)));
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());

    act(() => result.current.selectAll([0, 1]));
    expect(result.current.selection()).toHaveLength(2);

    revision = 2;
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.snapshot?.revision).toBe(2));
    // a new revision means the old indices no longer refer to the same rows
    expect(result.current.selected).toEqual([]);
  });

  it('reports a failure on the card AND in the dialog', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 500,
        json: async () => ({ success: false, error: 'no playable tracks' }),
      })),
    );
    const { result } = renderHook(() => useStationPreview());
    act(() => result.current.open(station(7)));
    await waitFor(() => expect(result.current.error).toBe('no playable tracks'));
    expect(result.current.cardErrors['7']).toBe('no playable tracks');
    expect(result.current.pendingId).toBeNull();
  });

  it('reset clears everything, for a profile switch', async () => {
    stubOk(() => snapshot(7));
    const { result } = renderHook(() => useStationPreview());
    act(() => result.current.open(station(7)));
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    act(() => result.current.reset());
    expect(result.current.station).toBeNull();
    expect(result.current.snapshot).toBeNull();
    expect(result.current.cardErrors).toEqual({});
  });

  it('toggles and clears a selection', async () => {
    stubOk(() => snapshot(7));
    const { result } = renderHook(() => useStationPreview());
    act(() => result.current.open(station(7)));
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    act(() => result.current.toggleTrack(1));
    expect(result.current.selected).toEqual([1]);
    act(() => result.current.toggleTrack(1));
    expect(result.current.selected).toEqual([]);
    act(() => result.current.selectAll([0, 1]));
    act(() => result.current.clearSelection());
    expect(result.current.selected).toEqual([]);
  });
});
