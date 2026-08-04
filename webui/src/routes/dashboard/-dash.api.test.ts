/**
 * Dashboard api layer. Mock router matches by URL, preferring the LONGEST key —
 * `/api/enrichment/status-all` vs `/api/enrichment/tidal/status` never collide,
 * but the discipline is kept anyway (the tools mock-router shadowing lesson).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  BUNDLE_PROVIDER_IDS,
  fetchActivityFeed,
  fetchActivityToasts,
  fetchAllProviderStatuses,
  fetchDashboardSyncHistory,
  fetchHydrabaseStatus,
  fetchLibraryScanStatus,
  fetchProviderStatus,
  fetchServiceStatus,
  fetchSystemStats,
  fetchWatchlistCount,
  fetchWishlistCount,
  isLibraryScanTerminal,
  setHydrabaseRunning,
  setProviderRunning,
  startLibraryDeepScan,
  startLibraryScan,
  stopLibraryScan,
} from './-dash.api';

const fetchMock = vi.fn();

function routes(map: Record<string, unknown>, opts: { status?: number } = {}) {
  fetchMock.mockImplementation((url: string) => {
    const hit = Object.keys(map)
      .filter((key) => String(url).includes(key))
      .sort((a, b) => b.length - a.length)[0];
    return Promise.resolve({
      ok: (opts.status ?? 200) < 400,
      status: opts.status ?? 200,
      json: async () => (hit ? map[hit] : {}),
    } as never);
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  routes({});
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

const calledUrls = () => fetchMock.mock.calls.map((call) => String(call[0]));

describe('fetchAllProviderStatuses', () => {
  it('serves every id from one bundle call on the happy path', async () => {
    const services = Object.fromEntries(BUNDLE_PROVIDER_IDS.map((id) => [id, { running: true }]));
    routes({ '/api/enrichment/status-all': { services } });

    const out = await fetchAllProviderStatuses();
    expect(Object.keys(out)).toHaveLength(13);
    expect(out.musicbrainz).toEqual({ running: true });
    expect(calledUrls()).toEqual(['/api/enrichment/status-all']);
  });

  it('falls back per-service for a MISSING id and for a payload carrying error', async () => {
    routes({
      '/api/enrichment/status-all': {
        services: { tidal: { running: true }, qobuz: { error: 'boom' } },
      },
      '/api/enrichment/qobuz/status': { paused: true },
      '/api/enrichment/lastfm/status': { idle: true },
    });

    const out = await fetchAllProviderStatuses(['tidal', 'qobuz', 'lastfm']);
    expect(out.tidal).toEqual({ running: true });
    expect(out.qobuz).toEqual({ paused: true });
    expect(out.lastfm).toEqual({ idle: true });
    expect(calledUrls()).toContain('/api/enrichment/qobuz/status');
    expect(calledUrls()).not.toContain('/api/enrichment/tidal/status');
  });

  it('a dead bundle sends every id to its own endpoint', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes('status-all')) return Promise.reject(new Error('down'));
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ idle: true }),
      } as never);
    });
    const out = await fetchAllProviderStatuses(['tidal', 'amazon']);
    expect(out).toEqual({ tidal: { idle: true }, amazon: { idle: true } });
  });

  it('a failed fallback leaves the id ABSENT — the pill keeps its previous state', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('down')));
    const out = await fetchAllProviderStatuses(['tidal']);
    expect(out).toEqual({});
  });
});

describe('toggles', () => {
  it('builds pause/resume from the run flag', async () => {
    await setProviderRunning('tidal', true);
    await setProviderRunning('discogs', false);
    await setHydrabaseRunning(false);
    expect(calledUrls()).toEqual([
      '/api/enrichment/tidal/resume',
      '/api/enrichment/discogs/pause',
      '/api/hydrabase-worker/pause',
    ]);
    expect(fetchMock.mock.calls.every((call) => (call[1] as RequestInit).method === 'POST')).toBe(
      true,
    );
  });
});

describe('the null-on-failure fetchers', () => {
  it.each([
    ['fetchProviderStatus', () => fetchProviderStatus('tidal'), null],
    ['fetchHydrabaseStatus', fetchHydrabaseStatus, null],
    ['fetchServiceStatus', fetchServiceStatus, null],
    ['fetchSystemStats', fetchSystemStats, null],
    ['fetchLibraryScanStatus', fetchLibraryScanStatus, null],
    ['fetchWishlistCount', fetchWishlistCount, null],
    ['fetchActivityFeed', fetchActivityFeed, [] as unknown],
    ['fetchActivityToasts', fetchActivityToasts, [] as unknown],
  ])('%s swallows a network failure', async (_name, fn, empty) => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('down')));
    expect(await (fn as () => Promise<unknown>)()).toEqual(empty);
  });

  it('unwraps the list payloads', async () => {
    routes({
      '/api/activity/feed': { activities: [{ title: 'a' }] },
      '/api/activity/toasts': { toasts: [{ title: 't' }] },
    });
    expect(await fetchActivityFeed()).toEqual([{ title: 'a' }]);
    expect(await fetchActivityToasts()).toEqual([{ title: 't' }]);
  });
});

describe('the counts', () => {
  it('watchlist trusts the payload only when success is set', async () => {
    routes({ '/api/watchlist/count': { success: true, count: 4, next_run_in_seconds: 90 } });
    expect(await fetchWatchlistCount()).toEqual({ count: 4, next_run_in_seconds: 90 });
    routes({ '/api/watchlist/count': { count: 4 } });
    expect(await fetchWatchlistCount()).toBeNull();
  });

  it('wishlist has no success flag — count || 0', async () => {
    routes({ '/api/wishlist/count': {} });
    expect(await fetchWishlistCount()).toBe(0);
    routes({ '/api/wishlist/count': { count: 7 } });
    expect(await fetchWishlistCount()).toBe(7);
  });
});

describe('fetchDashboardSyncHistory', () => {
  it('filters to playlist syncs AND entries with no sync_type', async () => {
    routes({
      '/api/sync/history': {
        entries: [
          { id: 1, sync_type: 'playlist' },
          { id: 2, sync_type: 'album' },
          { id: 3 },
          { id: 4, sync_type: 'wishlist' },
        ],
      },
    });
    const result = await fetchDashboardSyncHistory();
    expect(result).toEqual({
      status: 'ok',
      entries: [{ id: 1, sync_type: 'playlist' }, { id: 3 }],
    });
  });

  it('surfaces a 401 as a state, with loginRequired from the body', async () => {
    routes({ '/api/sync/history': { login_required: true } }, { status: 401 });
    expect(await fetchDashboardSyncHistory()).toEqual({
      status: 'unauthorized',
      loginRequired: true,
    });
    routes({ '/api/sync/history': {} }, { status: 401 });
    expect(await fetchDashboardSyncHistory()).toEqual({
      status: 'unauthorized',
      loginRequired: false,
    });
  });

  it('a 401 with an unparseable body still resolves (launch-PIN path)', async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve({
        ok: false,
        status: 401,
        json: async () => {
          throw new Error('not json');
        },
      } as never),
    );
    expect(await fetchDashboardSyncHistory()).toEqual({
      status: 'unauthorized',
      loginRequired: false,
    });
  });

  it('other failures are plain errors', async () => {
    routes({}, { status: 500 });
    expect(await fetchDashboardSyncHistory()).toEqual({ status: 'error' });
  });
});

describe('library scans', () => {
  it('sends the exact vanilla bodies', async () => {
    await startLibraryScan(true);
    await startLibraryDeepScan();
    await stopLibraryScan();
    const bodies = fetchMock.mock.calls.map((call) => (call[1] as RequestInit)?.body ?? null);
    expect(JSON.parse(String(bodies[0]))).toEqual({ full_refresh: true });
    expect(JSON.parse(String(bodies[1]))).toEqual({ deep_scan: true });
    expect(calledUrls()[2]).toBe('/api/database/update/stop');
  });

  it('names the four terminal statuses, and only those', () => {
    for (const status of ['completed', 'finished', 'error', 'idle']) {
      expect(isLibraryScanTerminal(status)).toBe(true);
    }
    expect(isLibraryScanTerminal('running')).toBe(false);
    expect(isLibraryScanTerminal(undefined)).toBe(false);
  });
});
