import { createMemoryHistory } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { VideoMovieItem, VideoShowItem } from './-video.types';

/**
 * The video wishlist tabs, end to end through the real route: the router, the
 * guard, the queries, the page, the panels. The server is stubbed at fetch, and
 * every write is checked for the exact endpoint and body the vanilla page sent.
 */

const res = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const snapshotRefused = {
  chain: ['torrent'],
  sources: {
    torrent: {
      ran: true,
      results: 3,
      accepted: 0,
      samples: [
        { title: 'Dune 2021 720p', accepted: false, rejected: "720p isn't in your enabled tiers" },
      ],
    },
  },
};

const MOVIES: VideoMovieItem[] = [
  {
    kind: 'movie',
    tmdb_id: 438631,
    title: 'Dune',
    year: 2021,
    poster_url: '/api/video/poster/movie/9',
    status: 'wanted',
    library_id: null,
    search_attempts: 7,
    last_search_at: '2026-10-19 08:00:00',
    last_refusal: "Best found: 720p — 720p isn't in your enabled tiers (3 releases)",
    search_snapshot: snapshotRefused,
    trouble: 'refused',
  },
  {
    kind: 'movie',
    tmdb_id: 1001,
    title: 'Upcoming Film',
    year: 2027,
    poster_url: null,
    status: 'monitored',
    search_attempts: 0,
    trouble: null,
  },
  {
    kind: 'movie',
    tmdb_id: 1002,
    title: 'Ordinary Want',
    year: 1999,
    poster_url: 'https://image.tmdb.org/t/p/original/x.jpg',
    status: 'wanted',
    search_attempts: 1,
    trouble: null,
  },
];

const SHOWS: VideoShowItem[] = [
  {
    kind: 'show',
    tmdb_id: 1399,
    title: 'Shogun',
    poster_url: '/api/video/poster/show/3',
    library_id: 77,
    wanted: 3,
    done: 0,
    muted: false,
    seasons: [
      {
        season_number: 1,
        poster_url: null,
        episodes: [
          {
            episode_number: 1,
            title: 'Anjin',
            air_date: '2024-02-27',
            status: 'wanted',
            search_attempts: 12,
            last_search_at: '2026-10-18 10:00:00',
            last_refusal: 'Nothing found for this search',
            trouble: 'notfound',
          },
          {
            episode_number: 2,
            title: 'Servants of Two Masters',
            status: 'wanted',
            search_attempts: 0,
          },
          {
            episode_number: 3,
            title: 'Tomorrow is Tomorrow',
            status: 'monitored',
            search_attempts: 0,
          },
        ],
      },
    ],
  },
  {
    kind: 'show',
    tmdb_id: 2002,
    title: 'Calm Show',
    poster_url: null,
    wanted: 1,
    done: 0,
    seasons: [{ season_number: 2, episodes: [{ episode_number: 5, status: 'wanted' }] }],
  },
];

const CHANNELS: VideoShowItem[] = [
  {
    kind: 'channel',
    source: 'youtube',
    tmdb_id: -5,
    youtube_id: 'UCabc',
    title: 'Some Channel',
    poster_url: 'https://yt3.ggpht.com/avatar',
    wanted: 2,
    done: 0,
    seasons: [
      {
        season_number: 2025,
        year: 2025,
        poster_url: 'https://i.ytimg.com/vi/v1/hq.jpg',
        episodes: [
          {
            episode_number: 1,
            title: 'Gone Video',
            source_id: 'v1',
            air_date: '2025-03-01',
            status: 'wanted',
            unavailable: true,
            search_attempts: 2,
            last_refusal: 'Private video',
          },
          {
            episode_number: 2,
            title: 'Fine Video',
            source_id: 'v2',
            air_date: '2025-01-10',
            status: 'wanted',
          },
        ],
      },
    ],
  },
];

interface Call {
  method: string;
  path: string;
  params: Record<string, string>;
  body: unknown;
}

let calls: Call[] = [];
let lists: { movie: unknown; show: unknown; youtube: unknown };
let pagination: {
  page: number;
  total_pages: number;
  total_count: number;
  has_prev: boolean;
  has_next: boolean;
} | null = null;

