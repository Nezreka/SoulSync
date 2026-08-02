import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { DiscoverHeroArtist } from './-discover.types';
import type { HeroToast } from './-discover.use-hero';

import { useHero } from './-discover.use-hero';

let toasts: HeroToast[] = [];
let checkBodies: string[] = [];
let batchBodies: unknown[] = [];
let watching: Record<string, boolean> = {};
let batchOk = true;

const artist = (id: string): DiscoverHeroArtist =>
  ({ artist_id: id, artist_name: `Artist ${id}` }) as DiscoverHeroArtist;

function stub() {
  checkBodies = [];
  batchBodies = [];
  server.use(
    http.post('/api/watchlist/check', async ({ request }) => {
      const body = (await request.json()) as { artist_id: string };
      checkBodies.push(body.artist_id);
      return HttpResponse.json({ success: true, is_watching: Boolean(watching[body.artist_id]) });
    }),
    http.post('/api/watchlist/add', () => HttpResponse.json({ success: true })),
    http.post('/api/watchlist/remove', () => HttpResponse.json({ success: true })),
    http.post('/api/watchlist/add-batch', async ({ request }) => {
      batchBodies.push(await request.json());
      return HttpResponse.json({ success: batchOk });
    }),
  );
}

function mount(artists: DiscoverHeroArtist[]) {
  return renderHook(() => useHero(artists, (t) => toasts.push(t)));
}

beforeEach(() => {
  toasts = [];
  watching = {};
  batchOk = true;
  stub();
});

afterEach(() => {
  vi.useRealTimers();
  server.resetHandlers();
});

describe('useHero — rotation', () => {
  it('auto-advances every 8s with more than one artist, wrapping', async () => {
    vi.useFakeTimers();
    const { result } = mount([artist('a'), artist('b')]);
    expect(result.current.index).toBe(0);
    await act(() => vi.advanceTimersByTimeAsync(8100));
    expect(result.current.index).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(8100));
    expect(result.current.index).toBe(0);
  });

  it('a LONE artist never rotates', async () => {
    vi.useFakeTimers();
    const { result } = mount([artist('a')]);
    await act(() => vi.advanceTimersByTimeAsync(20000));
    expect(result.current.index).toBe(0);
  });

  it('navigates with wrapping and jumps by dot', async () => {
    const { result } = mount([artist('a'), artist('b'), artist('c')]);
    act(() => result.current.navigate(-1));
    expect(result.current.index).toBe(2);
    act(() => result.current.jump(1));
    expect(result.current.index).toBe(1);
  });
});

describe('useHero — the watchlist button', () => {
  it('is NULL until the check answers, then reflects membership per slide', async () => {
    watching = { a: true, b: false };
    const { result } = mount([artist('a'), artist('b')]);
    expect(result.current.watchlist).toBeNull();
    await waitFor(() => expect(result.current.watchlist).not.toBeNull());
    expect(result.current.watchlist).toMatchObject({ watching: true, label: 'Watching...' });
    act(() => result.current.navigate(1));
    expect(result.current.watchlist).toBeNull(); // re-checking for the new slide
    await waitFor(() => expect(result.current.watchlist).not.toBeNull());
    expect(result.current.watchlist).toMatchObject({
      watching: false,
      label: 'Add to Watchlist',
    });
  });

  it('a SLOW check for the old slide never lands on the new one', async () => {
    // Gate slide a's answer until after slide b's arrived.
    let releaseA!: () => void;
    const gateA = new Promise<void>((r) => (releaseA = r));
    server.use(
      http.post('/api/watchlist/check', async ({ request }) => {
        const body = (await request.json()) as { artist_id: string };
        if (body.artist_id === 'a') {
          await gateA;
          return HttpResponse.json({ success: true, is_watching: true });
        }
        return HttpResponse.json({ success: true, is_watching: false });
      }),
    );
    const { result } = mount([artist('a'), artist('b')]);
    act(() => result.current.navigate(1)); // to b before a ever answers
    await waitFor(() => expect(result.current.watchlist).not.toBeNull());
    expect(result.current.watchlist?.watching).toBe(false); // b's answer
    releaseA();
    await act(async () => {
      await Promise.resolve();
    });
    // a's late answer was DROPPED — the button still shows b's state.
    expect(result.current.watchlist?.watching).toBe(false);
  });

  it('toggles through add/remove, toasting and flipping in place', async () => {
    const { result } = mount([artist('a')]);
    await waitFor(() => expect(result.current.watchlist).not.toBeNull());
    await act(() => result.current.toggleWatchlist());
    expect(toasts.at(-1)).toEqual({ message: 'Added Artist a to watchlist', level: 'success' });
    expect(result.current.watchlist?.watching).toBe(true);
    await act(() => result.current.toggleWatchlist());
    expect(toasts.at(-1)).toEqual({ message: 'Removed Artist a from watchlist', level: 'info' });
    expect(result.current.watchlist?.watching).toBe(false);
  });
});

describe('useHero — watch all', () => {
  it('starts done when EVERY artist is already watched (early-break probe)', async () => {
    watching = { a: true, b: true };
    const { result } = mount([artist('a'), artist('b')]);
    await waitFor(() => expect(result.current.watchAllPhase).toBe('done'));
  });

  it('one unwatched artist keeps it idle — and the probe stops there', async () => {
    watching = { a: false, b: true };
    const { result } = mount([artist('a'), artist('b')]);
    await waitFor(() => expect(result.current.watchlist).not.toBeNull());
    expect(result.current.watchAllPhase).toBe('idle');
    // The probe short-circuited: only 'a' was asked (plus the slide check).
    expect(checkBodies.filter((b) => b === 'b')).toHaveLength(0);
  });

  it('batches every artist, completes, and flips the current button', async () => {
    const { result } = mount([artist('a'), artist('b')]);
    await waitFor(() => expect(result.current.watchlist).not.toBeNull());
    await act(() => result.current.watchAll());
    expect(batchBodies).toEqual([
      {
        artists: [
          { artist_id: 'a', artist_name: 'Artist a' },
          { artist_id: 'b', artist_name: 'Artist b' },
        ],
      },
    ]);
    expect(result.current.watchAllPhase).toBe('done');
    expect(result.current.watchlist?.watching).toBe(true);
    // A done button is inert (601-602).
    await act(() => result.current.watchAll());
    expect(batchBodies).toHaveLength(1);
  });

  it('a refused batch returns to idle', async () => {
    batchOk = false;
    const { result } = mount([artist('a')]);
    await waitFor(() => expect(result.current.watchlist).not.toBeNull());
    await act(() => result.current.watchAll());
    expect(result.current.watchAllPhase).toBe('idle');
  });
});
