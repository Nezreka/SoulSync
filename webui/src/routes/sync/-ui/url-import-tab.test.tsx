/**
 * The URL-import tab wave, rendered against a captured fetch and the REAL
 * useSourceVertical hook — parse → card → mirror → history → open, per tab.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { UrlTabPlaylist } from '../-sync.url-tabs';

import { forgetAccountPlaylists } from '../-sync.account-cache';
import { fetchYouTubePlaylists } from '../-sync.api';
import { SYNC_SOURCES } from '../-sync.sources';
import { useSourceVertical } from '../-sync.use-vertical';
import { UrlHistoryBar, useUrlHistory } from './url-history-bar';
import {
  DeezerLinkTab,
  ITunesLinkTab,
  SpotifyPublicTab,
  YouTubeTab,
  useUrlCardOpen,
  useYouTubeCardOpen,
} from './url-import-tab';

interface Call {
  url: string;
  method: string;
  body: unknown;
}

/** The playlist name renders on the CARD and on the history pill — scope to the card. */
function cardName(text: string): HTMLElement | undefined {
  return screen.getAllByText(text).find((el) => el.className === 'playlist-card-name');
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

beforeEach(() => {
  // The URL tabs remember their rows across mounts now, so this must be
  // cleared BEFORE each test as well as after: an afterEach alone still lets
  // the file's FIRST test start on rows left by whatever ran before it, which
  // showed up as an order-dependent failure rather than a consistent one.
  forgetAccountPlaylists();
});

afterEach(() => {
  forgetAccountPlaylists();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  delete (window as { showToast?: unknown }).showToast;
});

function Harness({
  Tab,
  onOpen,
}: {
  Tab: typeof DeezerLinkTab;
  onOpen?: (id: string, playlist: UrlTabPlaylist) => void;
}) {
  const vertical = useSourceVertical(SYNC_SOURCES.deezer);
  return <Tab vertical={vertical} onOpen={onOpen ?? (() => undefined)} />;
}

describe('DeezerLinkTab', () => {
  it('parse: GET by extracted id, card, mirror body, history, states hydration, success toast', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/deezer/playlist/123?async=1') {
        return { pending: true, job_id: 'job-123' };
      }
      if (url === '/api/deezer/playlist-load/job-123') {
        return {
          status: 'complete',
          playlist: {
            id: 123,
            name: 'Dz Mix',
            track_count: 2,
            owner: 'DzOwner',
            image_url: 'http://img',
            tracks: [
              { name: 'T1', artists: ['A1'], album: 'Al1', duration_ms: 10, id: 1 },
              { name: 'T2', artists: ['A2'], album: 'Al2', duration_ms: 20, id: 2 },
            ],
          },
        };
      }
      if (url === '/api/deezer/playlists/states') {
        return {
          states: [
            { playlist_id: '123', phase: 'discovered', spotify_matches: 1, spotify_total: 2 },
            { playlist_id: '999', phase: 'syncing' },
          ],
        };
      }
      return { success: true };
    };

    render(<Harness Tab={DeezerLinkTab} />);
    fireEvent.change(screen.getByPlaceholderText('Paste Deezer Playlist URL...'), {
      target: { value: 'https://www.deezer.com/en/playlist/123' },
    });
    fireEvent.click(screen.getByText('Load Playlist'));

    await waitFor(() => expect(cardName('Dz Mix')).toBeDefined());
    expect(screen.getByText('2 tracks')).toBeInTheDocument();
    // The state hydration painted the discovered phase + check-note spans.
    await waitFor(() => expect(screen.getByText('Discovery Complete')).toBeInTheDocument());
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    // check-note sources print no percentage — unifying the markup must not
    // have invented one.
    expect(document.querySelector('.pcc-pct')).toBeNull();

    const mirror = calls.find((c) => c.url === '/api/mirror-playlist');
    expect(mirror).toBeDefined();
    expect(mirror!.body).toMatchObject({
      source: 'deezer',
      source_playlist_id: '123',
      name: 'Dz Mix',
      description: 'https://www.deezer.com/en/playlist/123',
      owner: 'DzOwner',
      image_url: 'http://img',
    });
    expect((mirror!.body as { tracks: unknown[] }).tracks[0]).toEqual({
      track_name: 'T1',
      artist_name: 'A1',
      album_name: 'Al1',
      duration_ms: 10,
      image_url: null,
      source_track_id: 1,
      extra_data: null,
    });
    expect(localStorage.getItem('soulsync-url-history-deezer')).toContain('Dz Mix');
    expect(toast).toHaveBeenCalledWith('Deezer playlist loaded: Dz Mix (2 tracks)', 'success');
  });

  it('invalid input and duplicate load toast without fetching', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    render(<Harness Tab={DeezerLinkTab} />);
    fireEvent.click(screen.getByText('Load Playlist'));
    expect(toast).toHaveBeenCalledWith('Please paste a Deezer playlist URL', 'error');
    fireEvent.change(screen.getByPlaceholderText('Paste Deezer Playlist URL...'), {
      target: { value: 'garbage' },
    });
    fireEvent.click(screen.getByText('Load Playlist'));
    expect(toast).toHaveBeenCalledWith(
      'Invalid Deezer playlist URL. Expected format: deezer.com/playlist/{id}',
      'error',
    );
    expect(calls).toEqual([]);
  });

  it('a second paste of a loaded playlist toasts info, clears the input, fetches nothing', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/deezer/playlist/77?async=1') {
        return { pending: true, job_id: 'job-77' };
      }
      if (url === '/api/deezer/playlist-load/job-77') {
        return {
          status: 'complete',
          playlist: { id: 77, name: 'Once', track_count: 1, tracks: [{ name: 'T' }] },
        };
      }
      if (url === '/api/deezer/playlists/states') return { states: [] };
      return { success: true };
    };
    render(<Harness Tab={DeezerLinkTab} />);
    const input = screen.getByPlaceholderText('Paste Deezer Playlist URL...');
    fireEvent.change(input, { target: { value: '77' } });
    fireEvent.click(screen.getByText('Load Playlist'));
    await waitFor(() => expect(cardName('Once')).toBeDefined());

    const fetchesBefore = calls.length;
    fireEvent.change(input, { target: { value: 'https://deezer.com/playlist/77' } });
    fireEvent.click(screen.getByText('Load Playlist'));
    expect(toast).toHaveBeenCalledWith('This playlist is already loaded', 'info');
    expect((input as HTMLInputElement).value).toBe('');
    expect(calls.length).toBe(fetchesBefore);

    // The history pill runs the same guard (renderUrlHistory 8725-8727).
    const pill = screen
      .getAllByText('Once')
      .find((el) => el.className === 'url-history-pill-name')!;
    fireEvent.click(pill);
    expect(toast).toHaveBeenLastCalledWith('This playlist is already loaded', 'info');
    expect(calls.length).toBe(fetchesBefore);
  });

  it('a discovering row restored from the states list resumes its poller (3320-3326)', async () => {
    stubFetch();
    window.showToast = vi.fn() as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/deezer/playlist/5?async=1') {
        return { pending: true, job_id: 'job-5' };
      }
      if (url === '/api/deezer/playlist-load/job-5') {
        return {
          status: 'complete',
          playlist: { id: 5, name: 'Mid', track_count: 1, tracks: [{ name: 'T' }] },
        };
      }
      if (url === '/api/deezer/playlists/states') {
        return { states: [{ playlist_id: '5', phase: 'discovering', discovery_progress: 40 }] };
      }
      return { phase: 'discovering', progress: 40 };
    };
    render(<Harness Tab={DeezerLinkTab} />);
    fireEvent.change(screen.getByPlaceholderText('Paste Deezer Playlist URL...'), {
      target: { value: '5' },
    });
    fireEvent.click(screen.getByText('Load Playlist'));
    await waitFor(() => expect(cardName('Mid')).toBeDefined());
    // Discovery polling has NO immediate first tick — the vanilla's
    // startXDiscoveryPolling is setInterval-only (unlike the sync poll, which
    // ticks at once, 1105). So the first status call lands one full cadence
    // (1000ms) out and the default 1000ms waitFor races it under load.
    await waitFor(
      () => expect(calls.some((c) => c.url.includes('/api/deezer/discovery/status/5'))).toBe(true),
      { timeout: 4000 },
    );
  });

  it('backend error lands in the vanilla catch toast', async () => {
    calls = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        calls.push({ url, method: 'GET', body: undefined });
        return new Response(JSON.stringify({ error: 'Deezer said no' }), { status: 502 });
      }),
    );
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    render(<Harness Tab={DeezerLinkTab} />);
    fireEvent.change(screen.getByPlaceholderText('Paste Deezer Playlist URL...'), {
      target: { value: '55' },
    });
    fireEvent.click(screen.getByText('Load Playlist'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Error loading Deezer playlist: Deezer said no', 'error'),
    );
  });
});

