import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { LibrarySong } from '../-library-v2.songs';

/**
 * #1906: search your library by song title. Through the real router, like the
 * page's other tests: the view lives in the URL. Library v2: the Songs section
 * of the catalogue page (`?section=songs`), catalogue ids, and a removal goes
 * through the journaled album-scoped file delete.
 */

function song(over: Partial<LibrarySong> & { id: number | string }): LibrarySong {
  const trackId = Number.parseInt(String(over.id), 10);
  return {
    track_id: trackId,
    file_ids: [trackId * 10],
    title: `Song ${over.id}`,
    artist_name: 'Morgan Wallen',
    artist_id: 1,
    album_id: trackId,
    album_title: 'Dangerous',
    album_thumb_url: null,
    year: 2021,
    track_number: trackId,
    disc_number: 1,
    duration: 157000,
    bitrate: 900,
    format: 'FLAC',
    file_path: `/m/${over.id}.flac`,
    play_count: 0,
    copies: 1,
    ...over,
  };
}

const PROOF = [
  song({ id: 1, title: 'You Proof', album_title: 'Dangerous', copies: 2 }),
  song({
    id: 2,
    title: 'You Proof',
    album_title: 'Dangerous: The Double Album',
    copies: 2,
    format: 'MP3',
    bitrate: 320,
  }),
];

let requested: { url: string; method: string; body?: unknown }[] = [];
let canWrite = true;
let songs: LibrarySong[] = PROOF;
let songTotal = 2;
let capped = false;
let artistRows: { id: number; name: string }[] = [{ id: 1, name: 'Morgan Wallen' }];
let copiesRows: LibrarySong[] = [];
/** a search whose request never answers, to see the list while it loads */
let holdSearch = '';

function stubFetch() {
  requested = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) || 'GET';
      const sent = input instanceof Request && method !== 'GET' ? await input.clone().text() : '';
      requested.push({ url, method, ...(sent ? { body: JSON.parse(sent) } : {}) });
      let body: unknown = {};
      if (holdSearch && url.includes(`search=${holdSearch}`)) {
        return new Promise<Response>(() => {});
      }
      if (url.includes('/copies')) {
        body = { success: true, copies: copiesRows };
      } else if (url.includes('/api/user-playlists/liked')) {
        body = { success: true, id: 1, added: 1, removed: 1, track_count: 1 };
      } else if (url.includes('/api/library/v2/enabled')) {
        body = { success: true, enabled: true, can_write: canWrite };
      } else if (url.includes('/api/library/v2/import/status')) {
        body = { running: false, stage: 'idle', artwork_cache: { running: false } };
      } else if (url.includes('/api/library/v2/mirror-status')) {
        body = { success: true, pending: 0, failed: 0 };
      } else if (url.includes('/file-delete-preview')) {
        body = { success: true, preview_token: 'tok', unsafe_count: 0, files: [] };
      } else if (url.includes('/file-delete') || url.includes('/file-remove')) {
        body = { success: true, operation: { id: 'op', status: 'completed', items: [] } };
      } else if (url.includes('/api/library/v2/songs')) {
        const page = Number(new URL(url, 'http://x').searchParams.get('page') ?? 1);
        body = {
          success: true,
          songs,
          pagination: {
            page,
            limit: 75,
            total_count: songTotal,
            total_capped: capped,
            total_pages: Math.max(1, Math.ceil(songTotal / 75)),
            has_prev: page > 1,
            has_next: false,
          },
        };
      } else if (url.includes('/api/library/v2/artists')) {
        body = {
          success: true,
          artists: artistRows,
          pagination: {
            page: 1,
            limit: 75,
            total_count: artistRows.length,
            total_pages: 1,
            has_prev: false,
            has_next: false,
          },
        };
      } else if (url.includes('/api/library/unmatched-summary')) {
        body = { success: true, count: 0, artist_id: null };
      }
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

