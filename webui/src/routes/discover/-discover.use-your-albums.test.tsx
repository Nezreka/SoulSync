import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { AlbumsToast } from './-discover.use-your-albums';

import { useYourAlbums } from './-discover.use-your-albums';

let toasts: AlbumsToast[] = [];
let gridQueries: string[] = [];
let batchBodies: unknown[] = [];

const album = (i: number, over: Record<string, unknown> = {}) => ({
  album_name: `Album ${i}`,
  artist_name: 'A',
  spotify_album_id: `sp${i}`,
  total_tracks: 2,
  ...over,
});

function stub({
  albums = [album(0)],
  stats = { total: 10, owned: 6, missing: 4 },
  stale = false,
  missing = [album(0), album(1, { in_library: true })],
  ndjson = '{"album_id":"sp0","status":"done","tracks_added":2,"tracks_skipped":0}\n{"status":"complete","total_added":2,"total_skipped":0}\n',
}: Record<string, unknown> = {}) {
  gridQueries = [];
  batchBodies = [];
  server.use(
    http.get('/api/discover/your-albums', ({ request }) => {
      const url = new URL(request.url);
      gridQueries.push(url.search);
      if (url.searchParams.get('status') === 'missing') {
        return HttpResponse.json({ success: true, albums: missing, total: 2 });
      }
      return HttpResponse.json({ success: true, albums, total: 10, stats, stale });
    }),
    http.post('/api/artist/your-albums/download-discography', async ({ request }) => {
      batchBodies.push(await request.json());
      return new HttpResponse(ndjson as string, {
        headers: { 'Content-Type': 'application/x-ndjson' },
      });
    }),
  );
}

function mount() {
  return renderHook(() => useYourAlbums((t) => toasts.push(t)));
}

beforeEach(() => {
  toasts = [];
  stub();
});

afterEach(() => {
  vi.useRealTimers();
  server.resetHandlers();
});

describe('useYourAlbums — grid', () => {
  it('loads on mount, derives the subtitle and the Download button', async () => {
    const { result } = mount();
    expect(result.current.grid.phase).toBe('loading');
    await waitFor(() => expect(result.current.grid.phase).toBe('ready'));
    expect(result.current.grid.albums).toHaveLength(1);
    expect(result.current.grid.subtitle).toBe('10 albums · 6 owned · 4 missing');
    expect(result.current.grid.canDownloadMissing).toBe(true);
    expect(result.current.grid.hidden).toBe(false);
    expect(gridQueries[0]).toContain('page=1');
  });

  it('hides the whole section only when truly empty and NOT stale', async () => {
    stub({ albums: [], stats: { total: 0, owned: 0, missing: 0 } });
    const { result } = mount();
    await waitFor(() => expect(result.current.grid.phase).toBe('ready'));
    expect(result.current.grid.hidden).toBe(true);
    // Nothing missing → no Download button (1385).
    expect(result.current.grid.canDownloadMissing).toBe(false);
  });

  it('a stale zero-total payload shows the fetching state and polls to life', async () => {
    vi.useFakeTimers();
    stub({ albums: [], stats: { total: 0 }, stale: true });
    const { result } = mount();
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(result.current.grid.phase).toBe('stale');
    // Stale-and-building is NOT hidden — the section shows its fetching state.
    expect(result.current.grid.hidden).toBe(false);
    // The cache finishes building; the 5s poll notices and reloads.
    stub({ albums: [album(0)], stats: { total: 3, owned: 3, missing: 0 } });
    await act(() => vi.advanceTimersByTimeAsync(5100));
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(result.current.grid.phase).toBe('ready');
    expect(result.current.grid.albums).toHaveLength(1);
  });

  it('debounces ONLY the search at 400ms; status/sort reload now, page 1', async () => {
    vi.useFakeTimers();
    const { result } = mount();
    await act(() => vi.advanceTimersByTimeAsync(10));
    const before = gridQueries.length;
    act(() => result.current.grid.page(3));
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(gridQueries.at(-1)).toContain('page=3');
    act(() => result.current.grid.filter({ status: 'missing' }));
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(gridQueries.length).toBe(before + 2);
    expect(result.current.grid.state.page).toBe(1);
    act(() => result.current.grid.filter({ search: 'xtal' }));
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(gridQueries.length).toBe(before + 2); // not yet
    await act(() => vi.advanceTimersByTimeAsync(450));
    expect(gridQueries.at(-1)).toContain('search=xtal');
  });
});

describe('useYourAlbums — batch', () => {
  it('refuses when nothing is missing, with the outcome toast', async () => {
    stub({ missing: [] });
    const { result } = mount();
    await act(() => result.current.batch.openForMissing());
    expect(result.current.batch.open).toBe(false);
    expect(toasts.at(-1)).toEqual({ message: 'No missing albums to download', level: 'info' });
  });

  it('opens with only the un-owned rows, ALL preselected', async () => {
    const { result } = mount();
    await act(() => result.current.batch.openForMissing());
    expect(result.current.batch.open).toBe(true);
    // album(1) is in_library → filtered by the outcome BEFORE preparing rows.
    expect(result.current.batch.rows).toHaveLength(1);
    expect(result.current.batch.selected).toEqual([0]);
  });

  it('streams the ndjson: per-row progress, summary toast, done phase', async () => {
    const { result } = mount();
    await act(() => result.current.batch.openForMissing());
    await act(() => result.current.batch.submit());
    expect(batchBodies[0]).toMatchObject({
      artist_name: 'Your Albums',
      source: null,
      albums: [{ id: 'sp0', source: 'spotify' }],
    });
    expect(result.current.batch.phase).toBe('done');
    expect(result.current.batch.progress!.items['spotify-sp0']).toEqual({
      status: 'done',
      text: '2 added · 0 skipped',
    });
    expect(toasts.at(-1)).toEqual({ message: '2 tracks added to wishlist', level: 'success' });
  });

  it('a broken ndjson line never aborts the stream', async () => {
    stub({ ndjson: 'not json\n{"status":"complete","total_added":1,"total_skipped":0}\n' });
    const { result } = mount();
    await act(() => result.current.batch.openForMissing());
    await act(() => result.current.batch.submit());
    expect(result.current.batch.phase).toBe('done');
    expect(result.current.batch.progress!.totalAdded).toBe(1);
  });
});