function stubServer() {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const req = input instanceof Request ? input : new Request(String(input));
      const url = new URL(req.url);
      const path = url.pathname.replace(/^\/api\//, '');
      let body: unknown = null;
      if (req.method !== 'GET') {
        try {
          body = await req.clone().json();
        } catch {
          body = null;
        }
      }
      calls.push({ method: req.method, path, params: Object.fromEntries(url.searchParams), body });

      if (path === 'video/wishlist/counts')
        return res({
          success: true,
          movie: 3,
          show: 2,
          episode: 4,
          total: 9,
          channel: 1,
          video: 2,
        });
      if (path === 'video/wishlist' && req.method === 'GET') {
        const kind = url.searchParams.get('kind') as 'movie' | 'show';
        const items = lists[kind] as unknown[];
        return res({
          success: true,
          kind,
          counts: { movie: 3, show: 2, episode: 4, total: 7 },
          items,
          pagination: pagination ?? {
            page: 1,
            total_pages: 1,
            total_count: items.length,
            has_prev: false,
            has_next: false,
          },
        });
      }
      if (path === 'video/youtube/wishlist')
        return res({
          success: true,
          counts: { channel: 1, video: 2 },
          items: lists.youtube,
          pagination: { page: 1, total_pages: 1, total_count: 1, has_prev: false, has_next: false },
        });
      if (path === 'video/wishlist/search') return res({ success: true, queued: 1, total: 1 });
      if (path === 'video/wishlist/retry') return res({ success: true, queued: 1, reset: 1 });
      if (path === 'video/wishlist/search-all')
        return res({ success: true, kinds: { movie: 'started', episode: 'started' } });
      if (path === 'video/wishlist/youtube/download-all')
        return res({ success: true, queued: 2, started: 1 });
      if (path === 'video/youtube/download') return res({ success: true });
      if (path === 'video/wishlist/remove') return res({ success: true });
      if (path === 'video/youtube/wishlist/remove') return res({ success: true });
      if (path === 'video/youtube/unfollow') return res({ success: true });
      if (path === 'video/wishlist/clear') return res({ success: true, removed: 3 });
      if (path.startsWith('video/wishlist/backfill')) return res({ success: true, updated: 0 });
      if (path === 'video/wishlist/diagnostics')
        return res({
          success: true,
          row: { search_attempts: 12 },
          ids: { tmdb_id: 1399 },
          downloads: [],
        });
      if (path.startsWith('video/detail/show/') || path.startsWith('video/tmdb/show/'))
        return res({
          overview: 'A shipwrecked sailor.',
          cast: [{ tmdb_id: 9, name: 'Hiroyuki Sanada' }],
        });
      if (path.startsWith('video/episode/'))
        return res({ guest_stars: [{ tmdb_id: 11, name: 'Guest Star' }] });
      if (path.startsWith('video/youtube/video/'))
        return res({
          video: { description: 'A video about things.', duration_seconds: 125, view_count: 1500 },
        });
      if (path === 'audiobook-routes/check') return res({ success: true, routed: [] });
      if (path === 'active-processes') return res({ active_processes: [] });
      return res({});
    }),
  );
}

/** wait for an element to exist, then hand it over. */
async function findEl(selector: string): Promise<Element> {
  return waitFor(() => {
    const el = document.querySelector(selector);
    if (!el) throw new Error(`not yet: ${selector}`);
    return el;
  });
}

const posts = (path: string) => calls.filter((c) => c.method === 'POST' && c.path === path);
const gets = (path: string) => calls.filter((c) => c.method === 'GET' && c.path === path);

function renderRoute(entry: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  render(<AppRouterProvider router={router} queryClient={queryClient} />);
  return { router, history };
}

beforeEach(() => {
  lists = { movie: MOVIES, show: SHOWS, youtube: CHANNELS };
  pagination = null;
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showToast = vi.fn();
  window.showConfirmDialog = vi.fn(async () => true);
  window.SoulSyncVideo = { openDetail: vi.fn() };
  window.VideoDownload = { manualSearch: vi.fn() };
  window.sessionStorage.clear();
  stubServer();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete window.SoulSyncWebShellBridge;
});