function SpHarness({ Tab }: { Tab: typeof SpotifyPublicTab }) {
  const vertical = useSourceVertical(SYNC_SOURCES.spotify_public);
  return <Tab vertical={vertical} onOpen={() => undefined} />;
}

describe('SpotifyPublicTab', () => {
  it('parse POSTs {url}, renders the type badge, mirrors with joined artist names', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/spotify/parse-public') {
        return {
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
        };
      }
      if (url === '/api/spotify-public/playlists/states') return { states: [] };
      return { success: true };
    };

    render(<SpHarness Tab={SpotifyPublicTab} />);
    fireEvent.change(screen.getByPlaceholderText('Paste Spotify Playlist or Album URL...'), {
      target: { value: 'https://open.spotify.com/album/abc' },
    });
    fireEvent.click(screen.getByText('Load'));

    await waitFor(() => expect(cardName('Sp Album')).toBeDefined());
    expect(screen.getByText('Album')).toBeInTheDocument();
    expect(screen.getByText('💿')).toBeInTheDocument();

    const parse = calls.find((c) => c.url === '/api/spotify/parse-public');
    expect(parse!.method).toBe('POST');
    expect(parse!.body).toEqual({ url: 'https://open.spotify.com/album/abc' });
    const mirror = calls.find((c) => c.url === '/api/mirror-playlist');
    expect(mirror!.body).toMatchObject({
      source: 'spotify_public',
      source_playlist_id: 'h1',
      owner: 'Sp Artist',
      description: 'https://open.spotify.com/album/abc',
    });
    expect((mirror!.body as { tracks: { artist_name: string }[] }).tracks[0].artist_name).toBe(
      'A, B',
    );
    expect(toast).toHaveBeenCalledWith('Loaded: Sp Album (1 tracks)', 'success');
  });

  it('post-parse url_hash dedupe (6657-6661): same hash twice toasts info, no second card', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/spotify/parse-public') {
        return {
          url_hash: 'dup',
          id: 'same',
          url: 'https://open.spotify.com/playlist/same',
          type: 'playlist',
          name: 'Dup List',
          track_count: 0,
          tracks: [],
        };
      }
      if (url === '/api/spotify-public/playlists/states') return { states: [] };
      return { success: true };
    };
    render(<SpHarness Tab={SpotifyPublicTab} />);
    const input = screen.getByPlaceholderText('Paste Spotify Playlist or Album URL...');
    fireEvent.change(input, { target: { value: 'https://open.spotify.com/playlist/same' } });
    fireEvent.click(screen.getByText('Load'));
    await waitFor(() => expect(cardName('Dup List')).toBeDefined());
    // A DIFFERENT input url resolving to the same url_hash — the pre-parse
    // guard misses, the post-parse guard catches.
    fireEvent.change(input, { target: { value: 'spotify:playlist:same' } });
    fireEvent.click(screen.getByText('Load'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('This playlist is already loaded', 'info'),
    );
    expect(
      screen.getAllByText('Dup List').filter((el) => el.className === 'playlist-card-name'),
    ).toHaveLength(1);
  });

  it("a result.error answers with the vanilla's `Error: ...` toast and adds no card", async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    responder = () => ({ error: 'Could not parse' });
    render(<SpHarness Tab={SpotifyPublicTab} />);
    fireEvent.change(screen.getByPlaceholderText('Paste Spotify Playlist or Album URL...'), {
      target: { value: 'spotify:playlist:abc' },
    });
    fireEvent.click(screen.getByText('Load'));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Error: Could not parse', 'error'));
    expect(calls.some((c) => c.url === '/api/mirror-playlist')).toBe(false);
  });
});

