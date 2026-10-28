/**
 * The URL-import tabs: paste a link and it's imported. Rendered against a
 * captured fetch inside an import landing, so what's asserted is the whole
 * path: load → mirror body → land on the new playlist, and a re-paste or a
 * card click opening what's already in.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { forgetAccountPlaylists } from '../-sync.account-cache';
import { fetchYouTubePlaylists } from '../-sync.api';
import { UrlHistoryBar, useUrlHistory } from './url-history-bar';
import { DeezerLinkTab, ITunesLinkTab, SpotifyPublicTab, YouTubeTab } from './url-import-tab';
import { type ImportLanding, ImportLandingProvider } from './use-importer';

interface Call {
  url: string;
  method: string;
  body: unknown;
}

let calls: Call[] = [];
let responder: (url: string, method: string) => unknown = () => ({});
/** the mirrors that exist, as /api/mirrored-playlists lists them */
let mirrors: { id: number; source: string; source_playlist_id: string }[] = [];

function stubFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
      if (url === '/api/mirrored-playlists') return new Response(JSON.stringify(mirrors));
      if (url === '/api/mirror-playlist') {
        return new Response(JSON.stringify({ success: true, playlist_id: 41 }));
      }
      return new Response(JSON.stringify(responder(url, method)));
    }),
  );
}

function landing() {
  const land = vi.fn<ImportLanding['land']>();
  const showList = vi.fn();
  return { value: { land, showList }, land, showList };
}

function renderTab(tab: React.ReactNode) {
  const l = landing();
  render(<ImportLandingProvider value={l.value}>{tab}</ImportLandingProvider>);
  return l;
}

function mirrorBody() {
  return calls.find((c) => c.url === '/api/mirror-playlist')?.body as
    | { source: string; source_playlist_id: string; tracks: Record<string, unknown>[] }
    | undefined;
}

beforeEach(() => {
  forgetAccountPlaylists();
  mirrors = [];
});

afterEach(() => {
  forgetAccountPlaylists();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  delete (window as { showToast?: unknown }).showToast;
  delete (window as { showLoadingOverlay?: unknown }).showLoadingOverlay;
  delete (window as { hideLoadingOverlay?: unknown }).hideLoadingOverlay;
});

const DEEZER_PLAYLIST = {
  id: 123,
  name: 'Dz Mix',
  track_count: 2,
  owner: 'DzOwner',
  image_url: 'http://img',
  tracks: [
    { name: 'T1', artists: ['A1'], album: 'Al1', duration_ms: 10, id: 1 },
    { name: 'T2', artists: ['A2'], album: 'Al2', duration_ms: 20, id: 2 },
  ],
};

function deezerResponder(url: string) {
  if (url === '/api/deezer/playlist/123?async=1') return { pending: true, job_id: 'job-123' };
  if (url === '/api/deezer/playlist-load/job-123') {
    return { status: 'complete', playlist: DEEZER_PLAYLIST };
  }
  return { success: true };
}

function pasteDeezer(url = 'https://www.deezer.com/en/playlist/123') {
  fireEvent.change(screen.getByPlaceholderText('Paste Deezer Playlist URL...'), {
    target: { value: url },
  });
  fireEvent.click(screen.getByText('Load Playlist'));
}

