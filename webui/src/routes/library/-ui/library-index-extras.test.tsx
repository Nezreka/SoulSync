import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * The old library page's index tools on the Library v2 page, ported from
 * dev's library-page.test.tsx: Radio, the artist card's top-tracks play, the
 * alphabet bar, the has/no provider filter, "could be better" with its card
 * count, Escape on the search, the search-online button, provider badges,
 * the album card's play, Export, and old bookmarks.
 */

let requested: { url: string; method: string }[] = [];
let artistRows: Record<string, unknown>[] = [];
let albumRows: Record<string, unknown>[] = [];
let upgradable: Record<string, number> = {};
let songRows: Record<string, unknown>[] = [];

function artist(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    name: 'Aphex Twin',
    image_url: null,
    genres: [],
    monitored: false,
    album_count: 2,
    single_count: 0,
    track_count: 12,
    tracks_present: 12,
    tracks_missing: 0,
    total_size_bytes: 0,
    user_overrides: {},
    provider_ids: { spotify: 'sp-aphex', deezer: '123' },
    ...over,
  };
}

function album(over: Record<string, unknown> = {}) {
  return {
    id: 5,
    title: 'Selected Ambient Works',
    album_type: 'album',
    year: 1992,
    image_url: null,
    added_at: null,
    monitored: false,
    artist_id: 1,
    artist_name: 'Aphex Twin',
    track_count: 1,
    tracks_present: 1,
    tracks_missing: 0,
    provider_ids: { spotify: 'sp-saw' },
    ...over,
  };
}

const page = (rows: unknown[]) => ({
  page: 1,
  limit: 75,
  total_count: rows.length,
  total_pages: 1,
  has_prev: false,
  has_next: false,
});

function stubFetch() {
  requested = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) || 'GET';
      requested.push({ url, method });
      let body: unknown = {};
      if (url.includes('/api/library/v2/enabled')) {
        body = { success: true, enabled: true, can_write: true, can_wish: true };
      } else if (url.includes('/api/library/v2/import/status')) {
        body = { success: true, running: false, stage: 'done', artwork_cache: { running: false } };
      } else if (url.includes('/api/library/v2/artists/upgradable-counts')) {
        body = { success: true, counts: upgradable };
      } else if (url.includes('/api/library/v2/artists')) {
        body = { success: true, artists: artistRows, pagination: page(artistRows) };
      } else if (url.includes('/api/library/v2/albums/5')) {
        body = {
          success: true,
          album: {
            id: 5,
            title: 'Selected Ambient Works',
            image_url: null,
            tracks: [
              {
                id: 50,
                title: 'Xtal',
                track_number: 1,
                disc_number: 1,
                artists: [{ id: 1, name: 'Aphex Twin', role: 'primary' }],
                file: { path: '/m/xtal.flac', file_state: 'active', format: 'flac' },
              },
            ],
          },
        };
      } else if (url.includes('/api/library/v2/albums')) {
        body = { success: true, albums: albumRows, pagination: page(albumRows) };
      } else if (url.includes('/top-tracks')) {
        body = {
          success: true,
          source: 'spotify',
          tracks: [
            { title: 'Xtal', name: 'Xtal', artists: [{ name: 'Aphex Twin' }] },
            { title: 'Tha', name: 'Tha', artists: [{ name: 'Aphex Twin' }] },
          ],
        };
      } else if (url.includes('/api/library/songs')) {
        body = { success: true, songs: songRows, pagination: page(songRows) };
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

function renderPage(entry = '/library') {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return { router, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

const lastList = (kind: 'artists' | 'albums') => {
  const calls = requested.filter(
    (r) => r.url.includes(`/api/library/v2/${kind}?`) || r.url.endsWith(`/api/library/v2/${kind}`),
  );
  return new URL(calls.at(-1)!.url, 'http://x').searchParams;
};

beforeEach(() => {
  artistRows = [artist()];
  albumRows = [album()];
  upgradable = {};
  songRows = [];
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showLibraryDownloadsSection = vi.fn();
  window.showToast = vi.fn();
  window._handoffLibrarySearchToEnhancedSearch = vi.fn();
  stubFetch();
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
  delete window.showLibraryDownloadsSection;
  delete window.playTrackList;
  delete window.startLibraryRadio;
});

describe('the header', () => {
  it('says what the page is, not "experimental"', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(screen.queryByText('Experimental library manager')).toBeNull();
  });

  it('the Radio button hands off to the player global', async () => {
    window.startLibraryRadio = vi.fn();
    renderPage();
    await screen.findByText('Aphex Twin');
    fireEvent.click(screen.getByText(/Radio/));
    expect(window.startLibraryRadio).toHaveBeenCalled();
  });

  it('Export opens the artists export', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    fireEvent.click(screen.getByText(/Export$/));
    expect(await screen.findByRole('dialog')).toBeTruthy();
  });
});

describe('artist cards', () => {
  it("▶ plays the artist's ranked top tracks in provider order", async () => {
    window.playTrackList = vi.fn();
    renderPage();
    await screen.findByText('Aphex Twin');
    fireEvent.click(screen.getByLabelText("Play Aphex Twin's top tracks"));
    await waitFor(() => expect(window.playTrackList).toHaveBeenCalledTimes(1));
    expect(window.playTrackList).toHaveBeenCalledWith(
      [
        expect.objectContaining({ title: 'Xtal', artist: 'Aphex Twin' }),
        expect.objectContaining({ title: 'Tha', artist: 'Aphex Twin' }),
      ],
      'Aphex Twin: Top Tracks',
    );
  });

  it('wears its provider badges, linking out', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    const link = screen.getByTitle('Open on Spotify');
    expect(link.getAttribute('href')).toBe('https://open.spotify.com/artist/sp-aphex');
    expect(screen.getByTitle('Open on Deezer').getAttribute('href')).toBe(
      'https://www.deezer.com/artist/123',
    );
  });

  it('says how many of its tracks could be better', async () => {
    upgradable = { '1': 3 };
    renderPage();
    expect(await screen.findByText('3 could be better')).toBeTruthy();
  });
});