function ItHarness({ Tab }: { Tab: typeof ITunesLinkTab }) {
  const vertical = useSourceVertical(SYNC_SOURCES.itunes_link);
  return <Tab vertical={vertical} onOpen={() => undefined} />;
}

describe('ITunesLinkTab', () => {
  it("keeps the 'a iTunes' toast and renders the Track badge in Apple pink", async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    render(<ItHarness Tab={ITunesLinkTab} />);
    fireEvent.click(screen.getByText('Load'));
    expect(toast).toHaveBeenCalledWith('Please enter a iTunes URL', 'error');

    responder = (url) => {
      if (url === '/api/itunes-link/parse') {
        return {
          url_hash: 'ih1',
          id: '42',
          type: 'track',
          url: 'https://music.apple.com/us/song/x/42',
          name: 'One Song',
          track_count: 1,
          tracks: [{ name: 'One Song', artists: [{ name: 'A' }] }],
        };
      }
      if (url === '/api/itunes-link/playlists/states') return { states: [] };
      return { success: true };
    };
    fireEvent.change(
      screen.getByPlaceholderText('Paste iTunes or Apple Music Album/Track URL...'),
      { target: { value: 'https://music.apple.com/us/song/x/42' } },
    );
    fireEvent.click(screen.getByText('Load'));
    await waitFor(() => expect(cardName('One Song')).toBeDefined());
    const badge = screen.getByText('Track');
    expect(badge).toHaveStyle({ color: '#fa586a' });
    const mirror = calls.find((c) => c.url === '/api/mirror-playlist');
    expect(mirror!.body).toMatchObject({ source: 'itunes_link', source_playlist_id: 'ih1' });
  });
});

