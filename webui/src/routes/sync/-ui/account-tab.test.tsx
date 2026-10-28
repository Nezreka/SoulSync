/**
 * The Tidal / Qobuz / YouTube Music tabs: your playlists on that service,
 * each one ready to import. Rendered against a captured fetch inside an
 * import landing, so what's asserted is the whole path: refresh lists them
 * (and mirrors nothing), import fetches that one playlist and saves it, and
 * you land on it.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { forgetAccountPlaylists } from '../-sync.account-cache';
import { fetchAccountPlaylist } from '../-sync.api';
import { SYNC_SOURCES } from '../-sync.sources';
import { QobuzTab, TidalTab, YTMusicTab } from './account-tab';
import { hydrateStatesForLoaded, resumeIfInFlight } from './url-import-tab';
import { type ImportLanding, ImportLandingProvider } from './use-importer';

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let responder: (url: string) => unknown = () => ({});
let mirrors: { id: number; source: string; source_playlist_id: string }[] = [];
let nextMirrorId = 50;

function stubFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ url, method: init?.method ?? 'GET', body });
      if (url === '/api/mirrored-playlists') return new Response(JSON.stringify(mirrors));
      if (url === '/api/mirror-playlist') {
        return new Response(JSON.stringify({ success: true, playlist_id: nextMirrorId++ }));
      }
      return new Response(JSON.stringify(responder(url)));
    }),
  );
}

function renderTab(tab: React.ReactNode) {
  const land = vi.fn<ImportLanding['land']>();
  const showList = vi.fn();
  const view = render(
    <ImportLandingProvider value={{ land, showList }}>{tab}</ImportLandingProvider>,
  );
  return { land, showList, view };
}

const mirrorBodies = () =>
  calls
    .filter((c) => c.url === '/api/mirror-playlist')
    .map((c) => c.body as Record<string, unknown>);

beforeEach(() => {
  forgetAccountPlaylists();
  mirrors = [];
  nextMirrorId = 50;
});

afterEach(() => {
  // The account cache is module-level, so it outlives a test. Left alone it
  // would hand the NEXT case a pre-loaded tab, and the failure would depend on
  // file order.
  forgetAccountPlaylists();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { showToast?: unknown }).showToast;
  delete (window as { showLoadingOverlay?: unknown }).showLoadingOverlay;
  delete (window as { hideLoadingOverlay?: unknown }).hideLoadingOverlay;
});

const TIDAL_LIST = [
  { id: 't1', name: 'Gappy', track_count: 30, owner: 'Me', image_url: 'http://art/1' },
  { id: 't2', name: 'Whole', track_count: 12 },
];

function tidalResponder(url: string) {
  if (url === '/api/tidal/playlists') return TIDAL_LIST;
  if (url === '/api/tidal/playlist/t1') {
    return {
      tracks: [{ name: 'S1', artists: ['A1'], album: 'Al', duration_ms: 5, id: 'x' }],
      source_skipped: { videos: 1, unavailable: 29 },
    };
  }
  if (url === '/api/tidal/playlist/t2') {
    return { tracks: [{ name: 'S2', artists: ['A2'], id: 'y' }], source_skipped: null };
  }
  return {};
}

describe('TidalTab', () => {
  it('starts on the click-Refresh placeholder and loads nothing but the index', () => {
    stubFetch();
    renderTab(<TidalTab />);
    expect(screen.getByText("Click 'Refresh' to load your Tidal playlists.")).toBeInTheDocument();
    expect(calls.filter((c) => c.url !== '/api/mirrored-playlists')).toEqual([]);
  });

  it('refresh lists the playlists and mirrors NOTHING, with no per-playlist crawl', async () => {
    stubFetch();
    responder = tidalResponder;
    renderTab(<TidalTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Gappy')).toBeInTheDocument());
    expect(screen.getByText('30 tracks · Me')).toBeInTheDocument();
    expect(screen.getByText('12 tracks')).toBeInTheDocument();
    expect(screen.getAllByText('Import')).toHaveLength(2);
    expect(screen.getByText('Import all 2')).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 20));
    expect(calls.some((c) => c.url.startsWith('/api/tidal/playlist/'))).toBe(false);
    expect(mirrorBodies()).toEqual([]);
  });

  it('importing fetches that one playlist, saves it with what tidal skipped, and lands', async () => {
    stubFetch();
    responder = tidalResponder;
    const { land } = renderTab(<TidalTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Gappy')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('Import Gappy into SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(50, 'Gappy', false));
    expect(calls.filter((c) => c.url.startsWith('/api/tidal/playlist/')).map((c) => c.url)).toEqual(
      ['/api/tidal/playlist/t1'],
    );
    const [body] = mirrorBodies();
    expect(body).toMatchObject({
      source: 'tidal',
      source_playlist_id: 't1',
      name: 'Gappy',
      owner: 'Me',
      image_url: 'http://art/1',
      source_skipped: { videos: 1, unavailable: 29 },
    });
    expect((body.tracks as Record<string, unknown>[])[0]).toMatchObject({
      track_name: 'S1',
      artist_name: 'A1',
      album_name: 'Al',
      source_track_id: 'x',
    });
    // the card says it's in now
    await waitFor(() => expect(screen.getByText('In SoulSync')).toBeInTheDocument());
  });

  it('#1613: a whole playlist sends source_skipped null, so a stored note clears', async () => {
    stubFetch();
    responder = tidalResponder;
    const { land } = renderTab(<TidalTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Whole')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Import Whole into SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalled());
    expect(mirrorBodies()[0]).toHaveProperty('source_skipped', null);
  });

  it('one already in reads In SoulSync and opens without importing again', async () => {
    stubFetch();
    responder = tidalResponder;
    mirrors = [{ id: 7, source: 'tidal', source_playlist_id: 't1' }];
    const { land } = renderTab(<TidalTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('In SoulSync')).toBeInTheDocument());
    // only the one that isn't in counts toward Import all
    expect(screen.queryByText(/^Import all/)).toBeNull();

    fireEvent.click(screen.getByLabelText('Open Gappy in SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(7, 'Gappy', true));
    expect(mirrorBodies()).toEqual([]);
  });

  it('Import all brings in every one not in yet, then shows the list', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/tidal/playlists'
        ? [...TIDAL_LIST, { id: 't3', name: 'Third', track_count: 1 }]
        : url === '/api/tidal/playlist/t3'
          ? { tracks: [{ name: 'S3', artists: ['A3'], id: 'z' }] }
          : tidalResponder(url);
    mirrors = [{ id: 7, source: 'tidal', source_playlist_id: 't2' }];
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    const { land, showList } = renderTab(<TidalTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Import all 2')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Import all 2'));
    await waitFor(() => expect(showList).toHaveBeenCalled());
    expect(mirrorBodies().map((b) => b.source_playlist_id)).toEqual(['t1', 't3']);
    expect(land).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(
      "Imported 2 playlists. They're identifying in the background",
      'success',
    );
  });

  it('a failed import says why and lands nowhere', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/tidal/playlists'
        ? TIDAL_LIST
        : url === '/api/tidal/playlist/t1'
          ? { error: 'Tidal token expired' }
          : {};
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    const { land } = renderTab(<TidalTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Gappy')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Import Gappy into SoulSync'));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Tidal token expired', 'error'));
    expect(land).not.toHaveBeenCalled();
    expect(mirrorBodies()).toEqual([]);
    // and the card can be tried again
    expect(screen.getAllByText('Import')).toHaveLength(2);
  });

  it('a failed list paints the ❌ placeholder and toasts', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === '/api/mirrored-playlists'
          ? new Response('[]')
          : new Response(JSON.stringify({ error: 'boom' }), { status: 500 }),
      ),
    );
    renderTab(<TidalTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('❌ Error: boom')).toBeInTheDocument());
    expect(toast).toHaveBeenCalledWith('Error loading Tidal playlists: boom', 'error');
  });
});

describe('QobuzTab', () => {
  it('imports from the qobuz endpoints under the qobuz source', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/qobuz/playlists'
        ? [{ id: 'q1', name: 'Hi-Res', track_count: 2 }]
        : url === '/api/qobuz/playlist/q1'
          ? { tracks: [{ name: 'Q', artists: ['QA'], id: 'qq' }] }
          : {};
    const { land } = renderTab(<QobuzTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Hi-Res')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Import Hi-Res into SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(50, 'Hi-Res', false));
    expect(mirrorBodies()[0]).toMatchObject({ source: 'qobuz', source_playlist_id: 'q1' });
  });

  it('an empty playlist says it has nothing to import', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/qobuz/playlists'
        ? [{ id: 'q1', name: 'Nothing', track_count: 0 }]
        : url === '/api/qobuz/playlist/q1'
          ? { tracks: [] }
          : {};
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    const { land } = renderTab(<QobuzTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Nothing')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Import Nothing into SoulSync'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Nothing has no tracks to import', 'error'),
    );
    expect(land).not.toHaveBeenCalled();
  });

  it("an empty account says 'No Qobuz playlists found.'", async () => {
    stubFetch();
    responder = (url) => (url === '/api/qobuz/playlists' ? [] : {});
    renderTab(<QobuzTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('No Qobuz playlists found.')).toBeInTheDocument());
  });
});

describe('YTMusicTab', () => {
  it('imports from the ytmusic endpoints (not qobuz)', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/ytmusic/playlists'
        ? [{ id: 'LM', name: 'Liked Music', track_count: 1 }]
        : url === '/api/ytmusic/playlist/LM'
          ? { tracks: [{ name: 'Y', artists: ['YA'], id: 'yy' }] }
          : {};
    const { land } = renderTab(<YTMusicTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('Liked Music')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Import Liked Music into SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalled());
    expect(calls.some((c) => c.url === '/api/ytmusic/playlist/LM')).toBe(true);
    expect(mirrorBodies()[0]).toMatchObject({ source: 'ytmusic', source_playlist_id: 'LM' });
  });

  it('an unauthenticated account (401) surfaces the backend error, not a blank list', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === '/api/mirrored-playlists'
          ? new Response('[]')
          : new Response(JSON.stringify({ error: 'YouTube Music not authenticated.' }), {
              status: 401,
            }),
      ),
    );
    renderTab(<YTMusicTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() =>
      expect(screen.getByText('❌ Error: YouTube Music not authenticated.')).toBeInTheDocument(),
    );
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

  it('hydrateStatesForLoaded honours the staleness guard before the loop', async () => {
    stubFetch();
    responder = () => ({ states: [{ playlist_id: 'known', phase: 'discovering' }] });
    const hydrate = vi.fn();
    const resumeDiscovery = vi.fn();
    const vertical = { hydrate, resumeDiscovery, resumeSync: vi.fn() } as unknown as Parameters<
      typeof hydrateStatesForLoaded
    >[1];
    await hydrateStatesForLoaded(
      SYNC_SOURCES.tidal,
      vertical,
      () => ({ id: 'known' }),
      () => false,
    );
    expect(hydrate).not.toHaveBeenCalled();
    expect(resumeDiscovery).not.toHaveBeenCalled();
  });

  it('...and again INSIDE the loop, so a mid-hydration refresh stops it', async () => {
    stubFetch();
    responder = () => ({
      states: [
        { playlist_id: 'a', phase: 'discovered' },
        { playlist_id: 'b', phase: 'discovered' },
      ],
    });
    const hydrate = vi.fn();
    const vertical = {
      hydrate,
      resumeDiscovery: vi.fn(),
      resumeSync: vi.fn(),
    } as unknown as Parameters<typeof hydrateStatesForLoaded>[1];
    // Current for the pre-loop check and row a, stale from row b on.
    let checks = 0;
    await hydrateStatesForLoaded(
      SYNC_SOURCES.tidal,
      vertical,
      (pid) => ({ id: pid }),
      () => checks++ < 2,
    );
    expect(hydrate).toHaveBeenCalledExactlyOnceWith(
      'a',
      expect.objectContaining({ playlist_id: 'a', playlist: { id: 'a' } }),
    );
  });

  it('one malformed state row does not drop the rest (867, 946-948)', async () => {
    stubFetch();
    responder = () => ({
      states: [
        { playlist_id: 'boom', phase: 'discovered' },
        { playlist_id: 'fine', phase: 'discovered' },
      ],
    });
    const hydrate = vi.fn((id: string) => {
      if (id === 'boom') throw new Error('bad row');
    });
    const vertical = {
      hydrate,
      resumeDiscovery: vi.fn(),
      resumeSync: vi.fn(),
    } as unknown as Parameters<typeof hydrateStatesForLoaded>[1];
    await hydrateStatesForLoaded(SYNC_SOURCES.tidal, vertical, (pid) => ({ id: pid }));
    expect(hydrate).toHaveBeenCalledTimes(2);
    expect(hydrate).toHaveBeenLastCalledWith('fine', expect.anything());
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

describe('Specialmed: playlists survive leaving the page', () => {
  it('a remounted tab shows what it already loaded, and does NOT re-fetch the list', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/tidal/playlists' ? [{ id: 't1', name: 'My Mix 1', track_count: 3 }] : {};
    const first = renderTab(<TidalTab />);
    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(screen.getByText('My Mix 1')).toBeInTheDocument());
    first.view.unmount();
    const listCalls = () => calls.filter((c) => c.url === '/api/tidal/playlists').length;
    const before = listCalls();

    renderTab(<TidalTab />);
    expect(screen.getByText('My Mix 1')).toBeInTheDocument();
    expect(
      screen.queryByText("Click 'Refresh' to load your Tidal playlists."),
    ).not.toBeInTheDocument();
    expect(listCalls()).toBe(before);

    fireEvent.click(screen.getByText('🔄 Refresh'));
    await waitFor(() => expect(listCalls()).toBe(before + 1));
  });
});
