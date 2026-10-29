/**
 * The Spotify and Deezer account tabs import into SoulSync. Rendered against
 * a captured fetch inside an import landing: list → import (that one
 * playlist's tracks, saved under the same mirror key the old engine wrote) →
 * land, and a playlist already in opens instead.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DeezerArlTab, SpotifyTab } from './account-tabs';
import { type ImportLanding, ImportLandingProvider } from './use-importer';

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let responder: (url: string) => unknown = () => ({});
let mirrors: { id: number; source: string; source_playlist_id: string }[] = [];

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
      if (url === '/api/mirrored-playlists') return new Response(JSON.stringify(mirrors));
      if (url === '/api/mirror-playlist') {
        return new Response(JSON.stringify({ success: true, playlist_id: 61 }));
      }
      return new Response(JSON.stringify(responder(url)));
    }),
  );
}

function renderTab(tab: React.ReactNode) {
  const land = vi.fn<ImportLanding['land']>();
  const showList = vi.fn();
  render(<ImportLandingProvider value={{ land, showList }}>{tab}</ImportLandingProvider>);
  return { land, showList };
}

const mirrorBody = () =>
  calls.find((c) => c.url === '/api/mirror-playlist')?.body as Record<string, unknown> | undefined;

beforeEach(() => {
  mirrors = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { showToast?: unknown }).showToast;
});

const SPOTIFY_ROWS = [
  {
    id: 'sp1',
    name: 'Late Night Drive',
    owner: 'boulder',
    track_count: 42,
    image_url: 'http://c/1',
  },
  { id: 'sp2', name: 'Gym', owner: 'boulder', track_count: 8 },
];

const SPOTIFY_TRACK = {
  id: 'trk1',
  name: 'Nightcall',
  artists: [{ name: 'Kavinsky' }],
  album: { name: 'OutRun', images: [{ url: 'http://art' }] },
  duration_ms: 258000,
};

describe('SpotifyTab', () => {
  it('lists the account on mount, each card ready to import', async () => {
    stubFetch();
    responder = (url) => (url === '/api/spotify/playlists' ? SPOTIFY_ROWS : {});
    renderTab(<SpotifyTab />);
    await waitFor(() => expect(screen.getByText('Late Night Drive')).toBeInTheDocument());
    expect(screen.getByText('42 tracks · boulder')).toBeInTheDocument();
    expect(screen.getByText('Import all 2')).toBeInTheDocument();
    expect(document.querySelector('#spotify-refresh-btn')).not.toBeNull();
    expect(document.querySelector('#spotify-card-sp1')).not.toBeNull();
  });

  it("importing saves it under 'spotify' + its id, with the track's own art, and lands", async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/spotify/playlists'
        ? SPOTIFY_ROWS
        : url === '/api/spotify/playlist/sp1'
          ? { name: 'Late Night Drive', tracks: [SPOTIFY_TRACK] }
          : {};
    const { land } = renderTab(<SpotifyTab />);
    await waitFor(() => expect(screen.getByText('Late Night Drive')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Import Late Night Drive into SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(61, 'Late Night Drive', false));
    expect(mirrorBody()).toMatchObject({
      source: 'spotify',
      source_playlist_id: 'sp1',
      name: 'Late Night Drive',
      owner: 'boulder',
      image_url: 'http://c/1',
    });
    expect((mirrorBody()!.tracks as Record<string, unknown>[])[0]).toMatchObject({
      track_name: 'Nightcall',
      artist_name: 'Kavinsky',
      album_name: 'OutRun',
      image_url: 'http://art',
      source_track_id: 'trk1',
    });
  });

  it('a playlist the old engine already mirrored reads as in, and opens', async () => {
    stubFetch();
    responder = (url) => (url === '/api/spotify/playlists' ? SPOTIFY_ROWS : {});
    mirrors = [{ id: 4, source: 'spotify', source_playlist_id: 'sp2' }];
    const { land } = renderTab(<SpotifyTab />);
    await waitFor(() => expect(screen.getByText('In SoulSync')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Open Gym in SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(4, 'Gym', true));
    expect(mirrorBody()).toBeUndefined();
  });

  it('an empty account shows the empty copy', async () => {
    stubFetch();
    responder = (url) => (url === '/api/spotify/playlists' ? [] : {});
    renderTab(<SpotifyTab />);
    await waitFor(() =>
      expect(screen.getByText('No Spotify playlists found.')).toBeInTheDocument(),
    );
  });

  it('a list error paints the container AND toasts, then re-enables refresh', async () => {
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === '/api/mirrored-playlists'
          ? new Response('[]')
          : new Response(JSON.stringify({ error: 'Spotify down' }), { status: 500 }),
      ),
    );
    renderTab(<SpotifyTab />);
    // the server's own words, not "No Spotify playlists found."
    await waitFor(() => expect(screen.getByText('❌ Error: Spotify down')).toBeInTheDocument());
    expect(toast).toHaveBeenCalledWith('Error loading Spotify playlists: Spotify down', 'error');
    expect(document.querySelector('#spotify-refresh-btn')).not.toBeDisabled();
  });
});

describe('DeezerArlTab', () => {
  it("imports under 'deezer' + the RAW id, through the async load job", async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/deezer/arl-playlists'
        ? [{ id: 7, name: 'Flow Mix', track_count: 2 }]
        : url === '/api/deezer/arl-playlist/7?async=1'
          ? { pending: true, job_id: 'arl-job-7' }
          : url === '/api/deezer/playlist-load/arl-job-7'
            ? {
                status: 'complete',
                playlist: {
                  tracks: [
                    { id: 'd1', name: 'Dz', artists: [{ name: 'DA' }], album: { name: 'DAl' } },
                  ],
                },
              }
            : {};
    const { land } = renderTab(<DeezerArlTab />);
    await waitFor(() => expect(screen.getByText('Flow Mix')).toBeInTheDocument());
    expect(document.querySelector('#deezer-arl-card-7')).not.toBeNull();
    fireEvent.click(screen.getByLabelText('Import Flow Mix into SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(61, 'Flow Mix', false));
    expect(mirrorBody()).toMatchObject({ source: 'deezer', source_playlist_id: '7' });
    expect((mirrorBody()!.tracks as Record<string, unknown>[])[0]).toMatchObject({
      track_name: 'Dz',
      artist_name: 'DA',
      album_name: 'DAl',
    });
  });

  it('a failed track load says why and lands nowhere', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    responder = (url) =>
      url === '/api/deezer/arl-playlists'
        ? [{ id: 7, name: 'Flow Mix', track_count: 2 }]
        : url === '/api/deezer/arl-playlist/7?async=1'
          ? { pending: false, error: 'ARL expired' }
          : {};
    const { land } = renderTab(<DeezerArlTab />);
    await waitFor(() => expect(screen.getByText('Flow Mix')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Import Flow Mix into SoulSync'));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('ARL expired', 'error'));
    expect(land).not.toHaveBeenCalled();
  });
});

describe('keep your own copy (#1913)', () => {
  const copyResponder = (url: string) =>
    url === '/api/spotify/playlists'
      ? SPOTIFY_ROWS
      : url === '/api/spotify/playlist/sp1'
        ? { name: 'Late Night Drive', tracks: [SPOTIFY_TRACK] }
        : url === '/api/user-playlists'
          ? { success: true, id: 88, added: 1 }
          : url === '/api/user-playlists/88/cover'
            ? { image_url: '/cover/88' }
            : {};

  function renderWithMine() {
    const land = vi.fn<ImportLanding['land']>();
    const landMine = vi.fn();
    render(
      <ImportLandingProvider value={{ land, showList: vi.fn(), landMine }}>
        <SpotifyTab />
      </ImportLandingProvider>,
    );
    return { land, landMine };
  }

  it('makes a My Playlists playlist from the songs, not a mirror, and lands on it', async () => {
    stubFetch();
    responder = copyResponder;
    const { land, landMine } = renderWithMine();
    await waitFor(() => expect(screen.getByText('Late Night Drive')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Keep Late Night Drive as your own playlist'));
    await waitFor(() => expect(landMine).toHaveBeenCalledWith(88, 'Late Night Drive'));
    // the card's own action didn't run: nothing was mirrored, nothing landed there
    expect(mirrorBody()).toBeUndefined();
    expect(land).not.toHaveBeenCalled();
    const create = calls.find((c) => c.url === '/api/user-playlists' && c.method === 'POST');
    expect(create?.body).toMatchObject({
      name: 'Late Night Drive',
      tracks: [
        {
          track_name: 'Nightcall',
          artist_name: 'Kavinsky',
          album_name: 'OutRun',
          duration_ms: 258000,
          // where it came from, so identifying it fetches it directly
          source: 'spotify',
          source_id: 'trk1',
        },
      ],
    });
    // the playlist's own cover comes along
    expect(calls.find((c) => c.url === '/api/user-playlists/88/cover')).toMatchObject({
      method: 'PUT',
      body: { image_url: 'http://c/1' },
    });
  });

  it('a copy is offered even once the playlist is imported', async () => {
    stubFetch();
    responder = copyResponder;
    mirrors = [{ id: 4, source: 'spotify', source_playlist_id: 'sp1' }];
    renderWithMine();
    await waitFor(() => expect(screen.getByText('In SoulSync')).toBeInTheDocument());
    expect(screen.getByLabelText('Keep Late Night Drive as your own playlist')).toBeInTheDocument();
  });

  it('a failed copy says why and lands nowhere', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/user-playlists' ? { error: 'Playlist name taken' } : copyResponder(url);
    window.showToast = vi.fn();
    const { landMine } = renderWithMine();
    await waitFor(() => expect(screen.getByText('Late Night Drive')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Keep Late Night Drive as your own playlist'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Playlist name taken', 'error'),
    );
    expect(landMine).not.toHaveBeenCalled();
  });
});