function renderPage(entry: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return { router, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

const songCalls = () => requested.filter((r) => r.url.includes('/api/library/v2/songs'));

beforeEach(() => {
  songs = PROOF;
  songTotal = 2;
  capped = false;
  artistRows = [{ id: 1, name: 'Morgan Wallen' }];
  copiesRows = [];
  holdSearch = '';
  canWrite = true;
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showLibraryDownloadsSection = vi.fn();
  window.showToast = vi.fn();
  window.currentMusicSourceName = 'spotify';
  stubFetch();
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
  delete window.showLibraryDownloadsSection;
});

describe('the Songs view', () => {
  it('lists songs with their album, so two copies read as two copies', async () => {
    renderPage('/library?section=songs&q=you%20proof');
    const rows = await screen.findAllByText('You Proof');
    expect(rows).toHaveLength(2);
    expect(screen.getByText('Dangerous')).toBeTruthy();
    expect(screen.getByText('Dangerous: The Double Album')).toBeTruthy();
    expect(screen.getAllByText('2 copies')).toHaveLength(2);
    expect(screen.getByText('FLAC 900k')).toBeTruthy();
    const params = new URL(songCalls().at(-1)!.url, 'http://x').searchParams;
    expect(params.get('search')).toBe('you proof');
    expect(screen.getByText('2 matches')).toBeTruthy();
  });

  it('plays the song and the list after it, from its file', async () => {
    const playTrackList = vi.fn(async () => ({}));
    window.playTrackList = playTrackList as never;
    renderPage('/library?section=songs');
    await screen.findAllByText('You Proof');
    fireEvent.click(screen.getAllByLabelText('Play You Proof')[0]);
    const [queue, context] = playTrackList.mock.calls[0] as unknown as [
      Record<string, unknown>[],
      string,
    ];
    // the catalogue id rides typed; `id` is the media server's, never a v2 id
    expect(queue.map((t) => t.lib2_track_id)).toEqual([1, 2]);
    expect(queue[0]).toMatchObject({ id: null, file_path: '/m/1.flac', is_library: true });
    expect(context).toBe('Your songs');
    delete window.playTrackList;
  });

  it('a capped count reads as "N+"', async () => {
    songs = [song({ id: 1, title: 'Love Story' })];
    songTotal = 1000;
    capped = true;
    renderPage('/library?section=songs&q=love');
    // the search's hit is marked, so the title is split around it
    await screen.findByRole('mark');
    expect(screen.getByText('1,000+ matches')).toBeTruthy();
  });

  it('an admin can delete a song, file and all, through the delete dialog', async () => {
    renderPage('/library?section=songs');
    await screen.findAllByText('You Proof');
    fireEvent.click(screen.getAllByLabelText('More for You Proof')[0]);
    fireEvent.click(await screen.findByText('Delete…'));
    fireEvent.click(await screen.findByText('Delete File Too'));
    // previewed, then deleted with that preview's token: the song's own file
    await waitFor(() =>
      expect(requested).toContainEqual({
        url: expect.stringContaining('/api/library/v2/albums/1/file-delete'),
        method: 'POST',
        body: { preview_token: 'tok', file_ids: [10] },
      }),
    );
    expect(
      requested.some((r) =>
        r.url.includes('/api/library/v2/albums/1/file-delete-preview?file_ids=10'),
      ),
    ).toBe(true);
    // the list refetches so the row is gone
    await waitFor(() => expect(songCalls().length).toBeGreaterThan(1));
  });

  it('everyone else gets play, playlist and open, but no delete', async () => {
    canWrite = false;
    renderPage('/library?section=songs');
    await screen.findAllByText('You Proof');
    fireEvent.click(screen.getAllByLabelText('More for You Proof')[0]);
    expect(await screen.findByText('Open album')).toBeTruthy();
    expect(screen.queryByText('Delete…')).toBeNull();
    expect(screen.getAllByLabelText('Add You Proof to a playlist').length).toBe(2);
  });
});

describe('a library search finds songs from any view', () => {
  it('shows the best song matches above the artists, and goes to all of them', async () => {
    songTotal = 12;
    const { router } = renderPage('/library?q=you%20proof');
    const strip = await screen.findByRole('region', { name: 'Songs that match' });
    expect(within(strip).getAllByText('You Proof')).toHaveLength(2);
    // the strip asks for a few, regardless of the view's letter or page
    const params = new URL(songCalls()[0].url, 'http://x').searchParams;
    expect(params.get('limit')).toBe('5');
    fireEvent.click(within(strip).getByText('See all 12 songs →'));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ section: 'songs', q: 'you proof' }),
    );
  });

  it('found songs but no artists is not "not in your library"', async () => {
    artistRows = [];
    renderPage('/library?q=you%20proof');
    await screen.findByRole('region', { name: 'Songs that match' });
    expect(await screen.findByText('No artists match this view')).toBeTruthy();
    expect(await screen.findByText('The songs above match.')).toBeTruthy();
  });

  it('no strip for a one-letter search, or in the Songs view itself', async () => {
    renderPage('/library?q=y');
    await screen.findByText('Morgan Wallen');
    expect(screen.queryByRole('region', { name: 'Songs that match' })).toBeNull();
    expect(songCalls()).toHaveLength(0);
  });
});

