import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import type { LbToast } from './-discover.use-listenbrainz';

import {
  LB_REFRESHED,
  LB_UP_TO_DATE,
  lbRefreshMessage,
  useListenBrainz,
} from './-discover.use-listenbrainz';

let toasts: LbToast[] = [];
let createdForHits = 0;

const pl = (id: string, title: string) => ({
  playlist: { identifier: `https://listenbrainz.org/playlist/${id}`, title, creator: 'lb' },
});

const zeroCounts = { new: 0, updated: 0, skipped: 0 };
const refreshAck = {
  success: true,
  summary: { created_for: zeroCounts, user: zeroCounts, collaborative: zeroCounts },
};

function stub({
  createdFor = [pl('c1', 'Weekly Jams for x'), pl('c2', 'Weekly Exploration of y')],
  user = [pl('u1', 'My Mix')],
  collaborative = [] as unknown[],
  createdForStatus = 200,
  usernames = { createdFor: 'boulder', user: 'other' },
  refresh = refreshAck as Record<string, unknown>,
}: Record<string, unknown> = {}) {
  const u = usernames as { createdFor?: string; user?: string };
  server.use(
    http.get('/api/discover/listenbrainz/created-for', () => {
      createdForHits += 1;
      if (createdForStatus !== 200) return HttpResponse.json({}, { status: 500 });
      return HttpResponse.json({ success: true, username: u.createdFor, playlists: createdFor });
    }),
    http.get('/api/discover/listenbrainz/user-playlists', () =>
      HttpResponse.json({ success: true, username: u.user, playlists: user }),
    ),
    http.get('/api/discover/listenbrainz/collaborative', () =>
      HttpResponse.json({ success: true, playlists: collaborative }),
    ),
    http.post('/api/discover/listenbrainz/refresh', () =>
      HttpResponse.json(refresh as Record<string, unknown>),
    ),
  );
}

function mount() {
  return renderHook(() => useListenBrainz((t) => toasts.push(t)));
}

beforeEach(() => {
  toasts = [];
  createdForHits = 0;
  stub();
});

afterEach(() => server.resetHandlers());

describe('useListenBrainz — init', () => {
  it('loads all three tabs, marks data, first-provider username, first tab active', async () => {
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.error).toBe(false);
    expect(result.current.hasData).toEqual({
      recommendations: true,
      user: true,
      collaborative: false,
    });
    // created-for offers a name FIRST — user's does not override (3428-3448).
    expect(result.current.username).toBe('boulder');
    expect(result.current.activeTab).toBe('recommendations');
    expect(result.current.showsConnect).toBe(false);
  });

  it('preserves a successful sibling after an HTTP failure', async () => {
    stub({ createdForStatus: 500, usernames: { user: 'other' } });
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.error).toBe(true);
    expect(result.current.hasData.recommendations).toBe(false);
    expect(result.current.activeTab).toBe('user');
    expect(result.current.username).toBe('other');
  });

  it('preserves a successful sibling after network failure', async () => {
    server.use(http.get('/api/discover/listenbrainz/user-playlists', () => HttpResponse.error()));
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.error).toBe(true);
    expect(result.current.mixes[0]?.title).toBe('Weekly Jams for x');
    expect(result.current.showsConnect).toBe(false);
  });

  it('treats acknowledged empty arrays as empty without inferring disconnection', async () => {
    stub({ createdFor: [], user: [], collaborative: [], usernames: {} });
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.showsConnect).toBe(false);
    expect(result.current.mixes).toEqual([]);
  });
});

describe('useListenBrainz — tabs and sub-tabs', () => {
  it('groups the recommendations tab into sub-tabs with counts', async () => {
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.groups).toEqual([
      { name: 'Weekly Jams', count: 1 },
      { name: 'Weekly Exploration', count: 1 },
    ]);
    expect(result.current.activeGroup).toBe('Weekly Jams');
    // Only the active GROUP's cards show.
    expect(result.current.mixes).toHaveLength(1);
    expect(result.current.mixes[0].title).toBe('Weekly Jams for x');
    act(() => result.current.selectGroup('Weekly Exploration'));
    expect(result.current.mixes[0].title).toBe('Weekly Exploration of y');
  });

  it('the user tab never sub-tabs; switching tabs switches the cards', async () => {
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    act(() => result.current.selectTab('user'));
    expect(result.current.groups).toBeNull();
    expect(result.current.mixes).toHaveLength(1);
    expect(result.current.mixes[0].key).toBe('lb-user-u1');
  });

  it('ONLY the recommendations tab ever sub-tabs (3685)', async () => {
    // A user tab whose titles span two groups must still render flat.
    stub({ user: [pl('u1', 'Weekly Jams mine'), pl('u2', 'My Mix')] });
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    act(() => result.current.selectTab('user'));
    expect(result.current.groups).toBeNull();
    expect(result.current.mixes).toHaveLength(2);
  });

  it('ONE group means no sub-tab bar at all (3685-3690)', async () => {
    stub({ createdFor: [pl('c1', 'Weekly Jams a'), pl('c2', 'Weekly Jams b')] });
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.groups).toBeNull();
    expect(result.current.mixes).toHaveLength(2);
  });
});

