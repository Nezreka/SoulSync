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

function stub({
  createdFor = [pl('c1', 'Weekly Jams for x'), pl('c2', 'Weekly Exploration of y')],
  user = [pl('u1', 'My Mix')],
  collaborative = [] as unknown[],
  createdForStatus = 200,
  usernames = { createdFor: 'boulder', user: 'other' },
  refresh = { success: true, summary: {} } as Record<string, unknown>,
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
    http.post('/api/discover/listenbrainz/refresh', () => HttpResponse.json(refresh)),
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

  it('a NON-OK tab is a tab without data — never a dead section', async () => {
    stub({ createdForStatus: 500, usernames: { user: 'other' } });
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.error).toBe(false);
    expect(result.current.hasData.recommendations).toBe(false);
    expect(result.current.activeTab).toBe('user');
    expect(result.current.username).toBe('other');
  });

  it('a NETWORK failure errors the whole section', async () => {
    server.use(http.get('/api/discover/listenbrainz/user-playlists', () => HttpResponse.error()));
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.error).toBe(true);
    // An errored section shows its ERROR copy, never the connect prompt.
    expect(result.current.showsConnect).toBe(false);
  });

  it('zero tabs with data shows the connect prompt', async () => {
    stub({ createdFor: [], user: [], collaborative: [], usernames: {} });
    const { result } = mount();
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.showsConnect).toBe(true);
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

    stub({ refresh: { success: true, summary: { user: { new: 1 } } } });
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