const lastSongsParams = () => new URL(songCalls().at(-1)!.url, 'http://x').searchParams;
const THREE = [
  song({ id: 1, title: 'Hangin On' }),
  song({ id: 2, title: 'You Proof' }),
  song({ id: 3, title: 'Sand in My Boots' }),
];
const row = (id: number) => document.querySelector(`[data-song-id="${id}"]`) as HTMLElement;

describe('best in class: playing', () => {
  it('a click on a row plays it and the rest of the list after it', async () => {
    songs = THREE;
    const playTrackList = vi.fn(async () => ({}));
    window.playTrackList = playTrackList as never;
    renderPage('/library?section=songs');
    await screen.findByText('You Proof');
    fireEvent.click(row(2));
    const queue = (playTrackList.mock.calls[0] as unknown as [{ lib2_track_id: number }[]])[0];
    expect(queue.map((t) => t.lib2_track_id)).toEqual([2, 3]);
    delete window.playTrackList;
  });

  it('Play all queues the whole view, up to 500, in its order', async () => {
    songs = THREE;
    const playTrackList = vi.fn(async () => ({}));
    window.playTrackList = playTrackList as never;
    renderPage('/library?section=songs&songSort=artist');
    await screen.findByText('You Proof');
    fireEvent.click(screen.getByText('Play all'));
    await waitFor(() => expect(playTrackList).toHaveBeenCalled());
    expect(lastSongsParams().get('limit')).toBe('500');
    expect(lastSongsParams().get('sort')).toBe('artist');
    const [queue, context] = playTrackList.mock.calls[0] as unknown as [
      { lib2_track_id: number }[],
      string,
    ];
    expect(queue.map((t) => t.lib2_track_id)).toEqual([1, 2, 3]);
    expect(context).toBe('Your songs');
    delete window.playTrackList;
  });

  it('the playing song wears moving bars, still ones when paused', async () => {
    songs = THREE;
    renderPage('/library?section=songs');
    await screen.findByText('You Proof');
    // the player announces the queue row: its catalogue id, a null server id
    fireEvent(
      window,
      new CustomEvent('ss:now-playing', { detail: { id: null, lib2_track_id: 2 } }),
    );
    await waitFor(() => expect(row(2).hasAttribute('data-playing')).toBe(true));
    expect(row(1).hasAttribute('data-playing')).toBe(false);
    fireEvent(window, new CustomEvent('ss:now-playing', { detail: null }));
    await waitFor(() => expect(row(2).hasAttribute('data-playing')).toBe(false));
  });
});