describe('useListenBrainz — refresh', () => {
  it('summarises per type, toasts success, and re-inits', async () => {
    expect(lbRefreshMessage({ created_for: { new: 2, updated: 1 }, user: { new: 0 } })).toBe(
      'ListenBrainz playlists refreshed! Updated: 3 created_for',
    );
    expect(lbRefreshMessage({})).toBe('All playlists are up to date');
    // The copy itself, pinned as literals.
    expect(LB_REFRESHED).toBe('ListenBrainz playlists refreshed!');
    expect(LB_UP_TO_DATE).toBe('All playlists are up to date');

    stub({
      refresh: {
        ...refreshAck,
        summary: { ...refreshAck.summary, user: { ...zeroCounts, new: 1 } },
      },
    });
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(() => result.current.refresh());
    expect(toasts.at(-1)).toEqual({
      message: 'ListenBrainz playlists refreshed! Updated: 1 user',
      level: 'success',
    });
    expect(result.current.refreshing).toBe(false);
    // Success RE-INITS — the tab endpoints are asked again (4239).
    expect(createdForHits).toBe(2);
  });

  it('a refused refresh toasts the server error and unlocks', async () => {
    stub({ refresh: { success: false, error: 'token expired' } });
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(() => result.current.refresh());
    expect(toasts.at(-1)).toEqual({ message: 'Failed to refresh: token expired', level: 'error' });
    expect(result.current.refreshing).toBe(false);
  });
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

it('preserves the selected tab and its last confirmed cards when its response is malformed', async () => {
  const { result } = mount();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  act(() => result.current.selectTab('user'));
  stub({ user: [null] });
  await act(() => result.current.refresh());
  expect(result.current.activeTab).toBe('user');
  expect(result.current.mixes.map((mix) => mix.title)).toEqual(['My Mix']);
  expect(result.current.sourceErrors.user).toBeTruthy();
  expect(toasts.some((toast) => toast.level === 'success')).toBe(false);
});

it('drops a malformed playlist row and keeps the rest of the shelf', async () => {
  stub({
    user: [null, { playlist: 'nope' }, pl('u2', 'Good Mix'), { playlist: { title: 'no id' } }],
  });
  const { result } = mount();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  act(() => result.current.selectTab('user'));
  expect(result.current.sourceErrors).toEqual({});
  expect(result.current.mixes.map((mix) => mix.title)).toEqual(['Good Mix']);
});

it('clears a failed refresh message when the shelf is retried', async () => {
  server.use(
    http.post('/api/discover/listenbrainz/refresh', () =>
      HttpResponse.json(refreshAck, { status: 503 }),
    ),
  );
  const { result } = mount();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  await act(() => result.current.refresh());
  expect(result.current.refreshError).toBeTruthy();
  await act(() => result.current.retry());
  expect(result.current.refreshError).toBeNull();
});

it.each([
  { summary: { ...refreshAck.summary, listens: zeroCounts } },
  {
    summary: {
      created_for: { new: 1, updated: 0 },
      user: { new: 0, updated: 2, skipped: 1, failed: 0 },
      collaborative: { new: 0, updated: 0 },
    },
  },
])(
  'accepts a refresh summary with the needed counts and extras ($summary)',
  async ({ summary }) => {
    server.use(
      http.post('/api/discover/listenbrainz/refresh', () =>
        HttpResponse.json({ success: true, summary }),
      ),
    );
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(() => result.current.refresh());
    expect(result.current.refreshError).toBeNull();
    expect(toasts.at(-1)?.level).toBe('success');
  },
);

it('retries the shelf reads without another refresh and falls back when the selected tab confirms empty', async () => {
  let posts = 0;
  stub({ createdForStatus: 500 });
  server.use(
    http.post('/api/discover/listenbrainz/refresh', () => {
      posts++;
      return HttpResponse.json(refreshAck);
    }),
  );
  const { result } = mount();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  await act(() => result.current.refresh());
  expect(result.current.sourceErrors.recommendations).toBeTruthy();
  stub({ user: [] });
  await act(() => result.current.retry());
  expect(posts).toBe(1);
  expect(result.current.sourceErrors).toEqual({});
  expect(result.current.activeTab).toBe('recommendations');
  expect(result.current.mixes[0]?.title).toBe('Weekly Jams for x');
});

it('allows only one refresh before React commits pending state', async () => {
  let posts = 0;
  server.use(
    http.post('/api/discover/listenbrainz/refresh', () => {
      posts++;
      return HttpResponse.json(refreshAck);
    }),
  );
  const { result } = mount();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  await act(async () => {
    await Promise.all([result.current.refresh(), result.current.refresh()]);
  });
  expect(posts).toBe(1);
});

it.each([
  { status: 503, body: refreshAck },
  { status: 200, body: { success: true, summary: {} } },
  { status: 200, body: { success: true, summary: { ...refreshAck.summary, user: {} } } },
  { status: 200, body: { success: 'true', summary: {} } },
  { status: 200, body: { success: true, summary: { user: { updated: '10' } } } },
  {
    status: 200,
    body: { success: true, summary: { ...refreshAck.summary, user: { new: 1, updated: '2' } } },
  },
])(
  'keeps an unacknowledged refresh error visible without reloading the shelf ($status $body)',
  async ({ status, body }) => {
    server.use(
      http.post('/api/discover/listenbrainz/refresh', () => HttpResponse.json(body, { status })),
    );
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(() => result.current.refresh());
    expect(result.current.refreshError).toBeTruthy();
    expect(toasts.at(-1)?.level).toBe('error');
    expect(createdForHits).toBe(1);
    expect(result.current.mixes[0]?.title).toBe('Weekly Jams for x');
  },
);

it('ignores a slow initial read after a newer retry completes', async () => {
  const started = deferred();
  const finish = deferred();
  server.use(
    http.get('/api/discover/listenbrainz/created-for', async () => {
      started.resolve();
      await finish.promise;
      return HttpResponse.json({ success: true, playlists: [pl('old', 'Old playlist')] });
    }),
  );
  const { result } = mount();
  await started.promise;
  stub();
  await act(() => result.current.retry());
  await act(async () => finish.resolve());
  expect(result.current.mixes[0]?.title).toBe('Weekly Jams for x');
});

it('skips the re-read and toast when a refresh settles after unmount', async () => {
  const started = deferred();
  const finish = deferred();
  server.use(
    http.post('/api/discover/listenbrainz/refresh', async () => {
      started.resolve();
      await finish.promise;
      return HttpResponse.json(refreshAck);
    }),
  );
  const { result, unmount } = mount();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  let refresh!: Promise<void>;
  await act(async () => {
    refresh = result.current.refresh();
    await started.promise;
  });
  unmount();
  finish.resolve();
  await refresh;
  expect(createdForHits).toBe(1);
  expect(toasts).toEqual([]);
});

it('selects a recovered sibling after the initial read confirms no sources', async () => {
  server.use(
    http.get('/api/discover/listenbrainz/created-for', () => HttpResponse.error()),
    http.get('/api/discover/listenbrainz/user-playlists', () => HttpResponse.error()),
    http.get('/api/discover/listenbrainz/collaborative', () => HttpResponse.error()),
  );
  const { result } = mount();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  expect(result.current.showsConnect).toBe(false);
  stub({ createdForStatus: 503 });
  await act(() => result.current.retry());
  expect(result.current.activeTab).toBe('user');
  expect(result.current.mixes[0]?.title).toBe('My Mix');
});

it('preserves the selected group and its cards when the recommendations read fails', async () => {
  const { result } = mount();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  act(() => result.current.selectGroup('Weekly Exploration'));
  stub({ createdForStatus: 503 });
  await act(() => result.current.retry());
  expect(result.current.activeGroup).toBe('Weekly Exploration');
  expect(result.current.mixes[0]?.title).toBe('Weekly Exploration of y');
});

it('prompts to connect only when every source explicitly confirms disconnection', async () => {
  const disconnected = () =>
    HttpResponse.json({
      success: false,
      error: 'Not authenticated',
      playlists: [],
      count: 0,
      username: null,
    });
  server.use(
    http.get('/api/discover/listenbrainz/created-for', disconnected),
    http.get('/api/discover/listenbrainz/user-playlists', disconnected),
    http.get('/api/discover/listenbrainz/collaborative', () => HttpResponse.error()),
  );
  const { result } = mount();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  expect(result.current.showsConnect).toBe(false);
  server.use(http.get('/api/discover/listenbrainz/collaborative', disconnected));
  await act(() => result.current.retry());
  expect(result.current.showsConnect).toBe(true);
});

it('retains the new selected group after a removed group returns', async () => {
  const { result } = mount();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  act(() => result.current.selectGroup('Weekly Exploration'));
  stub({ createdFor: [pl('c1', 'Weekly Jams for x')] });
  await act(() => result.current.retry());
  expect(result.current.mixes[0]?.title).toBe('Weekly Jams for x');
  stub();
  await act(() => result.current.retry());
  expect(result.current.activeGroup).toBe('Weekly Jams');
});

it('keeps the last confirmed numeric card count when a source returns a malformed annotation', async () => {
  const original = pl('u1', 'My Mix');
  const withCount = (track_count: unknown) => ({
    playlist: { ...original.playlist, annotation: { track_count } },
  });
  stub({ user: [withCount(12)] });
  const { result } = mount();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  act(() => result.current.selectTab('user'));
  stub({ user: [withCount('8')] });
  await act(() => result.current.retry());
  expect(result.current.mixes[0]?.trackCount).toBe(12);
  expect(result.current.sourceErrors.user).toBeTruthy();
  expect(result.current.hasData.recommendations).toBe(true);
});
