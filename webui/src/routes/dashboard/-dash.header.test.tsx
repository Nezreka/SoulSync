/**
 * The header controller — pill materialization (null = keep previous text),
 * the per-provider toggle quirks, the fallback polls, visibility flags, and
 * the quick-nav counts. All literals asserted against the vanilla originals.
 */

import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DashboardHeaderState } from './-dash.header';

import { PILL_DEFAULTS, useDashboardHeader } from './-dash.header';

const fetchMock = vi.fn();
const showToast = vi.fn();

function routes(map: Record<string, unknown>, opts: { status?: number } = {}) {
  // Every routes() swap also FORGETS earlier calls — the mount hydrate fires a
  // bundle + 13 per-id fallbacks + repair, and the toggle tests assert exact
  // URL lists.
  fetchMock.mockClear();
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

/** Every fetch fails — hydrates resolve to null/absent and defaults hold. */
function offline() {
  fetchMock.mockClear();
  fetchMock.mockImplementation(() => Promise.reject(new Error('down')));
}

let state: DashboardHeaderState;

function Probe() {
  state = useDashboardHeader();
  return null;
}

async function mount() {
  let view: ReturnType<typeof render>;
  await act(async () => {
    view = render(<Probe />);
  });
  return view!;
}

function fireEnrich(id: string, data: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id, data } }));
  });
}

const flush = () => act(async () => {});

beforeEach(() => {
  fetchMock.mockReset();
  offline();
  showToast.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  window.showToast = showToast;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete window.showToast;
  delete window.openRepairModal;
  delete window.isJiosaavnExperimentalEnabled;
});

const calledUrls = () => fetchMock.mock.calls.map((call) => String(call[0]));

describe('pill state', () => {
  it('starts from the markup defaults', async () => {
    await mount();
    expect(state.pills).toEqual(PILL_DEFAULTS);
    expect(state.repairBadge).toEqual({ count: 0, visible: false });
  });

  it('reduces an enrich frame into its pill', async () => {
    await mount();
    fireEnrich('musicbrainz', {
      running: true,
      paused: false,
      current_item: { type: 'artist', name: 'BYLT' },
    });
    expect(state.pills.musicbrainz.stateClass).toBe('active');
    expect(state.pills.musicbrainz.status).toBe('Running');
    expect(state.pills.musicbrainz.current).toBe('Artist: "BYLT"');
  });

  it('keeps the previous text when a frame computes null (the stale-tooltip quirk)', async () => {
    await mount();
    fireEnrich('musicbrainz', {
      running: true,
      paused: false,
      progress: { artists: { matched: 3, total: 10, percent: 30 } },
    });
    expect(state.pills.musicbrainz.progress).toBe('Artists: 3 / 10 (30%)');
    // No progress on the next frame → the reducer says null → text is KEPT.
    fireEnrich('musicbrainz', { running: true, paused: false });
    expect(state.pills.musicbrainz.progress).toBe('Artists: 3 / 10 (30%)');

    // Deezer's current has no final else — same retention through its reducer.
    fireEnrich('deezer', { running: true, paused: false, current_item: { name: 'Koven' } });
    expect(state.pills.deezer.current).toBe('Now: Koven');
    fireEnrich('deezer', { running: true, paused: false });
    expect(state.pills.deezer.current).toBe('Now: Koven');
  });

  it('routes ss:repair-status into the repair pill and badge', async () => {
    await mount();
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:repair-status', {
          detail: { enabled: true, running: true, paused: false, findings_pending: 4 },
        }),
      );
    });
    expect(state.pills.repair.stateClass).toBe('active');
    expect(state.repairBadge).toEqual({ count: 4, visible: true });

    // Only REPAIR frames drive the badge — a provider frame (whose payload has
    // no findings_pending) must not reset it to 0/hidden.
    fireEnrich('musicbrainz', { running: true, paused: false });
    expect(state.repairBadge).toEqual({ count: 4, visible: true });
  });

  it('carries the Hydrabase inline status color', async () => {
    await mount();
    fireEnrich('hydrabase', { running: false, paused: false });
    expect(state.pills.hydrabase.status).toBe('Stopped');
    expect(state.pills.hydrabase.statusColor).toBe('#ff5252');
  });
});

