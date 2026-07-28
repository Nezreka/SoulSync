import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * Driven through the real router: the page reads route params and search, and
 * useProfile needs the root route context.
 */
vi.mock('@/platform/shell/route-manifest', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/platform/shell/route-manifest')>();
  return {
    ...actual,
    getShellRouteByPageId: (pageId: string) =>
      pageId === 'artist-detail'
        ? { ...actual.getShellRouteByPageId('artist-detail'), kind: 'react' }
        : actual.getShellRouteByPageId(pageId as never),
  };
});

let requested: string[] = [];
let streamFrames: string[] = [];
let gapFillBody: unknown = { success: true, gaps: {} };
let enhancedBody: unknown = { success: true, artist: { id: 42, name: 'Aphex Twin' }, albums: [] };

function stubDetail(body: unknown, status = 200) {
  requested = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      requested.push(url);
      if (url.includes('/enhanced')) {
        return new Response(JSON.stringify(enhancedBody), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/discography/gap-fill')) {
        return new Response(JSON.stringify(gapFillBody), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/api/library/completion-stream')) {
        const encoder = new TextEncoder();
        let i = 0;
        return new Response(
          new ReadableStream({
            pull(controller) {
              if (i < streamFrames.length) controller.enqueue(encoder.encode(streamFrames[i++]));
              else controller.close();
            },
          }),
          { status: 200 },
        );
      }
      if (url.includes('/api/album/')) {
        return new Response(JSON.stringify({ success: true, tracks: [{ id: 1 }] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

function renderPage(entry = '/artist-detail/library/42') {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return { router, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

const LIBRARY = {
  success: true,
  artist: { id: 42, name: 'Aphex Twin', server_source: 'plex' },
  discography: { albums: [{ id: 1, title: 'SAW', owned: true }], source: 'spotify' },
};

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showToast = vi.fn();
  window.loadSimilarArtists = vi.fn();
  window.cancelSimilarArtistsLoad = vi.fn();
  window.checkArtistEnhanceEligibility = vi.fn();
  window.initializeLibraryWatchlistButton = vi.fn();
  window.observeLazyBackgrounds = vi.fn();
  streamFrames = [];
  gapFillBody = { success: true, gaps: {} };
  enhancedBody = {
    success: true,
    artist: { id: 42, name: 'Aphex Twin' },
    albums: [{ id: 1, title: 'SAW', record_type: 'album', tracks: [{ id: 9, title: 'Xtal' }] }],
  };
  localStorage.clear();
  stubDetail(LIBRARY);
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
  delete document.body.dataset.artistSource;
});

describe('body[data-artist-source]', () => {
  it('tags the body library for an artist with a server_source', async () => {
    // CSS keys off this to hide library-only UI.
    renderPage();
    await waitFor(() => expect(document.body.dataset.artistSource).toBe('library'));
  });

  it('tags it source when there is no library record', async () => {
    stubDetail({ success: true, artist: { id: 'sp1', name: 'X' }, discography: {} });
    renderPage();
    await waitFor(() => expect(document.body.dataset.artistSource).toBe('source'));
  });

  it('clears the flag on unmount so the next page does not inherit it', async () => {
    const { unmount } = renderPage();
    await waitFor(() => expect(document.body.dataset.artistSource).toBe('library'));
    unmount();
    expect(document.body.dataset.artistSource).toBeUndefined();
  });
});

describe('fire-and-forget side effects', () => {
  it('loads similar artists, cancelling any in flight first', async () => {
    renderPage();
    await waitFor(() => expect(window.loadSimilarArtists).toHaveBeenCalledWith('Aphex Twin'));
    expect(window.cancelSimilarArtistsLoad).toHaveBeenCalled();
  });

  it('cancels similar artists on unmount', async () => {
    const { unmount } = renderPage();
    await waitFor(() => expect(window.loadSimilarArtists).toHaveBeenCalled());
    (window.cancelSimilarArtistsLoad as ReturnType<typeof vi.fn>).mockClear();
    unmount();
    expect(window.cancelSimilarArtistsLoad).toHaveBeenCalled();
  });

  it('probes enhance eligibility for a LIBRARY artist only', async () => {
    renderPage();
    await waitFor(() => expect(window.checkArtistEnhanceEligibility).toHaveBeenCalledWith(42));
  });

  it('does not probe eligibility for a source artist', async () => {
    // The endpoint only works on library primary keys.
    stubDetail({ success: true, artist: { id: 'sp1', name: 'X' }, discography: {} });
    renderPage();
    await screen.findByText('X');
    expect(window.checkArtistEnhanceEligibility).not.toHaveBeenCalled();
  });

  it('wires the watchlist button to the canonical Spotify identity', async () => {
    stubDetail({
      ...LIBRARY,
      spotify_artist: { spotify_artist_id: 'sp9', spotify_artist_name: 'Aphex Twin' },
    });
    renderPage();
    await waitFor(() =>
      expect(window.initializeLibraryWatchlistButton).toHaveBeenCalledWith('sp9', 'Aphex Twin'),
    );
  });

  it('warns about a provider error but still renders the page', async () => {
    stubDetail({ ...LIBRARY, provider_error: { error: 'MusicBrainz timed out' } });
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(window.showToast).toHaveBeenCalledWith(
      'Discography provider warning: MusicBrainz timed out',
      'error',
    );
  });
});

describe('failure', () => {
  it('shows the error state with the hero HIDDEN, and toasts', async () => {
    stubDetail({ success: false, error: 'Artist not found' });
    renderPage();
    await screen.findByText('Failed to load artist details');
    expect(document.getElementById('artist-hero-section')).toBeNull();
    expect(document.getElementById('artist-detail-error-message')?.textContent).toBe(
      'Artist not found',
    );
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Failed to load artist details: Artist not found',
        'error',
      ),
    );
  });
});

describe('source artists', () => {
  it('settles unknown ownership to false so cards do not check forever', async () => {
    // Nothing will ever stream a result for a source artist.
    stubDetail({
      success: true,
      artist: { id: 'sp1', name: 'X' },
      discography: { albums: [{ id: 1, title: 'A', owned: null }] },
    });
    renderPage();
    await screen.findByText('A');
    expect(document.querySelector('.release-card')?.className).toContain('missing');
    expect(document.querySelector('.completion-overlay')).toBeNull();
  });
});

describe('MusicBrainz declutter', () => {
  it('applies once the discography source is known', async () => {
    stubDetail({
      ...LIBRARY,
      discography: {
        albums: [{ id: 1, title: 'Studio', secondary_types: [], owned: false }],
        source: 'musicbrainz',
      },
    });
    renderPage();
    await screen.findByText('Studio');
    // The Live toggle is relabelled and starts OFF under the declutter.
    const live = document.querySelector(
      '[data-filter="content"][data-value="live"]',
    ) as HTMLElement;
    expect(live.textContent).toBe('Non-Studio');
    expect(live.className).not.toContain('active');
  });

  it('leaves other sources with the toggle on and labelled Live', async () => {
    renderPage();
    await screen.findByText('SAW');
    const live = document.querySelector(
      '[data-filter="content"][data-value="live"]',
    ) as HTMLElement;
    expect(live.textContent).toBe('Live');
    expect(live.className).toContain('active');
  });
});

describe('ownership completion stream', () => {
  const UNRESOLVED = {
    success: true,
    artist: { id: 42, name: 'Aphex Twin', server_source: 'plex' },
    discography: {
      albums: [
        { id: 1, title: 'SAW', owned: null },
        { id: 2, title: 'Drukqs', owned: null },
      ],
      source: 'spotify',
    },
  };

  it('merges streamed ownership into the bars and the section counts', async () => {
    streamFrames = [
      'data: {"type":"completion","id":1,"category":"albums","status":"ok","formats":["FLAC"]}\n',
      'data: {"type":"completion","id":2,"category":"albums","status":"missing"}\n',
      'data: {"type":"complete","processed_count":2}\n',
    ];
    stubDetail(UNRESOLVED);
    renderPage();

    await waitFor(() => expect(document.getElementById('albums-stats')?.textContent).toBe('1/2'));
    // The cards move with the bar — the section counts read from the same
    // merged discography, so this proves the merge reaches the grid too.
    expect(screen.getByText('1 owned')).toBeTruthy();
    expect(screen.getByText('1 missing')).toBeTruthy();
    // Formats are artist-level and only appear on the terminal frame.
    expect(document.querySelector('.artist-format-tag')?.textContent).toBe('FLAC');
  });

  it('does not open a stream when every release is already resolved', async () => {
    stubDetail(LIBRARY);
    renderPage();
    await screen.findByText('Aphex Twin');
    await new Promise((r) => setTimeout(r, 20));
    expect(requested.some((u) => u.includes('completion-stream'))).toBe(false);
  });

  it('does not open a stream for a source artist', async () => {
    // No library to check against; settleOwnershipForSourceArtist resolves the
    // cards to "missing" up front instead.
    stubDetail({
      success: true,
      artist: { id: 'sp1', name: 'X' },
      discography: { albums: [{ id: 1, title: 'A', owned: null }], source: 'deezer' },
    });
    renderPage();
    await screen.findByText('X');
    await new Promise((r) => setTimeout(r, 20));
    expect(requested.some((u) => u.includes('completion-stream'))).toBe(false);
  });
});

describe('gap-fill (#1067)', () => {
  it('does not ask for gaps until the chip is on', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    await new Promise((r) => setTimeout(r, 20));
    expect(requested.some((u) => u.includes('gap-fill'))).toBe(false);
  });

  it('merges gap cards into the REAL grids, badged with their source', async () => {
    // Boulder's live feedback: a separate section felt bolted-on, so gap cards
    // slot into the Album/EP/Single grids and the badge is what marks them.
    localStorage.setItem('discog_gapfill', '1');
    gapFillBody = {
      success: true,
      gaps: { albums: [{ id: 'g1', title: 'Only On Deezer', gap_source: 'deezer', year: 2001 }] },
    };
    renderPage();

    await screen.findByText('Only On Deezer');
    const grid = document.getElementById('albums-grid') as HTMLElement;
    expect(grid.querySelector('.gapfill-card')).not.toBeNull();
    expect(grid.querySelector('.gapfill-source-badge')?.textContent).toBe('Deezer');
    // The base release is still there — gap-fill only ever appends.
    expect(screen.getByText('SAW')).toBeTruthy();
  });

  it('toggling the chip loads and then removes the gap cards', async () => {
    gapFillBody = {
      success: true,
      gaps: { albums: [{ id: 'g1', title: 'Only On Deezer', gap_source: 'deezer' }] },
    };
    renderPage();
    await screen.findByText('SAW');

    fireEvent.click(document.getElementById('gapfill-toggle-btn') as HTMLElement);
    await screen.findByText('Only On Deezer');

    fireEvent.click(document.getElementById('gapfill-toggle-btn') as HTMLElement);
    await waitFor(() => expect(screen.queryByText('Only On Deezer')).toBeNull());
  });
});

describe('the Enhanced view', () => {
  const toggle = (view: string) =>
    fireEvent.click(document.querySelector(`[data-view="${view}"]`) as HTMLElement);

  it('offers the toggle to an admin on a library artist', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(document.querySelector('[data-view="enhanced"]')).not.toBeNull();
  });

  it('does NOT offer it on a source artist', async () => {
    // No DB record to edit; the vanilla showed an empty pane with the
    // discography hidden behind it.
    stubDetail({ success: true, artist: { id: 'sp1', name: 'X' }, discography: {} });
    renderPage();
    await screen.findByText('X');
    expect(document.querySelector('[data-view="enhanced"]')).toBeNull();
  });

  it('fetches nothing until the user actually switches', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(requested.some((u) => u.includes('/enhanced'))).toBe(false);
  });

  it('swaps the discography for the Enhanced container', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(document.querySelector('.discography-sections')).not.toBeNull();

    toggle('enhanced');
    await waitFor(() => expect(document.getElementById('enhanced-view-container')).not.toBeNull());
    expect(document.querySelector('.discography-sections')).toBeNull();
    await screen.findByText('SAW');
  });

  it('hides Similar Artists, which lives outside React', async () => {
    const section = document.createElement('div');
    section.id = 'ad-similar-artists-section';
    document.body.appendChild(section);

    renderPage();
    await screen.findByText('Aphex Twin');
    toggle('enhanced');
    await waitFor(() => expect(section.style.display).toBe('none'));

    toggle('standard');
    await waitFor(() => expect(section.style.display).toBe(''));
    section.remove();
  });

  it('persists the choice per profile', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    toggle('enhanced');
    await waitFor(() =>
      expect(localStorage.getItem('soulsync-library-view-mode:2')).toBe('enhanced'),
    );
  });

  it('does NOT refetch when toggling back and forth', async () => {
    // The payload carries every track of every album.
    renderPage();
    await screen.findByText('Aphex Twin');
    toggle('enhanced');
    await screen.findByText('SAW');

    toggle('standard');
    toggle('enhanced');
    await screen.findByText('SAW');
    expect(requested.filter((u) => u.includes('/enhanced'))).toHaveLength(1);
  });

  it('opens straight into Enhanced when the profile saved that choice', async () => {
    localStorage.setItem('soulsync-library-view-mode:2', 'enhanced');
    renderPage();
    await waitFor(() => expect(document.getElementById('enhanced-view-container')).not.toBeNull());
  });

  it('refetches for a DIFFERENT artist', async () => {
    // One attempt per artist — but a new artist is a new payload.
    localStorage.setItem('soulsync-library-view-mode:2', 'enhanced');
    const first = renderPage('/artist-detail/library/42');
    await screen.findByText('SAW');
    first.unmount();

    renderPage('/artist-detail/library/99');
    await waitFor(() => expect(requested.filter((u) => u.includes('/enhanced'))).toHaveLength(2));
  });

  it('restores Similar Artists when the page UNMOUNTS', async () => {
    const section = document.createElement('div');
    section.id = 'ad-similar-artists-section';
    document.body.appendChild(section);
    localStorage.setItem('soulsync-library-view-mode:2', 'enhanced');

    const view = renderPage();
    await waitFor(() => expect(section.style.display).toBe('none'));
    view.unmount();
    // Otherwise the next page inherits a hidden section it never hid.
    expect(section.style.display).toBe('');
    section.remove();
  });

  it('reports a failed load instead of an empty view', async () => {
    enhancedBody = { success: false, error: 'no library data' };
    renderPage();
    await screen.findByText('Aphex Twin');
    toggle('enhanced');
    // The message is split across text nodes, so match on the element.
    await waitFor(() =>
      expect(document.querySelector('.enhanced-loading')?.textContent).toBe(
        'Failed to load: no library data',
      ),
    );
  });
});

