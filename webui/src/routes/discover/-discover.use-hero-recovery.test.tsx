import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import type { DiscoverHeroArtist } from './-discover.types';
import type { HeroToast } from './-discover.use-hero';

import { useHero } from './-discover.use-hero';

const artist = (id: string): DiscoverHeroArtist =>
  ({ artist_id: id, artist_name: `Artist ${id}` }) as DiscoverHeroArtist;
const artists = [artist('a'), artist('b')];
let toasts: HeroToast[];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => (resolve = done));
  return { promise, resolve };
}

function mount(initialArtists = artists) {
  return renderHook(({ list }) => useHero(list, (toast) => toasts.push(toast)), {
    initialProps: { list: initialArtists },
  });
}

beforeEach(() => {
  toasts = [];
  server.use(
    http.post('/api/watchlist/check', () =>
      HttpResponse.json({ success: true, is_watching: false }),
    ),
    http.post('/api/watchlist/add', () =>
      HttpResponse.json({ success: true, message: 'Added artist to watchlist' }),
    ),
  );
});

describe('hero truthful watchlist acknowledgements', () => {
  it.each([
    ['business refusal', () => HttpResponse.json({ success: false, error: 'Refused' })],
    ['HTTP failure', () => HttpResponse.json({ success: true }, { status: 500 })],
    ['missing acknowledgement', () => HttpResponse.json({ message: 'OK' })],
    ['wrong acknowledgement type', () => HttpResponse.json({ success: 'true' })],
    ['invalid JSON', () => new HttpResponse('not JSON')],
    ['network failure', () => HttpResponse.error()],
  ])('keeps prior membership and reports %s, allowing retry', async (_name, response) => {
    server.use(http.post('/api/watchlist/add', response));
    const { result } = mount();
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
    await act(() => result.current.toggleWatchlist());
    expect(result.current.watchlist?.watching).toBe(false);
    expect(toasts).toEqual([expect.objectContaining({ level: 'error' })]);
    expect(result.current.watchlistPending).toBe(false);
    server.use(http.post('/api/watchlist/add', () => HttpResponse.json({ success: true })));
    await act(() => result.current.toggleWatchlist());
    expect(result.current.watchlist?.watching).toBe(true);
    expect(toasts.at(-1)?.level).toBe('success');
  });

  it('preserves confirmed Watching when remove is refused', async () => {
    server.use(
      http.post('/api/watchlist/check', () =>
        HttpResponse.json({ success: true, is_watching: true }),
      ),
      http.post('/api/watchlist/remove', () => HttpResponse.json({ success: false })),
    );
    const { result } = mount();
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(true));
    await act(() => result.current.toggleWatchlist());
    expect(result.current.watchlist?.watching).toBe(true);
    expect(toasts.at(-1)?.level).toBe('error');
  });

  it.each([
    { success: false },
    { success: true },
    {
      success: true,
      added: 1,
      skipped: 0,
      rejected: [{ artist_name: 'Artist b', reason: 'Refused' }],
    },
    { success: true, added: 1, skipped: 0, rejected: [] },
    { success: true, added: '2', skipped: 0, rejected: [] },
  ])('does not claim All Watched for incomplete batch acknowledgement %j', async (body) => {
    server.use(http.post('/api/watchlist/add-batch', () => HttpResponse.json(body)));
    const { result } = mount();
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
    await act(() => result.current.watchAll());
    expect(result.current.watchAllPhase).toBe('idle');
    expect(result.current.watchlist?.watching).toBe(false);
    expect(toasts.at(-1)?.level).toBe('error');
    expect(result.current.watchlistPending).toBe(false);
  });

  it.each([
    [
      'HTTP failure',
      () =>
        HttpResponse.json({ success: true, added: 2, skipped: 0, rejected: [] }, { status: 500 }),
    ],
    ['malformed JSON', () => new HttpResponse('not JSON')],
    ['network failure', () => HttpResponse.error()],
  ])('returns a failed batch to idle after %s', async (_name, response) => {
    server.use(http.post('/api/watchlist/add-batch', response));
    const { result } = mount();
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
    await act(() => result.current.watchAll());
    expect(result.current.watchAllPhase).toBe('idle');
    expect(result.current.watchlist?.watching).toBe(false);
    expect(result.current.watchlistPending).toBe(false);
    expect(toasts.at(-1)?.level).toBe('error');
  });

  it('accepts added plus already-watched batch acknowledgement and can retry a refusal', async () => {
    server.use(http.post('/api/watchlist/add-batch', () => HttpResponse.json({ success: false })));
    const { result } = mount();
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
    await act(() => result.current.watchAll());
    server.use(
      http.post('/api/watchlist/add-batch', () =>
        HttpResponse.json({
          success: true,
          added: 1,
          skipped: 1,
          rejected: [],
          message: 'Added 1 artist',
        }),
      ),
    );
    await act(() => result.current.watchAll());
    expect(result.current.watchAllPhase).toBe('done');
    expect(result.current.watchlist?.watching).toBe(true);
  });

  it.each(['toggleWatchlist', 'watchAll'] as const)(
    'submits %s only once during same-tick activation',
    async (method) => {
      const response = deferred<Response>();
      let requests = 0;
      server.use(
        http.post(method === 'watchAll' ? '/api/watchlist/add-batch' : '/api/watchlist/add', () => {
          requests++;
          return response.promise.then((reply) => reply.clone());
        }),
      );
      const { result } = mount();
      await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
      let first!: Promise<void>;
      let second!: Promise<void>;
      act(() => {
        first = result.current[method]();
        second = result.current[method]();
      });
      await waitFor(() => expect(requests).toBeGreaterThan(0));
      expect(requests).toBe(1);
      expect(result.current.watchlistPending).toBe(true);
      await act(async () => {
        response.resolve(HttpResponse.json({ success: true, added: 2, skipped: 0, rejected: [] }));
        await Promise.all([first, second]);
      });
      expect(result.current.watchlistPending).toBe(false);
      expect(result.current.watchlist?.watching).toBe(true);
    },
  );

  it('recovers unknown membership on activation without guessing a mutation', async () => {
    server.use(http.post('/api/watchlist/check', () => HttpResponse.json({ success: false })));
    const { result } = mount();
    await act(() => result.current.toggleWatchlist());
    expect(result.current.watchlist).toBeNull();
    expect(toasts.at(-1)?.level).toBe('error');
    server.use(
      http.post('/api/watchlist/check', () =>
        HttpResponse.json({ success: true, is_watching: false }),
      ),
    );
    await act(() => result.current.toggleWatchlist());
    expect(result.current.watchlist?.watching).toBe(false);
    expect(toasts.at(-1)?.level).toBe('info');
    await act(() => result.current.toggleWatchlist());
    expect(result.current.watchlist?.watching).toBe(true);
  });

  it('does not infer unchecked membership from a malformed check', async () => {
    let checks = 0;
    server.use(
      http.post('/api/watchlist/check', () => {
        checks++;
        return HttpResponse.json({ success: true });
      }),
    );
    const { result } = mount();
    await waitFor(() => expect(checks).toBe(2));
    await act(async () => {
      await result.current.toggleWatchlist();
    });
    await waitFor(() => expect(result.current.watchlist).toBeNull());
    expect(toasts.some((toast) => toast.level === 'success')).toBe(false);
  });
});