describe('filters', () => {
  it('the alphabet bar filters by first letter, in the url', async () => {
    const { router } = renderPage();
    await screen.findByText('Aphex Twin');
    const bar = document.querySelector('#alphabet-selector') as HTMLElement;
    fireEvent.click(within(bar).getByText('C'));
    await waitFor(() => expect(lastList('artists').get('letter')).toBe('c'));
    expect(router.state.location.search).toMatchObject({ letter: 'c', page: 1 });
    expect(within(bar).getByText('C').getAttribute('aria-pressed')).toBe('true');
  });

  it('has / no provider goes to the server', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(lastList('artists').has('source')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }));
    fireEvent.change(screen.getByLabelText('Filter by metadata source'), {
      target: { value: '!spotify' },
    });
    await waitFor(() => expect(lastList('artists').get('source')).toBe('!spotify'));
  });

  it('could be better filters the grid', async () => {
    const { router } = renderPage();
    await screen.findByText('Aphex Twin');
    expect(lastList('artists').has('quality')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }));
    fireEvent.click(screen.getByRole('switch', { name: /Could be better/ }));
    await waitFor(() => expect(lastList('artists').get('quality')).toBe('upgradable'));
    expect(router.state.location.search).toMatchObject({ quality: 'upgradable', page: 1 });
    // what's on reads as a chip, which takes it off again
    const chip = await screen.findByRole('button', { name: 'Remove filter: Could be better' });
    expect(screen.getByRole('button', { name: /Filters/ }).textContent).toContain('1');
    fireEvent.click(chip);
    await waitFor(() => expect(router.state.location.search).toMatchObject({ quality: '' }));
  });

  it('the filters menu closes on Escape and a click outside', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    const button = screen.getByRole('button', { name: /Filters/ });
    fireEvent.click(button);
    expect(screen.getByRole('dialog', { name: 'Filters' })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Filters' })).toBeNull();
    fireEvent.click(button);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('dialog', { name: 'Filters' })).toBeNull();
  });

  it('monitoring lives in the filters menu', async () => {
    const { router } = renderPage();
    await screen.findByText('Aphex Twin');
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Not monitored' }));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ monitored: 'unmonitored', page: 1 }),
    );
    expect(
      await screen.findByRole('button', { name: 'Remove filter: Not monitored' }),
    ).toBeInTheDocument();
  });

  it('ignores a made-up quality value in the url', async () => {
    renderPage('/library?quality=perfect');
    await screen.findByText('Aphex Twin');
    expect(lastList('artists').has('quality')).toBe(false);
  });

  it('the albums tab has the letter and source filters too', async () => {
    renderPage('/library?section=albums&letter=s&source=spotify');
    await screen.findByText('Selected Ambient Works');
    expect(lastList('albums').get('letter')).toBe('s');
    expect(lastList('albums').get('source')).toBe('spotify');
  });
});