describe('DeezerLinkTab', () => {
  it('a pasted link is imported: mirror body, history, then you land on it', async () => {
    stubFetch();
    responder = deezerResponder;
    const { land } = renderTab(<DeezerLinkTab />);
    pasteDeezer();

    await waitFor(() => expect(land).toHaveBeenCalledWith(41, 'Dz Mix', false));
    expect(mirrorBody()).toMatchObject({
      source: 'deezer',
      source_playlist_id: '123',
      name: 'Dz Mix',
      description: 'https://www.deezer.com/en/playlist/123',
      owner: 'DzOwner',
      image_url: 'http://img',
    });
    expect(mirrorBody()!.tracks[0]).toEqual({
      track_name: 'T1',
      artist_name: 'A1',
      album_name: 'Al1',
      duration_ms: 10,
      image_url: null,
      source_track_id: 1,
      extra_data: null,
    });
    expect(localStorage.getItem('soulsync-url-history-deezer')).toContain('Dz Mix');
    // the card stays, and says it's in
    await waitFor(() => expect(screen.getByText('In SoulSync')).toBeInTheDocument());
    expect(screen.getByText('2 tracks')).toBeInTheDocument();
  });

  it('pasting it again opens the one already in, without loading or mirroring again', async () => {
    stubFetch();
    responder = deezerResponder;
    const { land } = renderTab(<DeezerLinkTab />);
    pasteDeezer();
    await waitFor(() => expect(land).toHaveBeenCalledTimes(1));
    const before = calls.length;

    pasteDeezer('https://www.deezer.com/playlist/123');
    await waitFor(() => expect(land).toHaveBeenCalledTimes(2));
    expect(land).toHaveBeenLastCalledWith(41, 'Dz Mix', true);
    expect(calls.slice(before)).toEqual([]);
  });

  it('a playlist that was imported before reads as in, and opens', async () => {
    stubFetch();
    responder = deezerResponder;
    mirrors = [{ id: 7, source: 'deezer', source_playlist_id: '123' }];
    const { land } = renderTab(<DeezerLinkTab />);
    pasteDeezer();
    await waitFor(() => expect(land).toHaveBeenCalledWith(7, 'Dz Mix', true));
    expect(mirrorBody()).toBeUndefined();
  });

  it('invalid input toasts without fetching anything but the index', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    renderTab(<DeezerLinkTab />);
    fireEvent.click(screen.getByText('Load Playlist'));
    expect(toast).toHaveBeenCalledWith('Please paste a Deezer playlist URL', 'error');
    expect(calls.filter((c) => c.url !== '/api/mirrored-playlists')).toEqual([]);
  });

  it('a backend error lands in the catch toast, and nothing is imported', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    responder = (url) =>
      url === '/api/deezer/playlist/123?async=1' ? { error: 'Deezer said no' } : {};
    const { land } = renderTab(<DeezerLinkTab />);
    pasteDeezer();
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.stringMatching(/^Error loading Deezer playlist/),
        'error',
      ),
    );
    expect(mirrorBody()).toBeUndefined();
    expect(land).not.toHaveBeenCalled();
  });

  it('a link routed from the Add-playlist sheet imports on arrival', async () => {
    stubFetch();
    responder = deezerResponder;
    const consumed = vi.fn();
    const { land } = renderTab(
      <DeezerLinkTab
        pendingUrl="https://www.deezer.com/en/playlist/123"
        onPendingConsumed={consumed}
      />,
    );
    await waitFor(() => expect(land).toHaveBeenCalledWith(41, 'Dz Mix', false));
    expect(consumed).toHaveBeenCalled();
  });
});

describe('SpotifyPublicTab', () => {
  it('parse POSTs {url}, imports with joined artist names, shows the type badge', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/spotify/parse-public'
        ? {
            url_hash: 'h1',
            id: 'abc',
            url: 'https://open.spotify.com/album/abc',
            type: 'album',
            name: 'Sp Album',
            subtitle: 'Sp Artist',
            track_count: 1,
            tracks: [
              {
                name: 'S1',
                artists: [{ name: 'A' }, { name: 'B' }],
                album: { name: 'Al' },
                id: 't1',
              },
            ],
          }
        : { success: true };
    const { land } = renderTab(<SpotifyPublicTab />);
    fireEvent.change(screen.getByPlaceholderText('Paste Spotify Playlist or Album URL...'), {
      target: { value: 'https://open.spotify.com/album/abc' },
    });
    fireEvent.click(screen.getByText('Load'));

    await waitFor(() => expect(land).toHaveBeenCalledWith(41, 'Sp Album', false));
    const parse = calls.find((c) => c.url === '/api/spotify/parse-public');
    expect(parse).toMatchObject({
      method: 'POST',
      body: { url: 'https://open.spotify.com/album/abc' },
    });
    expect(mirrorBody()).toMatchObject({
      source: 'spotify_public',
      source_playlist_id: 'h1',
      owner: 'Sp Artist',
      description: 'https://open.spotify.com/album/abc',
    });
    expect(mirrorBody()!.tracks[0].artist_name).toBe('A, B');
    expect(screen.getByText('Album')).toBeInTheDocument();
  });

  it('a result.error toasts and imports nothing', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    responder = (url) => (url === '/api/spotify/parse-public' ? { error: 'Private playlist' } : {});
    const { land } = renderTab(<SpotifyPublicTab />);
    fireEvent.change(screen.getByPlaceholderText('Paste Spotify Playlist or Album URL...'), {
      target: { value: 'https://open.spotify.com/playlist/xyz' },
    });
    fireEvent.click(screen.getByText('Load'));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Error: Private playlist', 'error'));
    expect(mirrorBody()).toBeUndefined();
    expect(land).not.toHaveBeenCalled();
  });

  it('a second paste that resolves to the same hash opens the first', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/spotify/parse-public'
        ? {
            url_hash: 'same',
            url: 'https://open.spotify.com/playlist/p1',
            name: 'One',
            tracks: [{ name: 'x', artists: [{ name: 'a' }] }],
          }
        : { success: true };
    const { land } = renderTab(<SpotifyPublicTab />);
    const input = screen.getByPlaceholderText('Paste Spotify Playlist or Album URL...');
    fireEvent.change(input, { target: { value: 'https://open.spotify.com/playlist/p1' } });
    fireEvent.click(screen.getByText('Load'));
    await waitFor(() => expect(land).toHaveBeenCalledTimes(1));
    fireEvent.change(input, { target: { value: 'https://open.spotify.com/playlist/p1?si=abc' } });
    fireEvent.click(screen.getByText('Load'));
    await waitFor(() => expect(land).toHaveBeenCalledTimes(2));
    expect(land).toHaveBeenLastCalledWith(41, 'One', true);
    expect(calls.filter((c) => c.url === '/api/mirror-playlist')).toHaveLength(1);
  });
});

