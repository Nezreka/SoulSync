/**
 * The Tidal/Qobuz account-vertical tabs against a captured fetch and the REAL
 * useSourceVertical hook — refresh → list → background tracks + mirror →
 * states hydration/resume → the two fresh-click drifts.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchAccountPlaylist } from '../-sync.api';
import { SYNC_SOURCES } from '../-sync.sources';
import { useSourceVertical } from '../-sync.use-vertical';
import { QobuzTab, TidalTab } from './account-tab';
import { hydrateStatesForLoaded, resumeIfInFlight } from './url-import-tab';

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let responder: (url: string) => unknown = () => ({});

function stubFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return new Response(JSON.stringify(responder(url)));
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { showToast?: unknown }).showToast;
  delete (window as { showLoadingOverlay?: unknown }).showLoadingOverlay;
  delete (window as { hideLoadingOverlay?: unknown }).hideLoadingOverlay;
});

function TidalHarness({ onOpen }: { onOpen?: (id: string) => void }) {
  const vertical = useSourceVertical(SYNC_SOURCES.tidal);
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <div>
      <TidalTab vertical={vertical} onOpen={onOpen ?? setOpenId} />
      <span data-testid="open-id">{openId ?? 'none'}</span>
      <span data-testid="phase">{vertical.states.t1?.phase ?? 'unseeded'}</span>
    </div>
  );
}

describe('TidalTab', () => {
  it('starts on the click-Refresh placeholder and loads nothing', () => {
    stubFetch();
    render(<TidalHarness />);
    expect(screen.getByText("Click 'Refresh' to load your Tidal playlists.")).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it('refresh: list → cards → background track fetch updates count + mirrors → states hydrate', async () => {
    stubFetch();
    window.showToast = vi.fn() as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/tidal/playlists') {
        return [
          {
            id: 't1',
            name: 'Tidal Mix',
            track_count: 0,
            owner: 'Me',
            image_url: 'http://img',
            description: 'desc-meta',
          },
        ];
      }
      if (url === '/api/tidal/playlist/t1') {
        return { tracks: [{ name: 'S1', artists: ['A1'], album: 'Al', duration_ms: 3, id: 'x' }] };
      }
      if (url === '/api/tidal/playlists/states') {
        return {
          states: [
            { playlist_id: 't1', phase: 'discovered', spotify_matches: 1, spotify_total: 1 },
          ],
        };
      }
      return { success: true };
    };
    render(<TidalHarness />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Tidal Mix')).toBeInTheDocument());
    // Background fetch landed: the card count updates (46-47).
    await waitFor(() => expect(screen.getByText('1 tracks')).toBeInTheDocument());
    // Mirror carries the account metadata incl. description (30-34).
    const mirror = calls.find((c) => c.url === '/api/mirror-playlist');
    expect(mirror!.body).toMatchObject({
      source: 'tidal',
      source_playlist_id: 't1',
      name: 'Tidal Mix',
      owner: 'Me',
      image_url: 'http://img',
      description: 'desc-meta',
    });
    // States applied after the list (61-62).
    await waitFor(() => expect(screen.getByText('Discovery Complete')).toBeInTheDocument());
    expect(screen.getByText('♪ 1 / ✓ 1 / ✗ 0 / 100%')).toBeInTheDocument();
  });

  it('a playlist that arrives WITH tracks mirrors immediately, no per-playlist fetch (28-36)', async () => {
    stubFetch();
    responder = (url) => {
      if (url === '/api/tidal/playlists') {
        return [
          {
            id: 't9',
            name: 'Prefilled',
            track_count: 1,
            owner: 'O',
            tracks: [{ name: 'PT', artists: ['PA'], album: 'PAl', duration_ms: 1, id: 'pt' }],
          },
        ];
      }
      if (url === '/api/tidal/playlists/states') return { states: [] };
      return { success: true };
    };
    render(<TidalHarness />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Prefilled')).toBeInTheDocument());
    const mirror = await waitFor(() => {
      const found = calls.find((c) => c.url === '/api/mirror-playlist');
      expect(found).toBeDefined();
      return found!;
    });
    expect((mirror.body as { tracks: { track_name: string }[] }).tracks[0].track_name).toBe('PT');
    // The skip-if-tracks branch never hits the per-playlist endpoint.
    expect(calls.some((c) => c.url === '/api/tidal/playlist/t9')).toBe(false);
  });

  it('a settled discovered card with no results refetches state before opening (173-193)', async () => {
    stubFetch();
    responder = (url) => {
      if (url === '/api/tidal/playlists') return [{ id: 't1', name: 'Settled', track_count: 2 }];
      if (url === '/api/tidal/playlists/states') {
        return { states: [{ playlist_id: 't1', phase: 'discovered' }] };
      }
      if (url === '/api/tidal/state/t1') {
        return { phase: 'discovered', discovery_results: [{ spotify_data: { name: 'R' } }] };
      }
      return { tracks: [] };
    };
    render(<TidalHarness />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Discovery Complete')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Settled'));
    await waitFor(() => expect(screen.getByTestId('open-id')).toHaveTextContent('t1'));
    expect(calls.some((c) => c.url === '/api/tidal/state/t1')).toBe(true);
  });

  it('Refresh is disabled for the whole load — the double-click race cannot start (9-10, 67-69)', async () => {
    calls = [];
    let releaseStates: (value: unknown) => void = () => undefined;
    const statesGate = new Promise((resolve) => {
      releaseStates = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        calls.push({ url, method: 'GET', body: undefined });
        if (url === '/api/tidal/playlists') {
          return new Response(JSON.stringify([{ id: 't1', name: 'Racer', track_count: 1 }]));
        }
        if (url === '/api/tidal/playlists/states') {
          await statesGate;
          return new Response(JSON.stringify({ states: [] }));
        }
        return new Response(JSON.stringify({ tracks: [] }));
      }),
    );

    render(<TidalHarness />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    // Loading label + disabled button hold until the states tail resolves —
    // this is WHY hydrateStatesForLoaded's staleness guard is belt-and-braces
    // rather than load-bearing (unit-tested directly below).
    await waitFor(() => expect(screen.getByText('🔄 Loading...')).toBeDisabled());
    releaseStates(undefined);
    await waitFor(() => expect(screen.getByText('🔄 Refresh')).toBeEnabled());
    expect(screen.getByText('Racer')).toBeInTheDocument();
  });

  it('a failed list paints the ❌ placeholder and toasts (64-66)', async () => {
    calls = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ error: 'Tidal down' }), { status: 502 })),
    );
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    render(<TidalHarness />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('❌ Error: Tidal down')).toBeInTheDocument());
    expect(toast).toHaveBeenCalledWith('Error loading Tidal playlists: Tidal down', 'error');
  });

  it('#867: a fresh card click opens IMMEDIATELY without a track fetch', async () => {
    stubFetch();
    responder = (url) => {
      if (url === '/api/tidal/playlists') {
        return [{ id: 't1', name: 'Instant', track_count: 5 }];
      }
      if (url === '/api/tidal/playlists/states') return { states: [] };
      // The background loop's per-playlist fetch may still run — empty tracks.
      return { tracks: [] };
    };
    render(<TidalHarness />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Instant')).toBeInTheDocument());
    const before = calls.length;
    fireEvent.click(screen.getByText('Instant'));
    await waitFor(() => expect(screen.getByTestId('open-id')).toHaveTextContent('t1'));
    // No overlay fetch on click — only whatever the background loop already did.
    expect(calls.length).toBe(before);
    expect(screen.getByTestId('phase')).toHaveTextContent('fresh');
  });
});

function QobuzHarness({ onOpen }: { onOpen?: (id: string) => void }) {
  const vertical = useSourceVertical(SYNC_SOURCES.qobuz);
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <div>
      <QobuzTab vertical={vertical} onOpen={onOpen ?? setOpenId} />
      <span data-testid="open-id">{openId ?? 'none'}</span>
    </div>
  );
}

describe('QobuzTab', () => {
  it('a fresh click fetches tracks behind the overlay, projects them, then opens (1649-1680)', async () => {
    stubFetch();
    const overlay = vi.fn();
    const hideOverlay = vi.fn();
    window.showLoadingOverlay = overlay as typeof window.showLoadingOverlay;
    window.hideLoadingOverlay = hideOverlay as typeof window.hideLoadingOverlay;
    let clickFetches = 0;
    responder = (url) => {
      if (url === '/api/qobuz/playlists') {
        return { playlists: [{ id: 'q1', name: 'Qz List', track_count: 1 }] };
      }
      if (url === '/api/qobuz/playlist/q1') {
        clickFetches += 1;
        // First call: the background loop (returns nothing). Later: the click.
        return clickFetches === 1
          ? { tracks: [] }
          : {
              tracks: [
                { id: 'qt', name: 'QS', artists: ['QA'], album: 'QAl', duration_ms: 7, extra: 'x' },
              ],
            };
      }
      if (url === '/api/qobuz/playlists/states') return { states: [] };
      return { success: true };
    };
    render(<QobuzHarness />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Qz List')).toBeInTheDocument());
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/qobuz/playlists/states')).toBe(true),
    );

    fireEvent.click(screen.getByText('Qz List'));
    await waitFor(() => expect(screen.getByTestId('open-id')).toHaveTextContent('q1'));
    expect(overlay).toHaveBeenCalledWith('Loading Qz List...');
    expect(hideOverlay).toHaveBeenCalled();
    expect(screen.getByText('1 tracks')).toBeInTheDocument();
  });

  it("no tracks → 'Could not load tracks for this playlist', no open (1672-1676)", async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/qobuz/playlists') {
        return { playlists: [{ id: 'q2', name: 'Empty One', track_count: 0 }] };
      }
      if (url === '/api/qobuz/playlists/states') return { states: [] };
      return { tracks: [] };
    };
    render(<QobuzHarness />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Empty One')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Empty One'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Could not load tracks for this playlist', 'error'),
    );
    expect(screen.getByTestId('open-id')).toHaveTextContent('none');
  });

  it("empty account list paints 'No Qobuz playlists found.'", async () => {
    stubFetch();
    responder = (url) => (url === '/api/qobuz/playlists' ? { playlists: [] } : { states: [] });
    render(<QobuzHarness />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('No Qobuz playlists found.')).toBeInTheDocument());
  });
});

describe('shared helpers', () => {
  it('fetchAccountPlaylist hits the per-source playlist endpoint', async () => {
    stubFetch();
    responder = () => ({ tracks: [{ id: 1 }] });
    await expect(fetchAccountPlaylist('qobuz', '9')).resolves.toEqual({ tracks: [{ id: 1 }] });
    expect(calls[0].url).toBe('/api/qobuz/playlist/9');
  });

  it('resumeIfInFlight restarts only the running phases', () => {
    const resumeDiscovery = vi.fn();
    const resumeSync = vi.fn();
    const vertical = { resumeDiscovery, resumeSync } as unknown as Parameters<
      typeof resumeIfInFlight
    >[0];
    resumeIfInFlight(vertical, 'a', 'discovering');
    resumeIfInFlight(vertical, 'b', 'syncing');
    resumeIfInFlight(vertical, 'c', 'discovered');
    resumeIfInFlight(vertical, 'd', 'fresh');
    expect(resumeDiscovery).toHaveBeenCalledExactlyOnceWith('a');
    expect(resumeSync).toHaveBeenCalledExactlyOnceWith('b');
  });

  it('hydrateStatesForLoaded honours the staleness guard', async () => {
    stubFetch();
    responder = () => ({ states: [{ playlist_id: 'known', phase: 'discovering' }] });
    const hydrate = vi.fn();
    const resumeDiscovery = vi.fn();
    const vertical = { hydrate, resumeDiscovery, resumeSync: vi.fn() } as unknown as Parameters<
      typeof hydrateStatesForLoaded
    >[1];
    await hydrateStatesForLoaded(SYNC_SOURCES.tidal, vertical, () => ({ id: 'known' }), () => false);
    expect(hydrate).not.toHaveBeenCalled();
    expect(resumeDiscovery).not.toHaveBeenCalled();
  });

  it('hydrateStatesForLoaded skips rows whose playlist is not loaded (3273-3275)', async () => {
    stubFetch();
    responder = () => ({
      states: [
        { playlist_id: 'known', phase: 'discovered' },
        { playlist_id: 'stranger', phase: 'discovered' },
      ],
    });
    const hydrate = vi.fn();
    const vertical = {
      hydrate,
      resumeDiscovery: vi.fn(),
      resumeSync: vi.fn(),
    } as unknown as Parameters<typeof hydrateStatesForLoaded>[1];
    await hydrateStatesForLoaded(SYNC_SOURCES.tidal, vertical, (pid) =>
      pid === 'known' ? { id: 'known' } : undefined,
    );
    expect(hydrate).toHaveBeenCalledTimes(1);
    expect(hydrate).toHaveBeenCalledWith('known', expect.objectContaining({ phase: 'discovered' }));
  });
});