describe('best in class: choosing songs', () => {
  it('ticking a song turns a row click into a tick, and shift ticks a range', async () => {
    songs = THREE;
    const playTrackList = vi.fn(async () => ({}));
    window.playTrackList = playTrackList as never;
    renderPage('/library?section=songs');
    await screen.findByText('You Proof');
    fireEvent.click(screen.getByLabelText('Select Hangin On'));
    expect(screen.getByText('1 selected')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Select Sand in My Boots'), { shiftKey: true });
    expect(screen.getByText('3 selected')).toBeTruthy();
    fireEvent.click(row(2));
    expect(screen.getByText('2 selected')).toBeTruthy();
    expect(playTrackList).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Clear'));
    expect(screen.queryByText(/selected$/)).toBeNull();
    delete window.playTrackList;
  });

  it('the heart likes a song, and says so', async () => {
    songs = THREE;
    renderPage('/library?section=songs');
    await screen.findByText('You Proof');
    const heart = screen.getByLabelText('Like You Proof');
    fireEvent.click(heart);
    await waitFor(() =>
      expect(requested).toContainEqual({
        url: expect.stringContaining('/api/user-playlists/liked'),
        method: 'POST',
      }),
    );
    expect(screen.getByLabelText('Unlike You Proof').getAttribute('aria-pressed')).toBe('true');
  });

  it('a liked song reads as liked', async () => {
    songs = [song({ id: 1, title: 'Hangin On', liked: true })];
    renderPage('/library?section=songs');
    expect(await screen.findByLabelText('Unlike Hangin On')).toBeTruthy();
  });
});

describe('best in class: filters, sorts, searching', () => {
  it('the filters and sorts go to the server and live in the url', async () => {
    const { router } = renderPage('/library?section=songs');
    await screen.findAllByText('You Proof');
    fireEvent.click(screen.getByText('Duplicates'));
    await waitFor(() => expect(lastSongsParams().get('dupes')).toBe('1'));
    fireEvent.click(screen.getByText('Lossless'));
    await waitFor(() => expect(lastSongsParams().get('quality')).toBe('lossless'));
    fireEvent.change(screen.getByLabelText('Sort songs'), { target: { value: 'year' } });
    await waitFor(() => expect(lastSongsParams().get('sort')).toBe('year'));
    expect(router.state.location.search).toMatchObject({ dupes: true, quality: 'lossless' });
    // leaving songs drops the song-only filters
    fireEvent.click(screen.getByText('Artists'));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ dupes: false, quality: '' }),
    );
  });

  it('a new search keeps the last results on screen, dimmed, until it answers', async () => {
    const { router } = renderPage('/library?section=songs&q=proof');
    await screen.findAllByRole('mark');
    holdSearch = 'pending';
    void router.navigate({ to: '/library', search: { section: 'songs', q: 'pending' } });
    await waitFor(() =>
      expect(document.querySelector('#library-songs-list')?.hasAttribute('data-stale')).toBe(true),
    );
    expect(document.querySelectorAll('#library-songs-list li')).toHaveLength(2);
    // and the songs view never waits on an artists search it doesn't show
    expect(requested.some((r) => r.url.includes('/api/library/v2/artists'))).toBe(false);
  });
});

describe('best in class: copies', () => {
  it('compares the copies, best file first, and keeps the one you pick', async () => {
    // one row per file: `<track>:<file>`
    copiesRows = [
      song({ id: '1:10', title: 'You Proof', album_title: 'Dangerous', format: 'FLAC', copies: 2 }),
      song({
        id: '2:20',
        title: 'You Proof',
        album_title: 'Double Album',
        format: 'MP3',
        copies: 2,
      }),
    ];
    renderPage('/library?section=songs');
    await screen.findAllByText('You Proof');
    fireEvent.click(screen.getAllByText('2 copies')[0]);
    const dialog = await screen.findByRole('dialog', { name: 'Copies of You Proof' });
    await within(dialog).findByText('Best file');
    const rows = within(dialog).getAllByRole('listitem');
    expect(rows[0].textContent).toContain('Dangerous');
    expect(rows[0].textContent).toContain('Best file');
    expect(rows[1].textContent).not.toContain('Best file');
    // keep the flac: the mp3 goes, from the library only
    fireEvent.click(within(dialog).getAllByText('Keep this one')[0]);
    fireEvent.click(await screen.findByText('Remove from Library'));
    await waitFor(() =>
      expect(requested).toContainEqual({
        url: expect.stringContaining('/api/library/v2/albums/2/file-remove'),
        method: 'POST',
        body: { file_ids: [20] },
      }),
    );
    expect(requested.some((r) => r.url.includes('/api/library/v2/albums/1/file-'))).toBe(false);
  });
});