describe('ITunesLinkTab', () => {
  it('validates, then imports under itunes_link', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    const { land } = renderTab(<ITunesLinkTab />);
    fireEvent.click(screen.getByText('Load'));
    expect(toast).toHaveBeenCalledWith('Please enter a iTunes URL', 'error');

    responder = (url) =>
      url === '/api/itunes-link/parse'
        ? {
            url_hash: 'ih1',
            id: '42',
            type: 'track',
            url: 'https://music.apple.com/us/song/x/42',
            name: 'One Song',
            track_count: 1,
            tracks: [{ name: 'One Song', artists: [{ name: 'A' }] }],
          }
        : { success: true };
    fireEvent.change(
      screen.getByPlaceholderText('Paste iTunes or Apple Music Album/Track URL...'),
      { target: { value: 'https://music.apple.com/us/song/x/42' } },
    );
    fireEvent.click(screen.getByText('Load'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(41, 'One Song', false));
    expect(mirrorBody()).toMatchObject({ source: 'itunes_link', source_playlist_id: 'ih1' });
    expect(screen.getByText('Track')).toBeInTheDocument();
  });
});

describe('fetchYouTubePlaylists', () => {
  it('unwraps {playlists} and throws the backend error on !ok', async () => {
    stubFetch();
    responder = () => ({ playlists: [{ url_hash: 'h' }] });
    await expect(fetchYouTubePlaylists()).resolves.toEqual([{ url_hash: 'h' }]);
    expect(calls[0]).toMatchObject({ url: '/api/youtube/playlists', method: 'GET' });

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ error: 'YT down' }), { status: 500 })),
    );
    await expect(fetchYouTubePlaylists()).rejects.toThrow('YT down');
  });
});