describe('hero watchlist request generations', () => {
  it.each([
    [false, 'before'],
    [true, 'before'],
    [false, 'after'],
    [true, 'after'],
  ] as const)(
    'reconciles returned A after mutation (initial watching=%s, returned check %s acknowledgement)',
    async (watching, checkTiming) => {
      const response = deferred<Response>();
      const returnedCheck = deferred<Response>();
      let aChecks = 0;
      server.use(
        http.post('/api/watchlist/check', async ({ request }) => {
          const { artist_id } = (await request.json()) as { artist_id: string };
          if (artist_id === 'a' && ++aChecks === 3) {
            return returnedCheck.promise;
          }
          return HttpResponse.json({ success: true, is_watching: watching });
        }),
        http.post(
          watching ? '/api/watchlist/remove' : '/api/watchlist/add',
          () => response.promise,
        ),
      );
      const { result } = mount();
      await waitFor(() => expect(result.current.watchlist?.watching).toBe(watching));
      let pending!: Promise<void>;
      act(() => {
        pending = result.current.toggleWatchlist();
      });
      act(() => result.current.navigate(1));
      await waitFor(() => expect(result.current.watchlist?.watching).toBe(watching));
      act(() => result.current.navigate(-1));
      await waitFor(() => expect(aChecks).toBe(3));
      if (checkTiming === 'before') {
        await act(async () => {
          returnedCheck.resolve(HttpResponse.json({ success: true, is_watching: watching }));
        });
        await waitFor(() => expect(result.current.watchlist?.watching).toBe(watching));
      }
      await act(async () => {
        response.resolve(HttpResponse.json({ success: true }));
        await pending;
      });
      expect(result.current.artist?.artist_id).toBe('a');
      expect(result.current.watchlist?.watching).toBe(!watching);
      expect(result.current.watchlistPending).toBe(false);
      if (checkTiming === 'after') {
        await act(async () => {
          returnedCheck.resolve(HttpResponse.json({ success: true, is_watching: watching }));
          await new Promise((done) => setTimeout(done, 20));
        });
        expect(result.current.watchlist?.watching).toBe(!watching);
      }
    },
  );

  it('does not put artist A mutation state on artist B', async () => {
    const response = deferred<Response>();
    server.use(http.post('/api/watchlist/add', () => response.promise));
    const { result } = mount();
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.toggleWatchlist();
    });
    act(() => result.current.navigate(1));
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
    await act(async () => {
      response.resolve(HttpResponse.json({ success: true }));
      await pending;
    });
    expect(result.current.artist?.artist_id).toBe('b');
    expect(result.current.watchlist?.watching).toBe(false);
    expect(result.current.watchlistPending).toBe(false);
  });

  it('clears All Watched after removing A while the user views watched B', async () => {
    const response = deferred<Response>();
    server.use(
      http.post('/api/watchlist/check', () =>
        HttpResponse.json({ success: true, is_watching: true }),
      ),
      http.post('/api/watchlist/remove', () => response.promise),
    );
    const { result } = mount();
    await waitFor(() => expect(result.current.watchAllPhase).toBe('done'));
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.toggleWatchlist();
    });
    act(() => result.current.navigate(1));
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(true));
    await act(async () => {
      response.resolve(HttpResponse.json({ success: true }));
      await pending;
    });
    expect(result.current.watchAllPhase).toBe('idle');
    expect(result.current.watchlist?.watching).toBe(true);
  });

  it('drops a pending mutation acknowledgement after unmount', async () => {
    const response = deferred<Response>();
    server.use(http.post('/api/watchlist/add', () => response.promise));
    const { result, unmount } = mount();
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.toggleWatchlist();
    });
    unmount();
    await act(async () => {
      response.resolve(HttpResponse.json({ success: true }));
      await pending;
    });
    expect(toasts).toEqual([]);
  });

  it('does not put an old batch acknowledgement on a replacement list', async () => {
    const response = deferred<Response>();
    server.use(http.post('/api/watchlist/add-batch', () => response.promise));
    const { result, rerender } = mount();
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.watchAll();
    });
    rerender({ list: [artist('c')] });
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
    await act(async () => {
      response.resolve(HttpResponse.json({ success: true, added: 2, skipped: 0, rejected: [] }));
      await pending;
    });
    expect(result.current.watchAllPhase).toBe('idle');
    expect(result.current.watchlist?.watching).toBe(false);
    expect(result.current.watchlistPending).toBe(false);
  });

  it.each([
    [artist('a'), artist('b'), artist('c')],
    [{ ...artist('a'), source: 'itunes' }, artist('b')],
  ])('does not reconcile an old removal into a replacement list or source %j', async (...list) => {
    const response = deferred<Response>();
    server.use(
      http.post('/api/watchlist/check', () =>
        HttpResponse.json({ success: true, is_watching: true }),
      ),
      http.post('/api/watchlist/remove', () => response.promise),
    );
    const { result, rerender } = mount();
    await waitFor(() => expect(result.current.watchAllPhase).toBe('done'));
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.toggleWatchlist();
    });
    rerender({ list });
    await waitFor(() => expect(result.current.watchAllPhase).toBe('done'));
    await act(async () => {
      response.resolve(HttpResponse.json({ success: true }));
      await pending;
    });
    expect(result.current.watchlist?.watching).toBe(true);
    expect(result.current.watchAllPhase).toBe('done');
    expect(toasts).toEqual([]);
  });

  it('resets All Watched when the list changes', async () => {
    server.use(
      http.post('/api/watchlist/add-batch', () =>
        HttpResponse.json({ success: true, added: 2, skipped: 0, rejected: [] }),
      ),
    );
    const { result, rerender } = mount();
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
    await act(() => result.current.watchAll());
    rerender({ list: [artist('c')] });
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(false));
    expect(result.current.watchAllPhase).toBe('idle');
  });

  it('keeps an empty replacement list unknown when its old check returns', async () => {
    const response = deferred<Response>();
    let checks = 0;
    server.use(
      http.post('/api/watchlist/check', () => {
        checks++;
        return response.promise.then((reply) => reply.clone());
      }),
    );
    const { result, rerender } = mount();
    await waitFor(() => expect(checks).toBe(2));
    rerender({ list: [] });
    await act(async () => {
      response.resolve(HttpResponse.json({ success: true, is_watching: true }));
      await new Promise((done) => setTimeout(done, 20));
    });
    expect(result.current.artist).toBeNull();
    expect(result.current.watchlist).toBeNull();
    expect(result.current.watchAllPhase).toBe('idle');
  });

  it('ignores a stale membership check after full batch acknowledgement', async () => {
    const response = deferred<Response>();
    let checkCount = 0;
    server.use(
      http.post('/api/watchlist/check', () => {
        checkCount++;
        return response.promise.then((reply) => reply.clone());
      }),
      http.post('/api/watchlist/add-batch', () =>
        HttpResponse.json({ success: true, added: 2, skipped: 0, rejected: [] }),
      ),
    );
    const { result } = mount();
    await waitFor(() => expect(checkCount).toBe(2));
    await act(() => result.current.watchAll());
    expect(result.current.watchlist?.watching).toBe(true);
    await act(async () => {
      response.resolve(HttpResponse.json({ success: true, is_watching: false }));
      await new Promise((done) => setTimeout(done, 20));
    });
    expect(result.current.watchlist?.watching).toBe(true);
  });

  it('ignores an old all-watched probe after an acknowledged removal', async () => {
    const response = deferred<Response>();
    let checks = 0;
    server.use(
      http.post('/api/watchlist/check', () => {
        checks++;
        return checks === 1
          ? HttpResponse.json({ success: true, is_watching: true })
          : response.promise;
      }),
      http.post('/api/watchlist/remove', () => HttpResponse.json({ success: true })),
    );
    const { result } = mount([artist('a')]);
    await waitFor(() => expect(result.current.watchlist?.watching).toBe(true));
    await act(() => result.current.toggleWatchlist());
    await act(async () => {
      response.resolve(HttpResponse.json({ success: true, is_watching: true }));
      await new Promise((done) => setTimeout(done, 20));
    });
    expect(result.current.watchAllPhase).toBe('idle');
    expect(result.current.watchlist?.watching).toBe(false);
  });
});
