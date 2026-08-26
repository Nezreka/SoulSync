import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { LastfmToast } from './-discover.use-lastfm-radio';

import { useLastfmRadio } from './-discover.use-lastfm-radio';

let toasts: LastfmToast[] = [];
let searchHits: string[] = [];
let generateBodies: unknown[] = [];
let playlistHits = 0;

const playlist = (id: string, title: string) => ({
  playlist: { identifier: `https://listenbrainz.org/playlist/${id}`, title, creator: 'lastfm' },
});

function stub({
  configured = true,
  playlists = [playlist('r1', 'Radio: Xtal')],
  searchResults = [{ name: 'Xtal', artist: 'Aphex Twin', listeners: 100 }],
  generateOk = true,
  generateError = undefined as string | undefined,
}: Record<string, unknown> = {}) {
  searchHits = [];
  generateBodies = [];
  playlistHits = 0;
  server.use(
    http.get('/api/lastfm/configured', () => HttpResponse.json({ configured })),
    http.get('/api/discover/listenbrainz/lastfm-radio', () => {
      playlistHits += 1;
      return HttpResponse.json({ success: true, playlists });
    }),
    http.get('/api/lastfm/search/tracks', ({ request }) => {
      searchHits.push(new URL(request.url).searchParams.get('q') ?? '');
      return HttpResponse.json({ results: searchResults });
    }),
    http.post('/api/lastfm/radio/generate', async ({ request }) => {
      generateBodies.push(await request.json());
      return HttpResponse.json(
        generateOk ? { success: true } : { success: false, error: generateError },
      );
    }),
  );
}

function mount() {
  return renderHook(() => useLastfmRadio((t) => toasts.push(t)));
}

beforeEach(() => {
  toasts = [];
  stub();
});

afterEach(() => {
  vi.useRealTimers();
  server.resetHandlers();
});

describe('useLastfmRadio — configured gate', () => {
  it('shows the section and loads the persisted radios when configured', async () => {
    const { result } = mount();
    expect(result.current.configured).toBeNull();
    await waitFor(() => expect(result.current.configured).toBe(true));
    await waitFor(() => expect(result.current.mixes).toHaveLength(1));
    expect(result.current.mixes[0].key).toBe('lb-lastfm_radio-r1');
    expect(result.current.mixes[0].title).toBe('Radio: Xtal');
    expect(result.current.loaded).toBe(true);
  });

  it('stays hidden when NOT configured, and never asks for playlists', async () => {
    stub({ configured: false });
    const { result } = mount();
    await waitFor(() => expect(result.current.configured).toBe(false));
    expect(playlistHits).toBe(0);
  });

  it('a NON-OK probe (500) also leaves configured null', async () => {
    // HttpResponse.error() exercises the catch path; a 500 exercises the
    // !res.ok early-return — both must leave the section hidden.
    server.use(http.get('/api/lastfm/configured', () => HttpResponse.json({}, { status: 500 })));
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.configured).toBeNull();
  });

  it('an EMPTY playlists answer empties the shelf — no stale cards', async () => {
    const { result } = mount();
    await waitFor(() => expect(result.current.mixes).toHaveLength(1));
    // The next reload REFUSES (3252-3255): the shelf must clear, not keep
    // stale cards. (success:false is the distinguishing case — an empty
    // array clears either way, since mapping [] is also [].)
    server.use(
      http.get('/api/discover/listenbrainz/lastfm-radio', () =>
        HttpResponse.json({ success: false }),
      ),
    );
    await act(() => result.current.pick({ name: 'X', artist: 'A' }));
    expect(result.current.mixes).toEqual([]);
  });

  it('a dead probe leaves configured null — hidden, not broken', async () => {
    server.use(http.get('/api/lastfm/configured', () => HttpResponse.error()));
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.configured).toBeNull();
  });
});

