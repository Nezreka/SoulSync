import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { BpToast } from './-discover.use-build-playlist';

import { useBuildPlaylist } from './-discover.use-build-playlist';

let toasts: BpToast[] = [];
let searchHits: string[] = [];
let generateBodies: unknown[] = [];

const artist = (id: string) => ({ id, name: `Artist ${id}`, image_url: '' });

function stub({
  artists = [artist('a1'), artist('a2')],
  searchOk = true,
  playlist = { tracks: [{ track_name: 't1' }], metadata: { total_tracks: 1 } } as Record<
    string,
    unknown
  > | null,
  generateOk = true,
  generateError = undefined as string | undefined,
}: Record<string, unknown> = {}) {
  searchHits = [];
  generateBodies = [];
  server.use(
    http.get('/api/discover/build-playlist/search-artists', ({ request }) => {
      searchHits.push(new URL(request.url).searchParams.get('query') ?? '');
      if (!searchOk) return HttpResponse.json({ error: 'lastfm down' }, { status: 502 });
      return HttpResponse.json({ success: true, artists });
    }),
    http.post('/api/discover/build-playlist/generate', async ({ request }) => {
      generateBodies.push(await request.json());
      if (!generateOk) return HttpResponse.json({ success: false, error: generateError });
      return HttpResponse.json({ success: true, playlist });
    }),
  );
}

function mount() {
  return renderHook(() => useBuildPlaylist((t) => toasts.push(t)));
}

beforeEach(() => {
  toasts = [];
  stub();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  server.resetHandlers();
});

async function search(result: { current: ReturnType<typeof useBuildPlaylist> }, q: string) {
  act(() => result.current.setQuery(q));
  await act(() => vi.advanceTimersByTimeAsync(450));
}

describe('useBuildPlaylist — search', () => {
  it('debounces at 400ms; empty clears the area NOW without a request', async () => {
    const { result } = mount();
    act(() => result.current.setQuery('ap'));
    await act(() => vi.advanceTimersByTimeAsync(200));
    act(() => result.current.setQuery('aphex'));
    await act(() => vi.advanceTimersByTimeAsync(450));
    expect(searchHits).toEqual(['aphex']);
    expect(result.current.results).toHaveLength(2);
    act(() => result.current.setQuery(''));
    expect(result.current.results).toEqual([]);
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(searchHits).toEqual(['aphex']);
  });

  it('filters out already-selected seeds, with the all-selected message', async () => {
    const { result } = mount();
    await search(result, 'aphex');
    act(() => result.current.addSeed(artist('a1')));
    stub({ artists: [artist('a1')] });
    await search(result, 'aphex');
    expect(result.current.results).toEqual([]);
    expect(result.current.resultsMessage).toBe('All results already selected');

    stub({ artists: [] });
    await search(result, 'zzz');
    expect(result.current.resultsMessage).toBe('No artists found for "zzz"');
  });

  it('a failed search toasts the server error and leaves the area AS IT WAS', async () => {
    const { result } = mount();
    await search(result, 'aphex');
    expect(result.current.results).toHaveLength(2);
    stub({ searchOk: false });
    await search(result, 'aphex again');
    expect(toasts.at(-1)).toEqual({ message: 'lastfm down', level: 'error' });
    expect(result.current.results).toHaveLength(2); // untouched (10910-10913)
  });
});

describe('useBuildPlaylist — seeds', () => {
  it('adds up to five with distinct refusals, duplicate check FIRST', async () => {
    const { result } = mount();
    for (let i = 0; i < 5; i++) act(() => result.current.addSeed(artist(`a${i}`)));
    expect(result.current.selected).toHaveLength(5);
    // Re-adding while full says ALREADY SELECTED, not maximum reached.
    act(() => result.current.addSeed(artist('a0')));
    expect(toasts.at(-1)).toEqual({ message: 'Artist already selected', level: 'warning' });
    act(() => result.current.addSeed(artist('a9')));
    expect(toasts.at(-1)).toEqual({ message: 'Maximum 5 seed artists', level: 'warning' });
    act(() => result.current.removeSeed('a0'));
    expect(result.current.selected).toHaveLength(4);
  });

  it('adding a seed CLEARS the search box and results (10974-10977)', async () => {
    const { result } = mount();
    await search(result, 'aphex');
    expect(result.current.results).toHaveLength(2);
    act(() => result.current.addSeed(artist('a1')));
    expect(result.current.query).toBe('');
    expect(result.current.results).toEqual([]);
  });
});

describe('useBuildPlaylist — generate + download', () => {
  it('refuses with no seeds, generates with them, derives the subtitle', async () => {
    const { result } = mount();
    await act(() => result.current.generate());
    expect(toasts.at(-1)).toEqual({ message: 'Please select at least 1 artist', level: 'warning' });
    expect(generateBodies).toHaveLength(0);

    act(() => result.current.addSeed(artist('a1')));
    await act(() => result.current.generate());
    expect(generateBodies).toEqual([{ seed_artist_ids: ['a1'], playlist_size: 50 }]);
    expect(result.current.tracks).toHaveLength(1);
    expect(result.current.metadata).toEqual({ total_tracks: 1 });
    expect(result.current.resultSubtitle).toContain('Artist a1');
    expect(result.current.generating).toBe(false);
  });

  it('keeps the two failure modes distinct, hiding previous results', async () => {
    const { result } = mount();
    act(() => result.current.addSeed(artist('a1')));
    await act(() => result.current.generate());
    expect(result.current.tracks).toHaveLength(1);

    stub({ generateOk: false, generateError: 'seeds too obscure' });
    await act(() => result.current.generate());
    expect(toasts.at(-1)).toEqual({ message: 'seeds too obscure', level: 'error' });
    expect(result.current.tracks).toBeNull(); // previous results hidden (11094)

    stub({ playlist: { tracks: [], error: 'nothing matched' } });
    await act(() => result.current.generate());
    expect(toasts.at(-1)).toEqual({ message: 'nothing matched', level: 'error' });
  });

  it('downloads the RAW tracks under build_playlist_custom', async () => {
    const { result } = mount();
    expect(result.current.download()).toEqual({
      kind: 'no-tracks',
      toast: 'No playlist tracks available',
      level: 'warning',
    });
    act(() => result.current.addSeed(artist('a1')));
    await act(() => result.current.generate());
    const out = result.current.download();
    expect(out).toEqual({
      kind: 'ok',
      // Deliberately NOT discover_build_playlist — the module documents the
      // two ids as different systems for the same playlist.
      virtualId: 'build_playlist_custom',
      name: 'Custom Playlist - Artist a1',
      tracks: [{ track_name: 't1' }],
    });
  });
});