describe('the vanilla page-state bridge', () => {
  /** The shape library.js declares, defaults and all. */
  type VanillaState = {
    currentArtistId: unknown;
    currentArtistName: string | null;
    currentArtistSource: string | null;
    enhancedData: unknown;
    selectedTracks: Set<string>;
  };

  const install = (): VanillaState => {
    const state: VanillaState = {
      currentArtistId: null,
      currentArtistName: null,
      currentArtistSource: null,
      enhancedData: null,
      selectedTracks: new Set<string>(),
    };
    (window as unknown as { artistDetailPageState: VanillaState }).artistDetailPageState = state;
    return state;
  };

  afterEach(() => {
    delete (window as unknown as { artistDetailPageState?: VanillaState }).artistDetailPageState;
  });

  it('populates the artist BEFORE any hero action can be clicked', async () => {
    // Artist Radio, the art picker and the discography modal read these two
    // fields instead of taking arguments; unset, they bail out with
    // "No artist selected" and the buttons do nothing at all.
    const state = install();
    renderPage();

    await screen.findByText('Aphex Twin');
    expect(state.currentArtistId).toBe(42);
    expect(state.currentArtistName).toBe('Aphex Twin');
    expect(state.currentArtistSource).toBe('spotify');
  });

  it('uses the id the PAYLOAD returned, not the one in the url', async () => {
    // The library-upgrade case: clicking a Deezer result for an artist already
    // in the library gets back the library primary key, and the library-only
    // endpoints behind those buttons need that id.
    const state = install();
    stubDetail({
      success: true,
      artist: { id: 77, name: 'Aphex Twin', server_source: 'plex' },
      discography: { albums: [], source: 'spotify' },
    });
    renderPage('/artist-detail/deezer/2481');

    await screen.findByText('Aphex Twin');
    expect(state.currentArtistId).toBe(77);
  });

  it('clears the artist on unmount, so the next page cannot act on it', async () => {
    const state = install();
    const view = renderPage();
    await screen.findByText('Aphex Twin');

    view.unmount();
    expect(state.currentArtistId).toBeNull();
    expect(state.currentArtistName).toBeNull();
  });

  it('hands the Enhanced payload over once the view is open', async () => {
    // Every still-vanilla album and track action reads enhancedData for the
    // artist name and to patch its own copy of the album list.
    const state = install();
    localStorage.setItem('soulsync-library-view-mode:2', 'enhanced');
    renderPage();

    await screen.findByText('SAW');
    expect((state.enhancedData as { albums: unknown[] })?.albums).toHaveLength(1);
  });

  it('takes the Enhanced payload back on unmount', async () => {
    // Otherwise the next artist's page finds the previous artist's albums, and
    // a delete would patch the wrong list.
    const state = install();
    localStorage.setItem('soulsync-library-view-mode:2', 'enhanced');
    const view = renderPage();
    await screen.findByText('SAW');
    expect(state.enhancedData).not.toBeNull();

    view.unmount();
    expect(state.enhancedData).toBeNull();
  });

  it('mirrors the track selection into the SAME Set object', async () => {
    // deleteLibraryTrack deletes from this Set; replacing it would leave those
    // writes landing somewhere nobody reads.
    const state = install();
    const originalSet = state.selectedTracks;
    localStorage.setItem('soulsync-library-view-mode:2', 'enhanced');
    renderPage();

    await screen.findByText('SAW');
    fireEvent.click(document.getElementById('enhanced-album-row-1') as HTMLElement);
    fireEvent.click(document.querySelector('tbody .enhanced-track-checkbox') as HTMLElement);

    await waitFor(() => expect([...state.selectedTracks]).toEqual(['9']));
    expect(state.selectedTracks).toBe(originalSet);
  });

  it('renders fine when library.js is not loaded at all', async () => {
    // The React page must not depend on the vanilla bundle existing.
    renderPage();
    await screen.findByText('Aphex Twin');
  });
});