describe('YouTubeTab', () => {
  it('lists the playlists the server holds from earlier pastes, each one opening or importing', async () => {
    stubFetch();
    mirrors = [{ id: 9, source: 'youtube', source_playlist_id: 'yh1' }];
    responder = (url) =>
      url === '/api/youtube/playlists'
        ? {
            playlists: [
              { url_hash: 'yh1', playlist: { name: 'Stored YT', tracks: [{}, {}, {}, {}, {}] } },
              {
                url_hash: 'yh2',
                playlist: { name: 'Never In', tracks: [{ title: 'T', artist: 'A' }] },
              },
            ],
          }
        : {};
    const { land } = renderTab(<YouTubeTab />);
    await waitFor(() => expect(screen.getByText('Stored YT')).toBeInTheDocument());
    expect(screen.getByText('5 tracks')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('In SoulSync')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('Open Stored YT in SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(9, 'Stored YT', true));

    fireEvent.click(screen.getByLabelText('Import Never In into SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(41, 'Never In', false));
    expect(mirrorBody()).toMatchObject({ source: 'youtube', source_playlist_id: 'yh2' });
  });

  it('parse shows the temp card, then imports and lands', async () => {
    stubFetch();
    let releaseParse: (value: unknown) => void = () => undefined;
    const parseGate = new Promise((resolve) => {
      releaseParse = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({
          url,
          method: init?.method ?? 'GET',
          body: init?.body ? JSON.parse(init.body as string) : undefined,
        });
        if (url === '/api/mirrored-playlists') return new Response(JSON.stringify([]));
        if (url === '/api/youtube/playlists')
          return new Response(JSON.stringify({ playlists: [] }));
        if (url === '/api/mirror-playlist') {
          return new Response(JSON.stringify({ success: true, playlist_id: 41 }));
        }
        if (url === '/api/youtube/parse') {
          await parseGate;
          return new Response(
            JSON.stringify({
              url_hash: 'newhash',
              name: 'Fresh YT',
              tracks: [{ title: 'V1', artist: 'A1', id: 'v1' }],
            }),
          );
        }
        return new Response(JSON.stringify({ success: true }));
      }),
    );
    const { land } = renderTab(<YouTubeTab />);
    fireEvent.change(screen.getByPlaceholderText('Paste YouTube Music Playlist URL...'), {
      target: { value: 'https://youtube.com/playlist?list=NEW' },
    });
    fireEvent.click(screen.getByText('Parse Playlist'));
    await waitFor(() =>
      expect(screen.getByText('Parsing YouTube playlist...')).toBeInTheDocument(),
    );

    releaseParse(undefined);
    await waitFor(() => expect(land).toHaveBeenCalledWith(41, 'Fresh YT', false));
    expect(screen.queryByText('Parsing YouTube playlist...')).not.toBeInTheDocument();
    expect(mirrorBody()).toMatchObject({ source: 'youtube', source_playlist_id: 'newhash' });
    expect(mirrorBody()!.tracks[0]).toMatchObject({
      track_name: 'V1',
      artist_name: 'A1',
      album_name: '',
    });
  });

  it('two URL variants resolving to ONE url_hash produce ONE card and one import', async () => {
    stubFetch();
    responder = (url) => {
      if (url === '/api/youtube/playlists') return { playlists: [] };
      if (url === '/api/youtube/parse')
        return { url_hash: 'samehash', name: 'One List', tracks: [{ title: 'T' }] };
      return { success: true };
    };
    const { land } = renderTab(<YouTubeTab />);
    const input = screen.getByPlaceholderText('Paste YouTube Music Playlist URL...');
    fireEvent.change(input, { target: { value: 'https://youtube.com/playlist?list=A' } });
    fireEvent.click(screen.getByText('Parse Playlist'));
    await waitFor(() => expect(land).toHaveBeenCalledTimes(1));
    fireEvent.change(input, { target: { value: 'https://music.youtube.com/playlist?list=A' } });
    fireEvent.click(screen.getByText('Parse Playlist'));
    await waitFor(() => expect(land).toHaveBeenCalledTimes(2));
    expect(land).toHaveBeenLastCalledWith(41, 'One List', true);
    expect(document.querySelectorAll('[id^="youtube-card-"]')).toHaveLength(1);
    expect(calls.filter((c) => c.url === '/api/mirror-playlist')).toHaveLength(1);
  });

  it('a playlist with no tracks says so instead of importing an empty mirror', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/youtube/playlists') return { playlists: [] };
      if (url === '/api/youtube/parse') return { url_hash: 'empty1', name: 'Empty YT', tracks: [] };
      return { success: true };
    };
    const { land } = renderTab(<YouTubeTab />);
    fireEvent.change(screen.getByPlaceholderText('Paste YouTube Music Playlist URL...'), {
      target: { value: 'https://youtube.com/playlist?list=E' },
    });
    fireEvent.click(screen.getByText('Parse Playlist'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Empty YT has no tracks to import', 'error'),
    );
    expect(mirrorBody()).toBeUndefined();
    expect(land).not.toHaveBeenCalled();
  });

  it('a parse error toasts and removes the temp card', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/youtube/playlists') return { playlists: [] };
      if (url === '/api/youtube/parse') return { error: 'Bad playlist' };
      return {};
    };
    renderTab(<YouTubeTab />);
    fireEvent.change(screen.getByPlaceholderText('Paste YouTube Music Playlist URL...'), {
      target: { value: 'https://youtube.com/playlist?list=BAD' },
    });
    fireEvent.click(screen.getByText('Parse Playlist'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Error parsing YouTube playlist: Bad playlist', 'error'),
    );
    expect(screen.queryByText('Parsing YouTube playlist...')).not.toBeInTheDocument();
    expect(screen.getByText('Parsed YouTube playlists will appear here.')).toBeInTheDocument();
  });
});

describe('UrlHistoryBar', () => {
  function BarHarness() {
    const ops = useUrlHistory('deezer');
    return (
      <div>
        <button type="button" onClick={() => ops.save('http://u1', 'Name One')}>
          save
        </button>
        <UrlHistoryBar
          containerId="deezer-url-history"
          icon="🎵"
          history={ops.history}
          onPick={() => undefined}
          onRemove={ops.remove}
        />
      </div>
    );
  }

  it('renders nothing when empty, pills after save, X removes', () => {
    render(<BarHarness />);
    expect(screen.queryByText('Recent')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('save'));
    expect(screen.getByText('Recent')).toBeInTheDocument();
    expect(screen.getByText('Name One')).toBeInTheDocument();
    fireEvent.click(screen.getByText('×'));
    expect(screen.queryByText('Recent')).not.toBeInTheDocument();
    expect(localStorage.getItem('soulsync-url-history-deezer')).toBe('[]');
  });

  it('truncates long pill names to 28 chars + ellipsis, keeps the full name as title', () => {
    const longName = 'A'.repeat(35);
    localStorage.setItem(
      'soulsync-url-history-deezer',
      JSON.stringify([{ url: 'http://long', name: longName, ts: 1 }]),
    );
    render(<BarHarness />);
    expect(screen.getByText('A'.repeat(28) + '...')).toBeInTheDocument();
    expect(screen.getByTitle(longName)).toBeInTheDocument();
  });
});