describe('the search box', () => {
  it('Escape clears it, and the url replaces rather than piling up history', async () => {
    const { router } = renderPage('/library?q=aphex');
    const input = (await screen.findByLabelText('Filter artists')) as HTMLInputElement;
    expect(input.value).toBe('aphex');
    const depth = router.history.length;
    fireEvent.keyDown(input, { key: 'Escape' });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: '' }));
    expect(router.history.length).toBe(depth);
  });

  it('× clears it', async () => {
    const { router } = renderPage('/library?q=aphex');
    await screen.findByLabelText('Filter artists');
    fireEvent.click(screen.getByLabelText('Clear search'));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: '' }));
  });

  it('a search that finds nothing offers to look online', async () => {
    artistRows = [];
    renderPage('/library?q=nonesuch');
    const cta = await screen.findByText(/Search online for/);
    fireEvent.click(cta.closest('button')!);
    expect(window._handoffLibrarySearchToEnhancedSearch).toHaveBeenCalledWith('nonesuch');
  });
});

describe('album cards', () => {
  it('▶ queues the tracks you own', async () => {
    window.playTrackList = vi.fn();
    renderPage('/library?section=albums');
    await screen.findByText('Selected Ambient Works');
    fireEvent.click(screen.getByLabelText('Play Selected Ambient Works'));
    await waitFor(() => expect(window.playTrackList).toHaveBeenCalledTimes(1));
    const [queue] = (window.playTrackList as ReturnType<typeof vi.fn>).mock.calls[0] as [
      Record<string, unknown>[],
    ];
    expect(queue[0]).toMatchObject({ lib2_track_id: 50, file_path: '/m/xtal.flac' });
  });

  it('wears its provider badges', async () => {
    renderPage('/library?section=albums');
    await screen.findByText('Selected Ambient Works');
    expect(screen.getByTitle('Open on Spotify').getAttribute('href')).toBe(
      'https://open.spotify.com/album/sp-saw',
    );
  });
});

describe('old bookmarks', () => {
  it('view=albums opens the albums tab, with its sort', async () => {
    const { router } = renderPage('/library?view=albums&sort=recent&letter=b');
    await screen.findByText('Selected Ambient Works');
    expect(router.state.location.search).toMatchObject({
      section: 'albums',
      albumSort: 'added',
      letter: 'b',
    });
  });

  it('view=songs opens the songs tab with its filters', async () => {
    const { router } = renderPage('/library?view=songs&sort=artist&dupes=1&quality=lossless');
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        section: 'songs',
        songSort: 'artist',
        dupes: true,
        quality: 'lossless',
      }),
    );
  });

  it('watchlist=unwatched is the unmonitored filter', async () => {
    const { router } = renderPage('/library?view=artists&watchlist=unwatched&sort=recent');
    await screen.findByText('Aphex Twin');
    expect(router.state.location.search).toMatchObject({
      section: 'artists',
      monitored: 'unmonitored',
      sort: 'added',
    });
  });
});