describe('movies', () => {
  it('lists posters in zones by what is wrong, worst first', async () => {
    renderRoute('/wishlist?media=movies');
    await waitFor(() => expect(document.querySelectorAll('.wlp-zone')).toHaveLength(3));
    expect(
      [...document.querySelectorAll('.wlp-zone')].map((z) => z.getAttribute('aria-label')),
    ).toEqual(['Found, but turned down', 'Still hunting', 'Not out yet']);
    // tab counts come from /wishlist/counts
    const tabs = screen.getByRole('tablist', { name: 'Video wishlist' });
    expect(within(tabs).getByRole('tab', { name: /Movies\s*3/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(within(tabs).getByRole('tab', { name: /YouTube\s*2/ })).toBeTruthy();
    // the list was asked for the movie kind, 60 a page, newest first
    expect(gets('video/wishlist')[0].params).toMatchObject({
      kind: 'movie',
      sort: 'added',
      page: '1',
      limit: '60',
    });
    // posters are sized, never raw
    const dune = document.querySelector('[data-tmdb="438631"] img') as HTMLImageElement;
    expect(dune.getAttribute('src')).toBe('/api/video/poster/movie/9?w=342');
  });

  it('says what is stuck in the banner and opens the panel with the reason', async () => {
    renderRoute('/wishlist?media=movies');
    expect(await screen.findByRole('alert')).toHaveTextContent('1 item is stuck.');
    fireEvent.click(document.querySelector('[data-tmdb="438631"]')!);
    const panel = screen.getByRole('complementary', { name: 'Dune on the wishlist' });
    expect(within(panel).getByRole('note')).toHaveTextContent('Found, but turned down');
    expect(within(panel).getByRole('note')).toHaveTextContent("720p isn't in your enabled tiers");
    expect(document.querySelector('.wl-nebula.has-sheet')).toBeTruthy();
  });

  it('every movie action sends what the old page sent', async () => {
    renderRoute('/wishlist?media=movies');
    fireEvent.click(await findEl('[data-tmdb="438631"]'));
    const panel = screen.getByRole('complementary', { name: 'Dune on the wishlist' });

    fireEvent.click(within(panel).getByRole('button', { name: 'Search now' }));
    await waitFor(() => expect(posts('video/wishlist/search')).toHaveLength(1));
    expect(posts('video/wishlist/search')[0].body).toEqual({ scope: 'movie', tmdb_id: 438631 });
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Searching for 1 item… grabs land in Downloads',
        'success',
      ),
    );
    // a queued search keeps its button down until the list reloads
    expect(within(panel).getByRole('button', { name: 'Search now' })).toBeDisabled();

    fireEvent.click(within(panel).getByRole('button', { name: 'Retry with all sources' }));
    await waitFor(() => expect(posts('video/wishlist/retry')).toHaveLength(1));
    expect(posts('video/wishlist/retry')[0].body).toEqual({ scope: 'movie', tmdb_id: 438631 });

    fireEvent.click(within(panel).getByRole('button', { name: 'Pick a release' }));
    expect(window.VideoDownload!.manualSearch).toHaveBeenCalledWith({
      title: 'Dune',
      scope: 'movie',
      year: 2021,
      poster: '/api/video/poster/movie/9',
    });

    fireEvent.click(within(panel).getByRole('button', { name: 'Open movie' }));
    expect(window.SoulSyncVideo!.openDetail).toHaveBeenCalledWith({
      kind: 'movie',
      id: 438631,
      source: 'tmdb',
    });

    // a single movie comes off without a confirm, like a single track does
    const changed = vi.fn();
    document.addEventListener('soulsync:video-wishlist-changed', changed);
    fireEvent.click(within(panel).getByRole('button', { name: 'Remove Dune from the wishlist' }));
    await waitFor(() => expect(posts('video/wishlist/remove')).toHaveLength(1));
    expect(posts('video/wishlist/remove')[0].body).toEqual({ scope: 'movie', tmdb_id: 438631 });
    expect(window.showConfirmDialog).not.toHaveBeenCalled();
    await waitFor(() => expect(changed).toHaveBeenCalled()); // the nav badge follows
    document.removeEventListener('soulsync:video-wishlist-changed', changed);
  });

  it('shows what the last search found, on demand', async () => {
    renderRoute('/wishlist?media=movies');
    fireEvent.click(await findEl('[data-tmdb="438631"]'));
    fireEvent.click(screen.getByRole('button', { name: 'What the last search found' }));
    await screen.findByTestId('video-diag');
    expect(gets('video/wishlist/diagnostics')[0].params).toEqual({
      kind: 'movie',
      tmdb_id: '438631',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Hide the last search' }));
    expect(screen.queryByTestId('video-diag')).toBeNull();
  });

  it('the Failing filter keeps only what keeps failing', async () => {
    renderRoute('/wishlist?media=movies&failing=true');
    await waitFor(() => expect(document.querySelectorAll('.wlp-poster')).toHaveLength(1));
    expect(document.querySelector('.wlp-poster')!.getAttribute('data-tmdb')).toBe('438631');
  });

  it('backfills missing art once, then reloads only if anything changed', async () => {
    renderRoute('/wishlist?media=movies');
    await waitFor(() => expect(posts('video/wishlist/backfill-movie-art')).toHaveLength(1));
    // updated: 0 means no reload
    expect(gets('video/wishlist')).toHaveLength(1);
  });

  it('search all missing and clear all', async () => {
    renderRoute('/wishlist?media=movies');
    fireEvent.click(await screen.findByRole('button', { name: 'Search all missing' }));
    await waitFor(() => expect(posts('video/wishlist/search-all')).toHaveLength(1));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Searching wishlist movies + episodes now — up to your Max downloads per run land in Downloads',
        'success',
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'More wishlist actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Clear all' }));
    await waitFor(() => expect(posts('video/wishlist/clear')).toHaveLength(1));
    expect(posts('video/wishlist/clear')[0].body).toEqual({ kind: 'movie' });
    expect(window.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({
        message: 'Remove ALL movies from your wishlist? This can’t be undone.',
      }),
    );
  });

  it('declining the clear-all confirm clears nothing', async () => {
    window.showConfirmDialog = vi.fn(async () => false);
    renderRoute('/wishlist?media=movies');
    fireEvent.click(await screen.findByRole('button', { name: 'More wishlist actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Clear all' }));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(posts('video/wishlist/clear')).toHaveLength(0);
  });

  it('pages through 60 at a time', async () => {
    pagination = { page: 1, total_pages: 3, total_count: 150, has_prev: false, has_next: true };
    renderRoute('/wishlist?media=movies');
    fireEvent.click(await screen.findByRole('button', { name: 'Next' }));
    await waitFor(() =>
      expect(gets('video/wishlist').some((c) => c.params.page === '2')).toBe(true),
    );
  });

  it('the filter box searches the server, debounced', async () => {
    renderRoute('/wishlist?media=movies');
    await waitFor(() => expect(gets('video/wishlist')).toHaveLength(1));
    const box = screen.getByLabelText('Filter wishlist');
    // typed a key at a time, 120ms apart: slower than one frame, faster than
    // the 250ms debounce. a debounce that had gone would search 'd' and 'du'.
    for (const text of ['d', 'du', 'dun']) {
      fireEvent.change(box, { target: { value: text } });
      await act(() => new Promise((r) => setTimeout(r, 120)));
    }
    await waitFor(() =>
      expect(gets('video/wishlist').some((c) => c.params.search === 'dun')).toBe(true),
    );
    // one request for the settled text, none for the keystrokes on the way
    expect(
      gets('video/wishlist').filter((c) => c.params.search === 'd' || c.params.search === 'du'),
    ).toHaveLength(0);
  });

  it('an empty tab says so in the old words', async () => {
    lists.movie = [];
    renderRoute('/wishlist?media=movies');
    expect(await screen.findByText('No movies on your wishlist yet')).toBeTruthy();
  });

  it('a failed load offers to try again instead of a blank page', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        if (url.pathname === '/api/video/wishlist')
          return res({ success: false, error: 'boom' }, 500);
        return res({ success: true });
      }),
    );
    renderRoute('/wishlist?media=movies');
    expect(await screen.findByText(/Could not load the wishlist/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('tv', () => {
  it('files shows under their worst episode and opens the panel', async () => {
    renderRoute('/wishlist?media=tv');
    await waitFor(() => expect(document.querySelectorAll('.wl-orb-group')).toHaveLength(2));
    expect(
      [...document.querySelectorAll('.wlp-zone')].map((z) => z.getAttribute('aria-label')),
    ).toEqual(['Nothing found', 'Still hunting']);
    expect(document.querySelector('[data-show="1399"] .wl-orb-meta')!.textContent).toBe(
      '1 of 3 stuck · Oct 18',
    );

    fireEvent.click(document.querySelector('[data-show="1399"] .wl-orb')!);
    const panel = screen.getByRole('complementary', { name: 'Shogun on the wishlist' });
    expect(within(panel).getByRole('note')).toHaveTextContent('Nothing found');
    // owned show: synopsis + cast come from the library detail
    expect(await within(panel).findByText('A shipwrecked sailor.')).toBeTruthy();
    expect(gets('video/detail/show/77')).toHaveLength(1);
  });

  it('search at show, season and episode level sends what the old page sent', async () => {
    renderRoute('/wishlist?media=tv');
    fireEvent.click(await findEl('[data-show="1399"] .wl-orb'));
    const panel = screen.getByRole('complementary', { name: 'Shogun on the wishlist' });

    fireEvent.click(within(panel).getByRole('button', { name: 'Search all 3 now' }));
    await waitFor(() => expect(posts('video/wishlist/search')).toHaveLength(1));
    expect(posts('video/wishlist/search')[0].body).toEqual({ scope: 'show', tmdb_id: 1399 });

    fireEvent.click(within(panel).getByRole('button', { name: 'Search Season 1 now' }));
    await waitFor(() => expect(posts('video/wishlist/search')).toHaveLength(2));
    expect(posts('video/wishlist/search')[1].body).toEqual({
      scope: 'season',
      tmdb_id: 1399,
      season_number: 1,
    });

    fireEvent.click(within(panel).getByRole('button', { name: 'Search S1·E1 · Feb 27 now' }));
    await waitFor(() => expect(posts('video/wishlist/search')).toHaveLength(3));
    expect(posts('video/wishlist/search')[2].body).toEqual({
      scope: 'episode',
      tmdb_id: 1399,
      season_number: 1,
      episode_number: 1,
    });
  });

  it('the picks, the retry and the removes send what the old page sent', async () => {
    renderRoute('/wishlist?media=tv');
    fireEvent.click(await findEl('[data-show="1399"] .wl-orb'));
    const panel = screen.getByRole('complementary', { name: 'Shogun on the wishlist' });

    // the stuck episode's ⋯: manual pick + retry with all sources
    fireEvent.click(within(panel).getByRole('button', { name: 'More for Anjin' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Pick a release' }));
    expect(window.VideoDownload!.manualSearch).toHaveBeenCalledWith({
      title: 'Shogun',
      scope: 'episode',
      season: 1,
      episode: 1,
      year: null,
      poster: '/api/video/poster/show/3',
    });
    fireEvent.click(within(panel).getByRole('button', { name: 'More for Anjin' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Retry with all sources' }));
    await waitFor(() => expect(posts('video/wishlist/retry')).toHaveLength(1));
    expect(posts('video/wishlist/retry')[0].body).toEqual({
      scope: 'episode',
      tmdb_id: 1399,
      season_number: 1,
      episode_number: 1,
    });

    // season pick
    fireEvent.click(within(panel).getByRole('button', { name: 'More for Season 1' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Pick a release' }));
    expect(window.VideoDownload!.manualSearch).toHaveBeenLastCalledWith(
      expect.objectContaining({ scope: 'season', season: 1, episode: null }),
    );

    // an episode comes off without a confirm; a season and a show ask first
    fireEvent.click(within(panel).getByRole('button', { name: 'Remove Anjin' }));
    await waitFor(() => expect(posts('video/wishlist/remove')).toHaveLength(1));
    expect(window.showConfirmDialog).not.toHaveBeenCalled();
    fireEvent.click(
      within(panel).getByRole('button', { name: 'Remove Season 1 from the wishlist' }),
    );
    await waitFor(() => expect(posts('video/wishlist/remove')).toHaveLength(2));
    expect(posts('video/wishlist/remove')[1].body).toEqual({
      scope: 'season',
      tmdb_id: 1399,
      season_number: 1,
    });
    expect(window.showConfirmDialog).toHaveBeenCalledTimes(1);
  });

  it('opens an episode in place: synopsis, guest cast, and the stuck one says why', async () => {
    renderRoute('/wishlist?media=tv');
    fireEvent.click(await findEl('[data-show="1399"] .wl-orb'));
    const panel = screen.getByRole('complementary', { name: 'Shogun on the wishlist' });
    const row = within(panel).getByText('Anjin').closest('button')!;
    fireEvent.click(row);
    expect(await within(panel).findByText('Guest Star')).toBeTruthy();
    expect(gets('video/episode/1399/1/1')).toHaveLength(1);
    await within(panel).findByTestId('video-diag');
    expect(gets('video/wishlist/diagnostics')[0].params).toEqual({
      kind: 'episode',
      tmdb_id: '1399',
      season_number: '1',
      episode_number: '1',
    });
    // a cast face opens the person page
    fireEvent.click(within(panel).getByTitle('Guest Star'));
    expect(window.SoulSyncVideo!.openDetail).toHaveBeenCalledWith({
      kind: 'person',
      id: 11,
      source: 'tmdb',
    });
  });

  it('declining the show confirm removes nothing', async () => {
    window.showConfirmDialog = vi.fn(async () => false);
    renderRoute('/wishlist?media=tv');
    fireEvent.click(await findEl('[data-show="1399"] .wl-orb'));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Shogun from the wishlist' }));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(posts('video/wishlist/remove')).toHaveLength(0);
  });

  it('the orb name opens the show page, the bubble opens the panel', async () => {
    renderRoute('/wishlist?media=tv');
    await waitFor(() =>
      expect(document.querySelector('[data-show="1399"] .wl-orb-label')).toBeTruthy(),
    );
    fireEvent.click(document.querySelector('[data-show="1399"] .wl-orb-label')!);
    expect(window.SoulSyncVideo!.openDetail).toHaveBeenCalledWith({
      kind: 'show',
      id: 77,
      source: 'library',
    });
    expect(document.querySelector('.wlp-sheet')).toBeNull();
  });
});

describe('youtube', () => {
  it('channels are orbs; the panel downloads and removes by video, year and channel', async () => {
    renderRoute('/wishlist?media=youtube');
    await waitFor(() => expect(document.querySelectorAll('.wl-orb-group')).toHaveLength(1));
    expect(gets('video/youtube/wishlist')).toHaveLength(1);
    // no Failing chip on youtube, as before
    expect(screen.queryByRole('button', { name: /Failing/ })).toBeNull();
    // youtube art goes through the image proxy
    const img = document.querySelector('[data-channel="UCabc"] .wl-orb-img') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe(
      `/api/video/img?u=${encodeURIComponent('https://yt3.ggpht.com/avatar')}`,
    );

    fireEvent.click(document.querySelector('[data-channel="UCabc"] .wl-orb')!);
    const panel = screen.getByRole('complementary', { name: 'Some Channel on the wishlist' });
    expect(within(panel).getByRole('note')).toHaveTextContent('Unavailable on YouTube');

    fireEvent.click(within(panel).getByRole('button', { name: 'Download Fine Video now' }));
    await waitFor(() => expect(posts('video/youtube/download')).toHaveLength(1));
    expect(posts('video/youtube/download')[0].body).toEqual({
      video_id: 'v2',
      channel_id: 'UCabc',
      channel_title: 'Some Channel',
      video_title: 'Fine Video',
      published_at: '2025-01-10',
    });

    fireEvent.click(within(panel).getByRole('button', { name: 'Remove Fine Video' }));
    await waitFor(() => expect(posts('video/youtube/wishlist/remove')).toHaveLength(1));
    expect(posts('video/youtube/wishlist/remove')[0].body).toEqual({
      scope: 'video',
      source_id: 'v2',
    });

    fireEvent.click(within(panel).getByRole('button', { name: 'Remove 2025 from the wishlist' }));
    await waitFor(() => expect(posts('video/youtube/wishlist/remove')).toHaveLength(3));
    expect(
      posts('video/youtube/wishlist/remove')
        .slice(1)
        .map((c) => c.body),
    ).toEqual([
      { scope: 'video', source_id: 'v1' },
      { scope: 'video', source_id: 'v2' },
    ]);

    fireEvent.click(
      within(panel).getByRole('button', { name: 'Remove Some Channel from the wishlist' }),
    );
    await waitFor(() => expect(posts('video/youtube/unfollow')).toHaveLength(1));
    expect(posts('video/youtube/unfollow')[0].body).toEqual({ youtube_id: 'UCabc' });
    await waitFor(() => expect(posts('video/youtube/wishlist/remove')).toHaveLength(4));
    expect(posts('video/youtube/wishlist/remove')[3].body).toEqual({
      scope: 'channel',
      source_id: 'UCabc',
    });
  });

  it('a video opens in place with its details and a watch link', async () => {
    renderRoute('/wishlist?media=youtube');
    fireEvent.click(await findEl('[data-channel="UCabc"] .wl-orb'));
    const panel = screen.getByRole('complementary', { name: 'Some Channel on the wishlist' });
    fireEvent.click(within(panel).getByText('Fine Video').closest('button')!);
    expect(await within(panel).findByText('A video about things.')).toBeTruthy();
    expect(within(panel).getByText('Jan 10 · 2:05 · 1.5K views')).toBeTruthy();
    expect(within(panel).getByRole('link', { name: /Watch on YouTube/ })).toHaveAttribute(
      'href',
      'https://www.youtube.com/watch?v=v2',
    );
  });

  it('the bulk action downloads instead of searching', async () => {
    renderRoute('/wishlist?media=youtube');
    fireEvent.click(await screen.findByRole('button', { name: 'Download all waiting' }));
    await waitFor(() => expect(posts('video/wishlist/youtube/download-all')).toHaveLength(1));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Queued 2 videos — 1 downloading now, the rest drain automatically',
        'success',
      ),
    );
    expect(posts('video/wishlist/search-all')).toHaveLength(0);
  });
});

describe('the page around the tabs', () => {
  it('switching tabs clears the filter and remembers the tab for the sidebar', async () => {
    const { router } = renderRoute('/wishlist?media=movies&q=dune');
    const tabs = await screen.findByRole('tablist', { name: 'Video wishlist' });
    fireEvent.click(within(tabs).getByRole('tab', { name: /TV/ }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ media: 'tv', q: '' }));
    expect(window.sessionStorage.getItem('soulsync.videoWishlistTab')).toBe('tv');
  });

  it('asks the shell for the VIDEO wishlist permission on the video tabs', async () => {
    renderRoute('/wishlist?media=tv');
    await waitFor(() => expect(document.querySelector('.wlp--video')).toBeTruthy());
    expect(window.SoulSyncWebShellBridge!.isPageAllowed).toHaveBeenCalledWith(
      'wishlist',
      '?media=tv',
    );
  });

  it('a video-only profile gets the video tabs and is still kept off the music one', async () => {
    // the real shell answers with isPageAccessAllowed: 'wishlist' alone is the
    // music page (denied), with ?media=<video tab> it is the video wishlist
    const allowed = vi.fn((page: string, search?: string) =>
      page === 'wishlist' ? Boolean(search && /media=(movies|tv|youtube)/.test(search)) : true,
    );
    window.SoulSyncWebShellBridge = createShellBridge({ isPageAllowed: allowed });
    const { router } = renderRoute('/wishlist?media=tv');
    await waitFor(() =>
      expect(document.querySelectorAll('.wl-orb-group').length).toBeGreaterThan(0),
    );
    // every gate asked media-aware: the route guard and the page shell
    expect(allowed).not.toHaveBeenCalledWith('wishlist');
    expect(window.SoulSyncWebShellBridge!.showReactHost).toHaveBeenCalledWith('wishlist');
    expect(router.state.location.pathname).toBe('/wishlist');
  });

  it('the music tabs never fetch the video lists, and the video tabs never fetch music', async () => {
    renderRoute('/wishlist?media=tv');
    await waitFor(() => expect(gets('video/wishlist').length).toBeGreaterThan(0));
    expect(calls.some((c) => c.path.startsWith('wishlist/'))).toBe(false);
  });

  it('a change made elsewhere reloads the list', async () => {
    renderRoute('/wishlist?media=movies');
    await waitFor(() => expect(gets('video/wishlist')).toHaveLength(1));
    act(() => {
      document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
    });
    await waitFor(() => expect(gets('video/wishlist')).toHaveLength(2));
  });
});