function YtHarness({ onOpen }: { onOpen?: (id: string, playlist: UrlTabPlaylist) => void }) {
  const vertical = useSourceVertical(SYNC_SOURCES.youtube);
  return <YouTubeTab vertical={vertical} onOpen={onOpen ?? (() => undefined)} />;
}

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
  it('restores stored playlists from the backend on mount', async () => {
    stubFetch();
    responder = (url) => {
      if (url === '/api/youtube/playlists') {
        return {
          playlists: [
            {
              url_hash: 'yh1',
              phase: 'discovered',
              spotify_matches: 3,
              spotify_total: 5,
              playlist: { name: 'Stored YT', tracks: [{}, {}, {}, {}, {}] },
            },
          ],
        };
      }
      if (url === '/api/youtube/state/yh1') {
        return { discovery_results: [], sync_progress: {} };
      }
      return {};
    };
    render(<YtHarness />);
    await waitFor(() => expect(screen.getByText('Stored YT')).toBeInTheDocument());
    expect(screen.getByText('5 tracks')).toBeInTheDocument();
    // The YT drift map: discovered says 'View Details', progress hidden.
    expect(screen.getByText('View Details')).toBeInTheDocument();
    expect(calls.some((c) => c.url === '/api/youtube/state/yh1')).toBe(true);
  });

  it('parse shows the temp card, then the real card with the YT button map + unconditional mirror', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
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
        if (url === '/api/youtube/playlists')
          return new Response(JSON.stringify({ playlists: [] }));
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
    calls = [];

    render(<YtHarness />);
    fireEvent.change(screen.getByPlaceholderText('Paste YouTube Music Playlist URL...'), {
      target: { value: 'https://youtube.com/playlist?list=NEW' },
    });
    fireEvent.click(screen.getByText('Parse Playlist'));

    // The temp card is the parse feedback (createYouTubeCard 8879).
    await waitFor(() =>
      expect(screen.getByText('Parsing YouTube playlist...')).toBeInTheDocument(),
    );
    expect(screen.getByText('Parsing...')).toBeDisabled();

    releaseParse(undefined);
    await waitFor(() => expect(cardName('Fresh YT')).toBeDefined());
    expect(screen.queryByText('Parsing YouTube playlist...')).not.toBeInTheDocument();
    expect(screen.getByText('Start Discovery')).toBeInTheDocument();

    const mirror = calls.find((c) => c.url === '/api/mirror-playlist');
    expect(mirror!.body).toMatchObject({ source: 'youtube', source_playlist_id: 'newhash' });
    expect(
      (mirror!.body as { tracks: { album_name: string; artist_name: string }[] }).tracks[0],
    ).toMatchObject({ track_name: 'V1', artist_name: 'A1', album_name: '' });
    expect(toast).toHaveBeenCalledWith('YouTube playlist parsed: Fresh YT (1 tracks)', 'success');
  });

  it('two URL variants resolving to ONE url_hash produce ONE card', async () => {
    stubFetch();
    window.showToast = vi.fn() as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/youtube/playlists') return { playlists: [] };
      if (url === '/api/youtube/parse') {
        // The backend canonicalises both youtube.com and music.youtube.com
        // to the same hash.
        return { url_hash: 'samehash', name: 'One List', tracks: [{ title: 'T' }] };
      }
      return { success: true };
    };
    render(<YtHarness />);
    const input = screen.getByPlaceholderText('Paste YouTube Music Playlist URL...');
    fireEvent.change(input, { target: { value: 'https://youtube.com/playlist?list=A' } });
    fireEvent.click(screen.getByText('Parse Playlist'));
    await waitFor(() => expect(cardName('One List')).toBeDefined());
    fireEvent.change(input, { target: { value: 'https://music.youtube.com/playlist?list=A' } });
    fireEvent.click(screen.getByText('Parse Playlist'));
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/youtube/parse')).toHaveLength(2),
    );
    expect(
      screen.getAllByText('One List').filter((el) => el.className === 'playlist-card-name'),
    ).toHaveLength(1);
  });

  it('YouTube mirrors even a ZERO-track playlist — the drift at 8861 (no length guard)', async () => {
    stubFetch();
    window.showToast = vi.fn() as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/youtube/playlists') return { playlists: [] };
      if (url === '/api/youtube/parse') return { url_hash: 'empty1', name: 'Empty YT', tracks: [] };
      return { success: true };
    };
    render(<YtHarness />);
    fireEvent.change(screen.getByPlaceholderText('Paste YouTube Music Playlist URL...'), {
      target: { value: 'https://youtube.com/playlist?list=E' },
    });
    fireEvent.click(screen.getByText('Parse Playlist'));
    await waitFor(() => expect(cardName('Empty YT')).toBeDefined());
    // deezer/spotify-public/itunes all gate on tracks.length > 0; YouTube does not.
    const mirror = calls.find((c) => c.url === '/api/mirror-playlist');
    expect(mirror).toBeDefined();
    expect((mirror!.body as { tracks: unknown[] }).tracks).toEqual([]);
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
    render(<YtHarness />);
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

describe('useUrlCardOpen / useYouTubeCardOpen', () => {
  function OpenHarness({ yt }: { yt: boolean }) {
    const vertical = useSourceVertical(yt ? SYNC_SOURCES.youtube : SYNC_SOURCES.deezer);
    const [openId, setOpenId] = useState<string | null>(null);
    const openShared = useUrlCardOpen(SYNC_SOURCES.deezer, vertical, setOpenId);
    const openYt = useYouTubeCardOpen(vertical, setOpenId);
    return (
      <div>
        <button
          type="button"
          onClick={() => void (yt ? openYt : openShared)('id1', { name: 'P', tracks: [] })}
        >
          open
        </button>
        <span data-testid="open-id">{openId ?? 'none'}</span>
        <span data-testid="phase">{vertical.states.id1?.phase ?? 'unseeded'}</span>
      </div>
    );
  }

  it('shared open seeds fresh and opens without starting discovery', async () => {
    stubFetch();
    render(<OpenHarness yt={false} />);
    fireEvent.click(screen.getByText('open'));
    await waitFor(() => expect(screen.getByTestId('open-id')).toHaveTextContent('id1'));
    expect(screen.getByTestId('phase')).toHaveTextContent('fresh');
    expect(calls).toEqual([]);
  });

  it('a discovered card with no results refetches the full state before opening (2862-2878)', async () => {
    stubFetch();
    responder = (url) => {
      if (url.includes('/api/deezer/state/')) {
        return { phase: 'discovered', discovery_results: [{ spotify_data: { name: 'M' } }] };
      }
      return {};
    };
    function RefetchHarness() {
      const vertical = useSourceVertical(SYNC_SOURCES.deezer);
      const [openId, setOpenId] = useState<string | null>(null);
      const open = useUrlCardOpen(SYNC_SOURCES.deezer, vertical, setOpenId);
      return (
        <div>
          <button
            type="button"
            onClick={() => vertical.hydrate('d9', { phase: 'discovered', playlist: { name: 'P' } })}
          >
            hydrate
          </button>
          <button type="button" onClick={() => void open('d9', { name: 'P' })}>
            open
          </button>
          <span data-testid="open-id">{openId ?? 'none'}</span>
          <span data-testid="rows">{vertical.states.d9?.rawResults.length ?? -1}</span>
        </div>
      );
    }
    render(<RefetchHarness />);
    fireEvent.click(screen.getByText('hydrate'));
    fireEvent.click(screen.getByText('open'));
    await waitFor(() => expect(screen.getByTestId('open-id')).toHaveTextContent('d9'));
    expect(calls.some((c) => c.url.includes('/api/deezer/state/d9'))).toBe(true);
    await waitFor(() => expect(screen.getByTestId('rows')).toHaveTextContent('1'));
  });

  it('the YouTube fresh click STARTS discovery immediately (9047-9054)', async () => {
    stubFetch();
    responder = () => ({ success: true });
    render(<OpenHarness yt={true} />);
    fireEvent.click(screen.getByText('open'));
    await waitFor(() => expect(screen.getByTestId('open-id')).toHaveTextContent('id1'));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/api/youtube/discovery/start/id1'))).toBe(true),
    );
    expect(screen.getByTestId('phase')).toHaveTextContent('discovering');
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