describe('useLastfmRadio — search', () => {
  it('empties hide the dropdown NOW; short queries fire a timer that does nothing', async () => {
    vi.useFakeTimers();
    const { result } = mount();
    await act(() => vi.advanceTimersByTimeAsync(10));
    act(() => result.current.setQuery('xtal'));
    await act(() => vi.advanceTimersByTimeAsync(450));
    expect(result.current.dropdownOpen).toBe(true);
    // SHORT (but non-empty): the dropdown stays AS IT WAS (3251).
    act(() => result.current.setQuery('x'));
    await act(() => vi.advanceTimersByTimeAsync(450));
    expect(result.current.dropdownOpen).toBe(true);
    expect(searchHits).toEqual(['xtal']);
    // EMPTY: hides immediately, before any debounce.
    act(() => result.current.setQuery(''));
    expect(result.current.dropdownOpen).toBe(false);
  });

  it('debounces at 400ms and shows only the last answer', async () => {
    vi.useFakeTimers();
    const { result } = mount();
    await act(() => vi.advanceTimersByTimeAsync(10));
    act(() => result.current.setQuery('ap'));
    await act(() => vi.advanceTimersByTimeAsync(200));
    act(() => result.current.setQuery('aphex'));
    await act(() => vi.advanceTimersByTimeAsync(450));
    expect(searchHits).toEqual(['aphex']);
    expect(result.current.results).toHaveLength(1);
    expect(result.current.searching).toBe(false);
  });

  it('a 500 search TOASTS the failure — no longer disguised as no-results', async () => {
    vi.useFakeTimers();
    server.use(
      http.get('/api/lastfm/search/tracks', () =>
        HttpResponse.json({ error: 'lastfm down' }, { status: 500 }),
      ),
    );
    const { result } = mount();
    await act(() => vi.advanceTimersByTimeAsync(10));
    act(() => result.current.setQuery('xtal'));
    await act(() => vi.advanceTimersByTimeAsync(450));
    expect(toasts).toEqual([{ message: 'lastfm down', level: 'error' }]);
    expect(result.current.dropdownOpen).toBe(false);
    expect(result.current.results).toEqual([]);
    expect(result.current.searching).toBe(false);
  });

  it('dismiss closes the dropdown but KEEPS the typed query', async () => {
    vi.useFakeTimers();
    const { result } = mount();
    await act(() => vi.advanceTimersByTimeAsync(10));
    act(() => result.current.setQuery('xtal'));
    await act(() => vi.advanceTimersByTimeAsync(450));
    expect(result.current.dropdownOpen).toBe(true);
    act(() => result.current.dismiss());
    expect(result.current.dropdownOpen).toBe(false);
    expect(result.current.query).toBe('xtal');
  });

  it('NO results hides the dropdown — never an empty row', async () => {
    vi.useFakeTimers();
    stub({ searchResults: [] });
    const { result } = mount();
    await act(() => vi.advanceTimersByTimeAsync(10));
    act(() => result.current.setQuery('zzzz'));
    await act(() => vi.advanceTimersByTimeAsync(450));
    expect(result.current.dropdownOpen).toBe(false);
    expect(result.current.results).toEqual([]);
  });
});

describe('useLastfmRadio — generate', () => {
  it('picking generates immediately and reloads the radios on success', async () => {
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    const before = playlistHits;
    await act(() => result.current.pick({ name: 'Xtal', artist: 'Aphex Twin' }));
    expect(generateBodies).toEqual([{ track_name: 'Xtal', artist_name: 'Aphex Twin' }]);
    expect(playlistHits).toBe(before + 1);
    expect(result.current.generating).toBe(false);
    expect(toasts).toEqual([]);
  });

  it('picking confirms the choice in the input, then clears it on success', async () => {
    const loadStates = vi.fn();
    window.loadListenBrainzPlaylistsFromBackend = loadStates;
    try {
      const { result } = mount();
      await waitFor(() => expect(result.current.loaded).toBe(true));
      const pending = act(() => result.current.pick({ name: 'Xtal', artist: 'Aphex Twin' }));
      await pending;
      // success: input cleared, LB state map refreshed so the new card's
      // Sync button has a state to read
      expect(result.current.query).toBe('');
      expect(loadStates).toHaveBeenCalledTimes(1);
    } finally {
      delete window.loadListenBrainzPlaylistsFromBackend;
    }
  });

  it('a FAILED pick leaves the selection label in the input', async () => {
    stub({ generateOk: false, generateError: 'rate limited' });
    const loadStates = vi.fn();
    window.loadListenBrainzPlaylistsFromBackend = loadStates;
    try {
      const { result } = mount();
      await waitFor(() => expect(result.current.loaded).toBe(true));
      await act(() => result.current.pick({ name: 'Xtal', artist: 'Aphex Twin' }));
      expect(result.current.query).toBe('Xtal — Aphex Twin');
      expect(loadStates).not.toHaveBeenCalled();
    } finally {
      delete window.loadListenBrainzPlaylistsFromBackend;
    }
  });

  it('a refused generate toasts the SERVER message first, then the fallback', async () => {
    stub({ generateOk: false, generateError: 'rate limited' });
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(() => result.current.pick({ name: 'X', artist: 'A' }));
    expect(toasts).toEqual([{ message: 'rate limited', level: 'error' }]);

    stub({ generateOk: false });
    await act(() => result.current.pick({ name: 'X', artist: 'A' }));
    expect(toasts.at(-1)).toEqual({ message: 'Failed to generate radio', level: 'error' });
  });

  it('a dead generate endpoint toasts the error copy and unlocks', async () => {
    server.use(http.post('/api/lastfm/radio/generate', () => HttpResponse.error()));
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(() => result.current.pick({ name: 'X', artist: 'A' }));
    expect(toasts.at(-1)).toEqual({ message: 'Error generating Last.fm radio', level: 'error' });
    expect(result.current.generating).toBe(false);
  });
});