describe('the standard toggle', () => {
  it('pauses a running provider then refetches it', async () => {
    await mount();
    fireEnrich('musicbrainz', { running: true, paused: false });
    routes({ '/api/enrichment/musicbrainz': { running: false, paused: true } });
    await act(async () => state.onOrbClick('musicbrainz'));
    expect(calledUrls()).toEqual([
      '/api/enrichment/musicbrainz/pause',
      '/api/enrichment/musicbrainz/status',
    ]);
    expect(state.pills.musicbrainz.stateClass).toBe('paused');
  });

  it('resumes anything that is not active', async () => {
    await mount(); // stateClass null
    routes({ '/api/enrichment/deezer': {} });
    await act(async () => state.onOrbClick('deezer'));
    expect(calledUrls()[0]).toBe('/api/enrichment/deezer/resume');
  });

  it('toasts the vanilla failure wording on a bad response', async () => {
    await mount();
    fireEnrich('lastfm', { running: true, paused: false, authenticated: true });
    routes({}, { status: 500 });
    await act(async () => state.onOrbClick('lastfm'));
    expect(showToast).toHaveBeenCalledWith('Error: Failed to pause Last.fm enrichment', 'error');
  });
});

describe('the toggle quirks', () => {
  it('Discogs reads INVERTED (paused-or-complete resumes) and toasts success', async () => {
    await mount();
    fireEnrich('discogs', { idle: true }); // complete
    routes({});
    await act(async () => state.onOrbClick('discogs'));
    expect(calledUrls()).toEqual(['/api/enrichment/discogs/resume']); // no refetch
    expect(showToast).toHaveBeenCalledWith('Discogs enrichment resumed', 'info');

    showToast.mockReset();
    fetchMock.mockClear();
    fireEnrich('discogs', { running: true, paused: false });
    routes({});
    await act(async () => state.onOrbClick('discogs'));
    expect(calledUrls()).toEqual(['/api/enrichment/discogs/pause']);
    expect(showToast).toHaveBeenCalledWith('Discogs enrichment paused', 'info');
  });

  it('Discogs swallows the failure with its own toast', async () => {
    await mount();
    offline();
    await act(async () => state.onOrbClick('discogs'));
    expect(showToast).toHaveBeenCalledWith('Failed to toggle Discogs enrichment', 'error');
  });

  it('Spotify reads rate_limited from the ERROR body and warns instead of erroring', async () => {
    await mount();
    routes({ '/api/enrichment/spotify/resume': { rate_limited: true } }, { status: 429 });
    await act(async () => state.onOrbClick('spotify'));
    expect(showToast).toHaveBeenCalledWith('Cannot resume — Spotify is rate limited', 'warning');
    expect(calledUrls()).toEqual(['/api/enrichment/spotify/resume']); // no refetch either
  });

  it('Spotify falls through to the standard error when the body has no rate_limited', async () => {
    await mount();
    fireEnrich('spotify', { running: true, paused: false });
    routes({}, { status: 500 });
    await act(async () => state.onOrbClick('spotify'));
    expect(showToast).toHaveBeenCalledWith('Error: Failed to pause Spotify enrichment', 'error');
  });

  it('Bandcamp REFUSES while no-auth — no request at all', async () => {
    await mount();
    fireEnrich('bandcamp', { enabled: false });
    expect(state.pills.bandcamp.stateClass).toBe('no-auth');
    fetchMock.mockClear();
    await act(async () => state.onOrbClick('bandcamp'));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('Bandcamp toggles normally once enabled', async () => {
    await mount();
    fireEnrich('bandcamp', { enabled: true, running: true, paused: false });
    routes({ '/api/enrichment/bandcamp': {} });
    await act(async () => state.onOrbClick('bandcamp'));
    expect(calledUrls()[0]).toBe('/api/enrichment/bandcamp/pause');
  });

  it('Hydrabase uses its own worker endpoints and never toasts', async () => {
    await mount();
    fireEnrich('hydrabase', { running: true, paused: false });
    routes({ '/api/hydrabase-worker': { running: false, paused: true } });
    await act(async () => state.onOrbClick('hydrabase'));
    expect(calledUrls()).toEqual(['/api/hydrabase-worker/pause', '/api/hydrabase-worker/status']);
    expect(showToast).not.toHaveBeenCalled();
  });

  it('Repair is a LINK to the tools maintenance hero', async () => {
    const openRepairModal = vi.fn();
    window.openRepairModal = openRepairModal;
    await mount();
    fetchMock.mockClear();
    await act(async () => state.onOrbClick('repair'));
    expect(openRepairModal).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('SoulID is display-only', async () => {
    await mount();
    fetchMock.mockClear();
    await act(async () => state.onOrbClick('soulid'));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(showToast).not.toHaveBeenCalled();
  });
});

describe('orb visibility', () => {
  it('JioSaavn starts from the shared experimental flag and follows the event', async () => {
    window.isJiosaavnExperimentalEnabled = () => true;
    await mount();
    expect(state.jiosaavnVisible).toBe(true);
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:jiosaavn-experimental', { detail: { enabled: false } }),
      );
    });
    expect(state.jiosaavnVisible).toBe(false);
  });

  it('Hydrabase shows on a positive dev-mode check and follows the event', async () => {
    routes({ '/api/dev-mode': { enabled: true } });
    await mount();
    await flush();
    expect(state.hydrabaseVisible).toBe(true);
    act(() => {
      window.dispatchEvent(new CustomEvent('ss:dev-mode', { detail: { enabled: false } }));
    });
    expect(state.hydrabaseVisible).toBe(false);
  });

  it('both stay hidden by default', async () => {
    await mount();
    expect(state.jiosaavnVisible).toBe(false);
    expect(state.hydrabaseVisible).toBe(false);
  });
});

describe('quick-nav counts', () => {
  it('watchlist applies the success gate the handler dispatches past', async () => {
    await mount();
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:watchlist-count', { detail: { count: 9 } }), // no success
      );
    });
    expect(state.watchlist.count).toBe(0);
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:watchlist-count', {
          detail: { success: true, count: 9, next_run_in_seconds: 1453 },
        }),
      );
    });
    expect(state.watchlist.count).toBe(9);
    expect(state.watchlist.title).toBe('Next auto-scan in 24m 13s');
  });

  it('a payload without a countdown KEEPS the previous title (vanilla only overwrites truthy)', async () => {
    await mount();
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:watchlist-count', {
          detail: { success: true, count: 9, next_run_in_seconds: 90 },
        }),
      );
    });
    expect(state.watchlist.title).toBe('Next auto-scan in 1m 30s');
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:watchlist-count', { detail: { success: true, count: 8 } }),
      );
    });
    expect(state.watchlist).toEqual({ count: 8, title: 'Next auto-scan in 1m 30s' });
  });

  it('wishlist count arrives via its socket event, count || 0', async () => {
    await mount();
    expect(state.wishlistCount).toBeNull(); // offline mount fetch → no classes yet
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:dashboard-wishlist-count', { detail: { count: 3 } }),
      );
    });
    expect(state.wishlistCount).toBe(3);
    act(() => {
      window.dispatchEvent(new CustomEvent('ss:dashboard-wishlist-count', { detail: {} }));
    });
    expect(state.wishlistCount).toBe(0);
  });

  it('hydrates both counts on mount when the endpoints answer', async () => {
    routes({
      '/api/watchlist/count': { success: true, count: 5, next_run_in_seconds: 60 },
      '/api/wishlist/count': { count: 2 },
    });
    await mount();
    await flush();
    expect(state.watchlist.count).toBe(5);
    expect(state.wishlistCount).toBe(2);
  });
});

