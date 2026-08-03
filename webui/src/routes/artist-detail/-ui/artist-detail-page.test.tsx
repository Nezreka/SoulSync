import { createMemoryHistory } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

function stubDetail(
  body: unknown,
  extra?: (url: string, requestBody: Record<string, unknown> | null) => unknown,
  status = 200,
) {
  requested = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      requested.push(url);
      const extraResult = extra?.(url, init?.body ? JSON.parse(String(init.body)) : null);
      if (extraResult) {
        return new Response(JSON.stringify(extraResult), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
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
  return {
    router,
    history,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
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
    // Local now (initializeLibraryWatchlistButton's port): the hero checks the
    // status itself and reflects it on the button.
    stubDetail(
      {
        ...LIBRARY,
        spotify_artist: { spotify_artist_id: 'sp9', spotify_artist_name: 'Aphex Twin' },
      },
      (url, body) =>
        url === '/api/watchlist/check' && body?.artist_id === 'sp9'
          ? { success: true, is_watching: true }
          : null,
    );
    renderPage();
    await waitFor(() =>
      expect(document.querySelector('.watchlist-text')?.textContent).toBe('Watching...'),
    );
    expect(
      document.getElementById('library-artist-watchlist-btn')?.classList.contains('watching'),
    ).toBe(true);
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

/** Re-queried each time: React replaces the node when the filters change. */
function liveToggle(): HTMLElement {
  return document.querySelector('[data-filter="content"][data-value="live"]') as HTMLElement;
}

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
    //
    // Waited for, not asserted straight after the card: the label reads
    // filters.mbDeclutter, which an EFFECT sets once the discography's source
    // is known — a render later than the one that paints the card. Asserting
    // on the findByText tick passed locally every time and went red on CI,
    // which is precisely the window between those two commits.
    await waitFor(() => expect(liveToggle().textContent).toBe('Non-Studio'));
    expect(liveToggle().className).not.toContain('active');
  });

  it('leaves other sources with the toggle on and labelled Live', async () => {
    renderPage();
    await screen.findByText('SAW');
    // 'Live' is also the pre-effect default, so a bare assertion here would
    // pass even if the declutter never ran. Settle first, then assert it
    // STAYED — that is the actual claim.
    await waitFor(() => expect(document.body.dataset.artistSource).toBe('library'));
    expect(liveToggle().textContent).toBe('Live');
    expect(liveToggle().className).toContain('active');
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
    await waitFor(() =>
      expect((state.enhancedData as { albums: unknown[] })?.albums).toHaveLength(1),
    );
  });

  it('takes the Enhanced payload back on unmount', async () => {
    // Otherwise the next artist's page finds the previous artist's albums, and
    // a delete would patch the wrong list.
    const state = install();
    localStorage.setItem('soulsync-library-view-mode:2', 'enhanced');
    const view = renderPage();
    await screen.findByText('SAW');
    // waitFor, not a bare assertion: findByText resolves on the committed
    // render, and the sync runs in a passive effect — which can land a tick
    // later. Asserting synchronously made this flake about once in twenty runs.
    await waitFor(() => expect(state.enhancedData).not.toBeNull());

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

describe('the page header', () => {
  it('renders the Back button — it lives in markup the React host replaces', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    const back = document.getElementById('artist-detail-back-btn') as HTMLElement;
    expect(back).not.toBeNull();
    expect(back.textContent).toBe('← Back');
    expect(back.closest('.page-header')).not.toBeNull();
  });

  it('is there while loading and on failure too', async () => {
    stubDetail({ success: false, error: 'nope' });
    renderPage();
    await screen.findByText('Failed to load artist details');
    expect(document.getElementById('artist-detail-back-btn')).not.toBeNull();
  });

  it('goes back through history when there is history to go back to', async () => {
    const length = vi.spyOn(window.history, 'length', 'get').mockReturnValue(3);
    const { router } = renderPage();
    await screen.findByText('Aphex Twin');
    const back = vi.spyOn(router.history, 'back');

    fireEvent.click(document.getElementById('artist-detail-back-btn') as HTMLElement);
    expect(back).toHaveBeenCalled();
    back.mockRestore();
    length.mockRestore();
    // Let the remaining in-flight request settle inside act(), so its state
    // update does not land after the test returns (React's act warning).
    await act(async () => {});
  });

  it('falls back to the Library on a COLD load, where back would leave SoulSync', async () => {
    const length = vi.spyOn(window.history, 'length', 'get').mockReturnValue(1);
    const { router, history } = renderPage();
    await screen.findByText('Aphex Twin');
    const back = vi.spyOn(router.history, 'back');

    fireEvent.click(document.getElementById('artist-detail-back-btn') as HTMLElement);
    expect(back).not.toHaveBeenCalled();
    await waitFor(() => expect(history.location.pathname).toBe('/library'));
    back.mockRestore();
    length.mockRestore();
  });
});

describe('overlays must escape the hero', () => {
  // .artist-hero-section sets backdrop-filter, which makes it the CONTAINING
  // BLOCK for any position:fixed descendant. An overlay rendered inside it has
  // its inset:0 clamped to the hero box — wrong place, cut off at the top, and
  // a click anywhere else never reaches the backdrop so it cannot be closed.
  const escapesHero = (el: Element | null) => {
    expect(el).not.toBeNull();
    expect(el?.closest('.artist-hero-section')).toBeNull();
    expect(el?.parentElement).toBe(document.body);
  };

  it('portals the DB Record modal to the body', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    fireEvent.click(document.getElementById('artist-db-record-btn') as HTMLElement);

    await waitFor(() => expect(document.getElementById('artist-record-overlay')).not.toBeNull());
    escapesHero(document.getElementById('artist-record-overlay'));
  });

  it('closes the DB Record modal on a backdrop click', async () => {
    // Only reachable once the overlay actually covers the viewport.
    renderPage();
    await screen.findByText('Aphex Twin');
    fireEvent.click(document.getElementById('artist-db-record-btn') as HTMLElement);
    await waitFor(() => expect(document.getElementById('artist-record-overlay')).not.toBeNull());

    fireEvent.click(document.getElementById('artist-record-overlay') as HTMLElement);
    await waitFor(() => expect(document.getElementById('artist-record-overlay')).toBeNull());
  });

  it('portals the bulk bar to the body', async () => {
    localStorage.setItem('soulsync-library-view-mode:2', 'enhanced');
    renderPage();
    await screen.findByText('SAW');
    escapesHero(document.getElementById('enhanced-bulk-bar'));
  });
});

describe('the filter bar actually filters the page', () => {
  const FULL = {
    success: true,
    artist: { id: 42, name: 'Aphex Twin', server_source: 'plex' },
    discography: {
      albums: [
        { id: 1, title: 'SAW', owned: true },
        { id: 2, title: 'Live At Wembley', owned: false },
      ],
      eps: [{ id: 3, title: 'Digeridoo', owned: false }],
      singles: [{ id: 4, title: 'On', owned: true }],
      source: 'spotify',
    },
  };

  const shown = () =>
    [...document.querySelectorAll('.release-card')]
      .filter((c) => (c as HTMLElement).style.display !== 'none')
      .map((c) => c.getAttribute('data-album-name'));

  it('drops a whole category when its chip is switched off', async () => {
    stubDetail(FULL);
    renderPage();
    await screen.findByText('SAW');
    expect(shown()).toContain('Digeridoo');

    fireEvent.click(
      document.querySelector('[data-filter="category"][data-value="eps"]') as HTMLElement,
    );
    await waitFor(() => expect(shown()).not.toContain('Digeridoo'));
    // The others are untouched — category chips are independent toggles.
    expect(shown()).toContain('SAW');
  });

  it('filters by ownership as a single-select', async () => {
    stubDetail(FULL);
    renderPage();
    await screen.findByText('SAW');

    fireEvent.click(
      document.querySelector('[data-filter="ownership"][data-value="missing"]') as HTMLElement,
    );
    await waitFor(() => expect(shown()).not.toContain('SAW'));
    expect(shown()).toContain('Digeridoo');

    fireEvent.click(
      document.querySelector('[data-filter="ownership"][data-value="owned"]') as HTMLElement,
    );
    await waitFor(() => expect(shown()).toContain('SAW'));
    expect(shown()).not.toContain('Digeridoo');
  });

  it('excludes live releases via the content chip', async () => {
    stubDetail(FULL);
    renderPage();
    await screen.findByText('SAW');
    expect(shown()).toContain('Live At Wembley');

    fireEvent.click(
      document.querySelector('[data-filter="content"][data-value="live"]') as HTMLElement,
    );
    await waitFor(() => expect(shown()).not.toContain('Live At Wembley'));
  });

  it('+ Other sources loads gap cards, and switching it off removes them', async () => {
    stubDetail(FULL);
    gapFillBody = {
      success: true,
      gaps: { albums: [{ id: 'g1', title: 'Only On Deezer', gap_source: 'deezer', year: 2001 }] },
    };
    renderPage();
    await screen.findByText('SAW');

    const chip = document.getElementById('gapfill-toggle-btn') as HTMLElement;
    expect(chip.className).not.toContain('active');

    fireEvent.click(chip);
    await screen.findByText('Only On Deezer');
    expect(chip.className).toContain('active');
    expect(document.querySelector('.gapfill-source-badge')?.textContent).toBe('Deezer');

    fireEvent.click(chip);
    await waitFor(() => expect(screen.queryByText('Only On Deezer')).toBeNull());
    expect(chip.className).not.toContain('active');
  });

  it('the ownership filter applies to gap cards too', async () => {
    // They arrive owned:false, so a Missing filter must keep them and an Owned
    // filter must hide them.
    stubDetail(FULL);
    gapFillBody = {
      success: true,
      gaps: { albums: [{ id: 'g1', title: 'Only On Deezer', gap_source: 'deezer' }] },
    };
    localStorage.setItem('discog_gapfill', '1');
    renderPage();
    await screen.findByText('Only On Deezer');

    fireEvent.click(
      document.querySelector('[data-filter="ownership"][data-value="owned"]') as HTMLElement,
    );
    await waitFor(() => expect(shown()).not.toContain('Only On Deezer'));
  });
});

describe('Similar Artists', () => {
  it('renders the section the vanilla loader fills', async () => {
    // Its markup lived inside #artist-detail-page, which the React host
    // replaces — so the whole section disappeared and loadSimilarArtists,
    // which resolves four elements by id, silently did nothing.
    renderPage();
    await screen.findByText('Aphex Twin');

    expect(document.getElementById('ad-similar-artists-section')).not.toBeNull();
    expect(document.getElementById('ad-similar-artists-loading')).not.toBeNull();
    expect(document.getElementById('ad-similar-artists-error')).not.toBeNull();
    expect(document.getElementById('ad-similar-artists-bubbles-container')).not.toBeNull();
    expect(screen.getByText('Similar Artists')).toBeTruthy();
  });

  it('has the section in the DOM by the time the loader runs', async () => {
    // Ordering is the whole difference between a populated section and an
    // empty one.
    let sectionAtCallTime: Element | null = null;
    window.loadSimilarArtists = vi.fn(() => {
      sectionAtCallTime = document.getElementById('ad-similar-artists-section');
    });

    renderPage();
    await waitFor(() => expect(window.loadSimilarArtists).toHaveBeenCalled());
    expect(sectionAtCallTime).not.toBeNull();
  });

  it('leaves bubbles the vanilla inserted alone across a re-render', async () => {
    // React must not reconcile away nodes it did not create.
    renderPage();
    await screen.findByText('Aphex Twin');
    const container = document.getElementById(
      'ad-similar-artists-bubbles-container',
    ) as HTMLElement;
    container.innerHTML = '<a class="similar-artist-bubble">Squarepusher</a>';

    fireEvent.click(
      document.querySelector('[data-filter="category"][data-value="eps"]') as HTMLElement,
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector('.similar-artist-bubble')).not.toBeNull();
  });
});

describe('the Back button label', () => {
  type W = { artistDetailLabelStack?: { type: string; pageId?: string; name?: string }[] };

  afterEach(() => {
    delete (window as W).artistDetailLabelStack;
  });

  it('names the page you arrived from', async () => {
    // navigateToArtistDetail pushed this when you came from a vanilla page.
    (window as W).artistDetailLabelStack = [{ type: 'page', pageId: 'search' }];
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(document.getElementById('artist-detail-back-btn')?.textContent).toBe('← Back to Search');
  });

  it('is a plain Back on a cold load', async () => {
    (window as W).artistDetailLabelStack = [];
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(document.getElementById('artist-detail-back-btn')?.textContent).toBe('← Back');
  });
});

describe('scroll position', () => {
  // body is overflow:hidden, so .main-content is the real scroller — the
  // window never moves and a window.scrollTo would be a silent no-op.
  const withMainContent = () => {
    const main = document.createElement('div');
    main.className = 'main-content';
    document.body.appendChild(main);
    Object.defineProperty(main, 'scrollTop', { value: 0, writable: true });
    main.scrollTop = 900;
    return main;
  };

  // Swept centrally, not per test: scrollArtistDetailToTop takes the FIRST
  // .main-content, so one container left behind by a failing test silently
  // makes every later test in this block assert against the wrong element.
  afterEach(() => {
    document.querySelectorAll('.main-content').forEach((el) => el.remove());
  });

  it('opens the artist at the top of the scroll container', async () => {
    const main = withMainContent();
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(main.scrollTop).toBe(0);
    main.remove();
  });

  it('resets again when the route moves to another artist', async () => {
    const main = withMainContent();
    const { router } = renderPage('/artist-detail/library/42');
    await screen.findByText('Aphex Twin');

    main.scrollTop = 1200;
    // The navigate itself has to be inside act(): the router commits the new
    // match -- and the page starts the second artist's fetches -- as it
    // resolves, and those updates are React state.
    await act(async () => {
      await router.navigate({
        to: '/artist-detail/$source/$id',
        params: { source: 'library', id: '99' },
      });
      await new Promise((r) => setTimeout(r, 30));
    });
    await waitFor(() => expect(main.scrollTop).toBe(0));
    main.remove();
  });
});
