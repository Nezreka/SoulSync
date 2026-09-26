import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { LibraryAlbum } from '../-library.types';

/**
 * The library's second view.
 *
 * Driven through the real router, like the artists view's tests, so
 * validateSearch and the URL-driven filter state are exercised rather than
 * stubbed — `view` is a URL filter like the other five, not local state.
 */

function album(over: Partial<LibraryAlbum> & { id: string }): LibraryAlbum {
  return {
    title: `Album ${over.id}`,
    artist_id: 'a1',
    artist_name: 'Blur',
    year: 1994,
    thumb_url: null,
    track_count: 12,
    ...over,
  };
}

let requested: string[] = [];
let albumTracks: unknown[] = [];

function stubFetch(albums: LibraryAlbum[], total = albums.length, pages = 1) {
  requested = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      requested.push(url);
      const page = Number(new URL(url, 'http://x').searchParams.get('page') ?? 1);
      const body = url.includes('/tracks')
        ? { success: true, tracks: albumTracks }
        : url.includes('/api/library/albums')
          ? {
              success: true,
              albums,
              pagination: {
                page,
                limit: 75,
                total_count: total,
                total_pages: pages,
                has_prev: page > 1,
                has_next: page < pages,
              },
            }
          : url.includes('/api/library/artists')
            ? {
                success: true,
                artists: [{ id: 1, name: 'Aphex Twin' }],
                pagination: {
                  page,
                  limit: 75,
                  total_count: 1,
                  total_pages: 1,
                  has_prev: false,
                  has_next: false,
                },
              }
            : { success: true, count: 0, artist_id: null };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

function renderPage(entry = '/library?view=albums') {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return {
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

const albumCalls = () => requested.filter((u) => u.includes('/api/library/albums'));
const lastQuery = () => new URL(albumCalls().at(-1)!, 'http://x').searchParams;

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showLibraryDownloadsSection = vi.fn();
  window.showToast = vi.fn();
  window.currentMusicSourceName = 'spotify';
  albumTracks = [
    { id: 't1', title: 'Girls & Boys', track_number: 1, file_path: '/m/1.flac', duration: 260000 },
  ];
  stubFetch([
    album({ id: 'al1', title: 'Parklife', artist_name: 'Blur', year: 1994, track_count: 16 }),
  ]);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
  delete window.showLibraryDownloadsSection;
});

describe('the view switcher', () => {
  it('starts on artists, the view the page has always had', async () => {
    renderPage('/library');
    await screen.findByText('Aphex Twin');
    expect(albumCalls()).toHaveLength(0);
  });

  it('switches to albums and back from the toolbar', async () => {
    const { router } = renderPage('/library');
    await screen.findByText('Aphex Twin');

    fireEvent.click(screen.getByRole('button', { name: 'Albums' }));

    await waitFor(() => expect(router.state.location.search).toMatchObject({ view: 'albums' }));
    await screen.findByText('Parklife');
  });

  it('resets to page 1, because page 7 of the artists is meaningless for albums', async () => {
    const { router } = renderPage('/library?page=7');
    await screen.findByText('Aphex Twin');

    fireEvent.click(screen.getByRole('button', { name: 'Albums' }));

    await waitFor(() => expect(router.state.location.search).toMatchObject({ page: 1 }));
  });
});

describe('the album grid', () => {
  it('reads from the albums endpoint', async () => {
    renderPage();
    await waitFor(() => expect(albumCalls().length).toBeGreaterThan(0));
    expect(requested.filter((u) => u.includes('/api/library/artists'))).toHaveLength(0);
  });

  it('shows the title, the artist, the year and how many tracks you own', async () => {
    renderPage();
    await screen.findByText('Parklife');
    expect(screen.getByText('Blur')).toBeTruthy();
    expect(document.querySelector('.library-album-card')?.textContent).toContain('1994');
    expect(document.querySelector('.library-album-card')?.textContent).toContain('16 tracks');
  });

  it('is the same tile the artists view draws, so the two grids match', async () => {
    renderPage();
    await screen.findByText('Parklife');
    // The album card wears the artist card's own classes rather than a second
    // near-identical set — the grid is meant to be indistinguishable.
    expect(document.querySelector('.library-artists-grid')).not.toBeNull();
    expect(document.querySelector('.library-album-card.library-artist-card')).not.toBeNull();
    expect(document.querySelector('.library-artist-image')).not.toBeNull();
    expect(document.querySelector('.library-artist-name')?.textContent).toBe('Parklife');
  });

  it('counts albums in the header, not artists', async () => {
    stubFetch([album({ id: 'al1' })], 815);
    renderPage();
    await waitFor(() => expect(document.querySelector('.stat-number')?.textContent).toBe('815'));
    expect(document.querySelector('.stat-label')?.textContent).toBe('Albums');
  });
});

describe('the filters an album has', () => {
  it('sends search and letter, which both mean something for a title', async () => {
    renderPage('/library?view=albums&q=park&letter=p&page=2');
    await waitFor(() => expect(albumCalls().length).toBeGreaterThan(0));
    const p = lastQuery();
    expect(p.get('search')).toBe('park');
    expect(p.get('letter')).toBe('p');
    expect(p.get('page')).toBe('2');
    expect(p.get('limit')).toBe('75');
  });

  it('keeps the alphabet strip, which is how you cross a big collection', async () => {
    renderPage();
    await screen.findByText('Parklife');
    expect(document.querySelector('#alphabet-selector')).not.toBeNull();
  });
});

describe('opening an album', () => {
  it("links to its artist's page, pointed at that album", async () => {
    renderPage();
    await screen.findByText('Parklife');

    // A real link, like the artist card, so middle-click and open-in-new-tab
    // work. `album` is what the artist page expands and scrolls to.
    expect(document.querySelector('.library-album-card')?.getAttribute('href')).toBe(
      '/artist-detail/library/a1?album=al1',
    );
  });
});

describe('the source badges', () => {
  it('draws one per provider the album is matched to', async () => {
    stubFetch([
      album({
        id: 'al1',
        musicbrainz_release_id: 'mb-rel',
        deezer_id: 7,
        soul_id: 'soul_abc',
      }),
    ]);
    renderPage();
    await screen.findByText('Album al1');

    const titles = [...document.querySelectorAll('.library-album-card .source-card-icon')].map(
      (n) => n.getAttribute('title'),
    );
    expect(titles).toEqual(['MusicBrainz', 'Deezer', 'SoulID: soul_abc']);
  });

  it('links a badge to the ALBUM page, not the artist one', async () => {
    stubFetch([album({ id: 'al1', musicbrainz_release_id: 'mb-rel' })]);
    renderPage();
    await screen.findByText('Album al1');

    expect(document.querySelector('.source-card-icon')?.getAttribute('data-url')).toBe(
      'https://musicbrainz.org/release/mb-rel',
    );
  });

  it('draws none at all for an album nothing has matched', async () => {
    renderPage();
    await screen.findByText('Parklife');
    expect(document.querySelector('.library-album-card .source-card-icon')).toBeNull();
  });
});

describe('the source filter', () => {
  it('is offered in the album view', async () => {
    renderPage();
    await screen.findByText('Parklife');
    expect(document.querySelector('.library-source-filter')).not.toBeNull();
  });

  it('sends the chosen source to the albums endpoint', async () => {
    renderPage('/library?view=albums&source=musicbrainz');
    await waitFor(() => expect(albumCalls().length).toBeGreaterThan(0));
    expect(lastQuery().get('source_filter')).toBe('musicbrainz');
  });

  it('omits the param when no source is chosen', async () => {
    renderPage();
    await waitFor(() => expect(albumCalls().length).toBeGreaterThan(0));
    expect(lastQuery().has('source_filter')).toBe(false);
  });

  it('still hides the watchlist filter, which an album has no answer for', async () => {
    renderPage();
    await screen.findByText('Parklife');
    expect(document.querySelector('#watchlist-filter')).toBeNull();
  });
});

describe('playing an album', () => {
  it('queues the tracks you own, under the album name', async () => {
    window.playTrackList = vi.fn();
    renderPage();
    await screen.findByText('Parklife');

    fireEvent.click(document.querySelector('.library-artist-play-btn')!);

    await waitFor(() => expect(window.playTrackList).toHaveBeenCalledTimes(1));
    expect(window.playTrackList).toHaveBeenCalledWith(
      [expect.objectContaining({ title: 'Girls & Boys', artist: 'Blur', album: 'Parklife' })],
      'Parklife',
    );
    delete window.playTrackList;
  });

  it('says so rather than queueing nothing when no track has a file', async () => {
    window.playTrackList = vi.fn();
    albumTracks = [];
    renderPage();
    await screen.findByText('Parklife');

    fireEvent.click(document.querySelector('.library-artist-play-btn')!);

    await waitFor(() => expect(window.showToast).toHaveBeenCalled());
    expect(window.playTrackList).not.toHaveBeenCalled();
    delete window.playTrackList;
  });
});

describe('when the albums fail to load', () => {
  it('names what actually failed', async () => {
    // The toast is fixed text, and the page has two things it can be loading
    // now — "Failed to load artists" in the album grid is simply wrong.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('nope', { status: 500 })),
    );
    renderPage();

    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Failed to load albums', 'error'),
    );
  });
});