describe('fallback polling', () => {
  it('polls the bundle+hydrabase at 10s — and repair NEVER (the app-wide vanilla poll owns it)', async () => {
    vi.useFakeTimers();
    await mount();
    routes({});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(calledUrls()).toContain('/api/enrichment/status-all');
    expect(calledUrls()).toContain('/api/hydrabase-worker/status');
    // enrichment.js keeps the 5s app-wide repair poll whose handler dispatches
    // ss:repair-status; a second interval here would double the request rate.
    expect(calledUrls()).not.toContain('/api/repair/status');
  });

  it('skips ticks while the socket is pushing (window._socketConnected)', async () => {
    vi.useFakeTimers();
    await mount();
    window._socketConnected = true;
    fetchMock.mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(fetchMock).not.toHaveBeenCalled();
    delete window._socketConnected;
  });

  it('skips ticks while the tab is hidden, like the vanilla pollers', async () => {
    vi.useFakeTimers();
    await mount();
    // document.hidden is a prototype accessor in jsdom — shadow it on the
    // instance rather than spying.
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    fetchMock.mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(fetchMock).not.toHaveBeenCalled();
    delete (document as { hidden?: boolean }).hidden;
  });

  it('hydrates the repair pill once on mount', async () => {
    routes({
      '/api/repair/status': { enabled: true, running: true, paused: false, findings_pending: 2 },
    });
    await mount();
    expect(state.pills.repair.stateClass).toBe('active');
    expect(state.repairBadge).toEqual({ count: 2, visible: true });
  });

  it('stops polling on unmount', async () => {
    vi.useFakeTimers();
    const view = await mount();
    view.unmount();
    fetchMock.mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
