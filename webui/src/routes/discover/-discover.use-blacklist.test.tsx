import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { BlacklistToast } from './-discover.use-blacklist';

import { useBlacklist } from './-discover.use-blacklist';

let toasts: BlacklistToast[] = [];
let searchHits: string[] = [];
let listHits = 0;
let posted: unknown[] = [];
let deleted: string[] = [];

function stub({
  entries = [{ id: 1, artist_name: 'Nickelback' }],
  searchArtists = [{ name: 'Creed' }],
  blockOk = true,
  listFails = false,
}: {
  entries?: unknown[];
  searchArtists?: unknown[];
  blockOk?: boolean;
  listFails?: boolean;
} = {}) {
  searchHits = [];
  listHits = 0;
  posted = [];
  deleted = [];
  server.use(
    http.get('/api/discover/artist-blacklist', () => {
      listHits += 1;
      if (listFails) return HttpResponse.error();
      return HttpResponse.json({ success: true, entries });
    }),
    http.post('/api/discover/artist-blacklist', async ({ request }) => {
      posted.push(await request.json());
      return HttpResponse.json({ success: blockOk });
    }),
    http.delete('/api/discover/artist-blacklist/:id', ({ params }) => {
      deleted.push(String(params.id));
      return HttpResponse.json({ success: true });
    }),
    http.post('/api/enhanced-search', async ({ request }) => {
      const body = (await request.json()) as { query: string };
      searchHits.push(body.query);
      return HttpResponse.json({ spotify_artists: searchArtists });
    }),
  );
}

function mount() {
  return renderHook(() => useBlacklist((t) => toasts.push(t)));
}

beforeEach(() => {
  toasts = [];
  stub();
});

afterEach(() => {
  vi.useRealTimers();
  server.resetHandlers();
});

describe('useBlacklist', () => {
  it('opens fresh — empty query, hidden dropdown — and loads the list', async () => {
    const { result } = mount();
    expect(result.current.open).toBe(false);
    act(() => result.current.openModal());
    expect(result.current.open).toBe(true);
    expect(result.current.query).toBe('');
    expect(result.current.results).toBeNull();
    expect(result.current.listPhase).toBe('loading');
    await waitFor(() => expect(result.current.listPhase).toBe('ready'));
    expect(result.current.entries).toEqual([{ id: 1, artist_name: 'Nickelback' }]);
  });

  it('REOPENS fresh after a close left search residue', async () => {
    // The vanilla rebuilds the whole overlay per open (5059) — a reopen must
    // not show the previous session's query or dropdown.
    vi.useFakeTimers();
    const { result } = mount();
    act(() => result.current.openModal());
    act(() => result.current.setQuery('creed'));
    await act(() => vi.advanceTimersByTimeAsync(350));
    expect(result.current.results).not.toBeNull();
    act(() => result.current.closeModal());
    act(() => result.current.openModal());
    expect(result.current.query).toBe('');
    expect(result.current.results).toBeNull();
  });

  it('marks a dead list endpoint as error', async () => {
    stub({ listFails: true });
    const { result } = mount();
    act(() => result.current.openModal());
    await waitFor(() => expect(result.current.listPhase).toBe('error'));
  });

  it('gates short queries BEFORE the debounce — dropdown hides instantly', async () => {
    vi.useFakeTimers();
    const { result } = mount();
    act(() => result.current.openModal());
    act(() => result.current.setQuery('ap'));
    await act(() => vi.advanceTimersByTimeAsync(350));
    expect(searchHits).toEqual(['ap']);
    // Shrinking to one char hides the dropdown NOW and schedules nothing.
    act(() => result.current.setQuery('a'));
    expect(result.current.results).toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(searchHits).toEqual(['ap']);
  });

  it('debounces at 300ms and keeps only the last query', async () => {
    vi.useFakeTimers();
    const { result } = mount();
    act(() => result.current.setQuery('cre'));
    await act(() => vi.advanceTimersByTimeAsync(200));
    act(() => result.current.setQuery('creed'));
    await act(() => vi.advanceTimersByTimeAsync(350));
    expect(searchHits).toEqual(['creed']);
    expect(result.current.results).toEqual([{ name: 'Creed' }]);
  });

  it('a successful block toasts, CLEARS the search, and reloads the list', async () => {
    vi.useFakeTimers();
    const { result } = mount();
    act(() => result.current.openModal());
    await act(() => vi.advanceTimersByTimeAsync(0));
    const listHitsBefore = listHits;
    act(() => result.current.setQuery('creed'));
    await act(() => vi.advanceTimersByTimeAsync(350));
    await act(() => result.current.block('Creed'));
    expect(posted).toEqual([{ artist_name: 'Creed' }]);
    expect(toasts).toEqual([{ message: 'Blocked Creed from discovery', level: 'success' }]);
    expect(result.current.query).toBe('');
    expect(result.current.results).toBeNull();
    expect(listHits).toBe(listHitsBefore + 1);
  });

  it('a refused block changes nothing — no toast, search intact', async () => {
    vi.useFakeTimers();
    stub({ blockOk: false });
    const { result } = mount();
    act(() => result.current.setQuery('creed'));
    await act(() => vi.advanceTimersByTimeAsync(350));
    await act(() => result.current.block('Creed'));
    expect(toasts).toEqual([]);
    expect(result.current.query).toBe('creed');
    expect(result.current.results).not.toBeNull();
  });

  it('unblocks by id, toasts by name, and reloads', async () => {
    const { result } = mount();
    act(() => result.current.openModal());
    await waitFor(() => expect(result.current.listPhase).toBe('ready'));
    const before = listHits;
    await act(() => result.current.unblock({ id: 7, artist_name: 'Nickelback' }));
    expect(deleted).toEqual(['7']);
    expect(toasts).toEqual([{ message: 'Unblocked Nickelback', level: 'success' }]);
    await waitFor(() => expect(listHits).toBe(before + 1));
  });
});
