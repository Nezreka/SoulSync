import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { LibraryArtist } from '../-library.types';

/**
 * Driven through the REAL router and the real manifest, which now hands
 * /library to React.
 *
 * The page cannot render standalone (useProfile reads the root route context),
 * and going through the router also exercises validateSearch, the loader and
 * the URL-driven filter state rather than stubs of them.
 */

function artist(over: Partial<LibraryArtist> & { id: number }): LibraryArtist {
  return { name: `Artist ${over.id}`, ...over };
}

let requested: string[] = [];
/** Request bodies, captured BEFORE ky consumes them — a Request cannot be
 *  cloned once its body has been read ("TypeError: unusable"). */
let sent: { url: string; body: unknown }[] = [];

async function record(input: RequestInfo | URL): Promise<string> {
  const url = input instanceof Request ? input.url : String(input);
  requested.push(url);
  if (input instanceof Request && input.body) {
    sent.push({ url, body: await input.clone().json() });
  }
  return url;
}

function stubFetch(
  artists: LibraryArtist[],
  total = artists.length,
  pages = 1,
  /** #1202 banner payload; `fail` makes the request reject. */
  unmatched: { count: number; artist_id: string | number | null } | 'fail' | null = null,
) {
  requested = [];
  sent = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = await record(input);
      if (url.includes('/api/library/unmatched-summary')) {
        if (unmatched === 'fail') return new Response('nope', { status: 500 });
        return new Response(JSON.stringify({ success: true, ...(unmatched ?? { count: 0, artist_id: null }) }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/api/watchlist/')) {
        return new Response(JSON.stringify({ success: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      const body = url.includes('/api/library/artists')
        ? {
            success: true,
            artists,
            pagination: {
              page: Number(new URL(url, 'http://x').searchParams.get('page') ?? 1),
              limit: 75,
              total_count: total,
              total_pages: pages,
              has_prev: Number(new URL(url, 'http://x').searchParams.get('page') ?? 1) > 1,
              has_next: Number(new URL(url, 'http://x').searchParams.get('page') ?? 1) < pages,
            },
          }
        : {};
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
  return {
    router,
    queryClient,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

const libraryCalls = () => requested.filter((u) => u.includes('/api/library/artists'));
const lastQuery = () => new URL(libraryCalls().at(-1)!, 'http://x').searchParams;

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showLibraryDownloadsSection = vi.fn();
  window._handoffLibrarySearchToEnhancedSearch = vi.fn();
  window.showToast = vi.fn();
  window.updateWatchlistCount = vi.fn();
  window.currentMusicSourceName = 'spotify';
  stubFetch([artist({ id: 1, name: 'Aphex Twin', track_count: 12 })]);
});
afterEach(() => {
  // Unconditional: a test that fails before its inline restore would otherwise
  // leak fake timers into every later test, and they all hang in waitFor.
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
  delete window.showLibraryDownloadsSection;
});

describe('LibraryPage rendering', () => {
  it('renders the artist grid with the vanilla classes', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(document.querySelector('.library-artists-grid')).not.toBeNull();
    expect(document.querySelectorAll('.library-artist-card')).toHaveLength(1);
    expect(document.querySelector('.library-artist-stat')?.textContent).toBe('12 tracks');
  });

  it('shows the artist count from pagination, not the page length', async () => {
    // A 75-per-page library must report its TOTAL, not the 75 on screen.
    stubFetch([artist({ id: 1 })], 4213, 57);
    renderPage();
    await waitFor(() => expect(document.querySelector('.stat-number')?.textContent).toBe('4213'));
  });

  it('calls the vanilla downloads-bubble renderer on mount', async () => {
    // It is bound to module state in core.js and cannot move into React.
    renderPage();
    await waitFor(() => expect(window.showLibraryDownloadsSection).toHaveBeenCalled());
    expect(document.querySelector('[data-library-downloads-host]')).not.toBeNull();
  });

  it('the header Radio button hands off to the player global', async () => {
    // Library Radio lives in media-player.js (it owns the queue + radio
    // mode); the page only provides the entry point.
    window.startLibraryRadio = vi.fn();
    renderPage();
    await screen.findByText('Aphex Twin');
    fireEvent.click(document.querySelector('.library-radio-btn')!);
    expect(window.startLibraryRadio).toHaveBeenCalled();
    delete window.startLibraryRadio;
  });
});

describe('LibraryPage filters', () => {
  it('sends every filter to the API', async () => {
    renderPage('/library?q=aphex&letter=a&page=2&watchlist=watched&source=spotify');
    await waitFor(() => expect(libraryCalls().length).toBeGreaterThan(0));
    const p = lastQuery();
    expect(p.get('search')).toBe('aphex');
    expect(p.get('letter')).toBe('a');
    expect(p.get('page')).toBe('2');
    expect(p.get('watchlist')).toBe('watched');
    expect(p.get('source_filter')).toBe('spotify');
    expect(p.get('limit')).toBe('75');
  });

  it('omits source_filter when no source is chosen', async () => {
    // The vanilla loader only set() it when non-empty; sending an empty one
    // would also fragment the query cache.
    renderPage('/library');
    await waitFor(() => expect(libraryCalls().length).toBeGreaterThan(0));
    expect(lastQuery().has('source_filter')).toBe(false);
  });

  it('debounces typing for 300ms, then resets to page 1', async () => {
    vi.useFakeTimers();
    // Entering on page 7 also proves the TYPING path resets the page — it
    // navigates itself rather than going through setSearch.
    const { router } = renderPage('/library?page=7');
    await vi.advanceTimersByTimeAsync(0);
    const before = libraryCalls().length;
    const input = document.querySelector('.library-search-input') as HTMLInputElement;

    fireEvent.change(input, { target: { value: 'a' } });
    fireEvent.change(input, { target: { value: 'ap' } });
    fireEvent.change(input, { target: { value: 'aph' } });
    expect(libraryCalls().length).toBe(before);

    // Still inside the window: a shorter timer would have fired by now, so
    // this is what pins the 300ms rather than merely "is deferred at all".
    await vi.advanceTimersByTimeAsync(250);
    expect(libraryCalls().length).toBe(before);

    await vi.advanceTimersByTimeAsync(100);
    vi.useRealTimers();
    await waitFor(() => expect(lastQuery().get('search')).toBe('aph'));
    expect(router.state.location.search).toMatchObject({ page: 1 });
  });

  it('resets to page 1 whenever a filter changes', async () => {
    // Page 7 of the old result set is meaningless against a new one.
    const { router } = renderPage('/library?page=7');
    await waitFor(() => expect(libraryCalls().length).toBeGreaterThan(0));

    fireEvent.click(screen.getByText('Watched'));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ page: 1 }));
  });

  it('clears the search on Escape', async () => {
    const { router } = renderPage('/library?q=aphex');
    // waitFor resolves on the first call that does not THROW, and returning
    // null does not throw — so the assertion has to live inside it.
    const input = (await screen.findByLabelText('Filter your library')) as HTMLInputElement;
    expect(input.value).toBe('aphex');

    fireEvent.keyDown(input, { key: 'Escape' });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: '' }));
  });

  it('marks the active filter and letter', async () => {
    renderPage('/library?watchlist=unwatched&letter=c');
    await waitFor(() =>
      expect(document.querySelector('.watchlist-filter-btn.active')?.textContent).toBe('Unwatched'),
    );
    expect(document.querySelector('.alphabet-btn.active')?.textContent).toBe('C');
  });

  it('offers Watch All Unwatched only while filtered to unwatched', async () => {
    const { unmount } = renderPage('/library?watchlist=unwatched');
    await waitFor(() =>
      expect(document.querySelector('.library-watchlist-all-btn.hidden')).toBeNull(),
    );
    unmount();

    renderPage('/library?watchlist=all');
    await waitFor(() =>
      expect(document.querySelector('.library-watchlist-all-btn.hidden')).not.toBeNull(),
    );
  });
});

describe('LibraryPage empty state', () => {
  it('offers the search-online CTA when a query found nothing', async () => {
    stubFetch([], 0, 0);
    renderPage('/library?q=nonesuch');
    await screen.findByText(/isn't in your library/);
    fireEvent.click(document.querySelector('.library-empty-search-cta')!);
    expect(window._handoffLibrarySearchToEnhancedSearch).toHaveBeenCalledWith('nonesuch');
  });

  it('shows the generic empty copy with no query', async () => {
    stubFetch([], 0, 0);
    renderPage('/library');
    await screen.findByText('No artists found');
    // No CTA to hand off — there is nothing to search for.
    expect(document.querySelector('.library-empty-search-cta')).toBeNull();
  });
});

describe('LibraryPage watchlist from a card', () => {
  const posted = () => sent.find((c) => c.url.includes('/api/watchlist/'));

  it('adds the artist with the SOURCE-matched id, then flips the badge', async () => {
    window.currentMusicSourceName = 'spotify';
    stubFetch([
      artist({ id: 1, name: 'Aphex Twin', spotify_artist_id: 'sp', itunes_artist_id: 9 }),
    ]);
    renderPage();
    await screen.findByText('Aphex Twin');

    fireEvent.click(document.querySelector('.watch-card-icon')!);
    await waitFor(() => expect(posted()).toBeDefined());

    expect(posted()!.url).toContain('/api/watchlist/add');
    expect(posted()!.body).toEqual({ artist_id: 'sp', artist_name: 'Aphex Twin' });
    await waitFor(() =>
      expect(document.querySelector('.watch-icon-label')?.textContent).toBe('Watching'),
    );
    expect(window.showToast).toHaveBeenCalledWith('Added Aphex Twin to watchlist', 'success');
    expect(window.updateWatchlistCount).toHaveBeenCalled();
  });

  it('keeps the pending badge on the card that is actually waiting', async () => {
    // Two adds in flight at once: the mutation's own `variables` hold only the
    // latest, so the first card must not lose its "...".
    let release: (() => void) | undefined;
    const gate = new Promise<void>((r) => (release = r));
    requested = [];
    sent = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = await record(input);
        if (url.includes('/api/watchlist/')) await gate;
        const body = url.includes('/api/watchlist/')
          ? { success: true }
          : {
              success: true,
              artists: [
                artist({ id: 1, name: 'Aphex Twin', spotify_artist_id: 'sp1' }),
                artist({ id: 2, name: 'Boards of Canada', spotify_artist_id: 'sp2' }),
              ],
              pagination: {
                page: 1,
                limit: 75,
                total_count: 2,
                total_pages: 1,
                has_prev: false,
                has_next: false,
              },
            };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
    renderPage();
    await screen.findByText('Boards of Canada');

    const badges = document.querySelectorAll('.watch-card-icon');
    fireEvent.click(badges[0]);
    fireEvent.click(badges[1]);

    await waitFor(() =>
      expect([...document.querySelectorAll('.watch-icon-label')].map((n) => n.textContent)).toEqual(
        ['...', '...'],
      ),
    );
    release?.();
  });

  it('prefers the iTunes id when iTunes is the active source', async () => {
    window.currentMusicSourceName = 'iTunes';
    stubFetch([
      artist({ id: 1, name: 'Aphex Twin', spotify_artist_id: 'sp', itunes_artist_id: 9 }),
    ]);
    renderPage();
    await screen.findByText('Aphex Twin');

    fireEvent.click(document.querySelector('.watch-card-icon')!);
    await waitFor(() => expect(posted()).toBeDefined());
    expect(posted()!.body).toMatchObject({ artist_id: '9' });
  });

  it('toasts the reason and leaves the badge unwatched when the server refuses', async () => {
    window.currentMusicSourceName = 'spotify';
    requested = [];
    sent = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = await record(input);
        const body = url.includes('/api/watchlist/')
          ? { success: false, error: 'already watching' }
          : {
              success: true,
              artists: [artist({ id: 1, name: 'Aphex Twin', spotify_artist_id: 'sp' })],
              pagination: {
                page: 1,
                limit: 75,
                total_count: 1,
                total_pages: 1,
                has_prev: false,
                has_next: false,
              },
            };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
    renderPage();
    await screen.findByText('Aphex Twin');

    fireEvent.click(document.querySelector('.watch-card-icon')!);
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Error: already watching', 'error'),
    );
    expect(document.querySelector('.watch-icon-label')?.textContent).toBe('Watch');
  });
});

describe('LibraryPage vanilla refresh seam', () => {
  it('refetches when vanilla announces the list changed', async () => {
    // "Watch All Unwatched" is a vanilla modal; it used to refresh by calling
    // loadLibraryArtists(), which no-ops now that React owns the page.
    renderPage();
    await screen.findByText('Aphex Twin');
    const before = libraryCalls().length;

    window.dispatchEvent(new CustomEvent('ss:library-changed'));

    await waitFor(() => expect(libraryCalls().length).toBeGreaterThan(before));
  });

  it('stops listening once the page unmounts', async () => {
    // Asserted on the registration itself: after unmount the query has no
    // observers, so a leaked listener would invalidate silently and a
    // fetch-count check would pass either way.
    const add = vi.spyOn(window, 'addEventListener');
    const remove = vi.spyOn(window, 'removeEventListener');
    const { unmount } = renderPage();
    await screen.findByText('Aphex Twin');

    const handler = add.mock.calls.find(([type]) => type === 'ss:library-changed')?.[1];
    expect(handler).toBeDefined();

    unmount();
    expect(
      remove.mock.calls.some(([type, fn]) => type === 'ss:library-changed' && fn === handler),
    ).toBe(true);
    add.mockRestore();
    remove.mockRestore();
  });
});

describe('LibraryPage failure', () => {
  function stubFailure(body: unknown, status = 200) {
    requested = [];
    sent = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        await record(input);
        return new Response(JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
  }

  it('toasts the vanilla fixed string when the server reports failure', async () => {
    // Not the server's reason — vanilla only used that as the thrown message.
    stubFailure({ success: false, error: 'db locked' });
    renderPage();
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Failed to load artists', 'error'),
    );
  });

  it('toasts on a transport failure too', async () => {
    stubFailure({ error: 'boom' }, 500);
    renderPage();
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Failed to load artists', 'error'),
    );
  });

  it('keeps the honest empty copy after a failure, even with a query', async () => {
    // Vanilla claimed "X isn't in your library" here; the library was never read.
    stubFailure({ success: false, error: 'db locked' });
    renderPage('/library?q=aphex');
    await screen.findByText('No artists found');
    expect(document.querySelector('.library-empty-search-cta')).toBeNull();
  });

  it('stops showing the loading spinner after a failure', async () => {
    stubFailure({ success: false, error: 'db locked' });
    renderPage();
    await screen.findByText('No artists found');
    expect(document.querySelector('.library-loading')).toBeNull();
  });
});

describe('LibraryPage pagination', () => {
  it('hides pagination for a single page', async () => {
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(document.querySelector('.library-pagination')).toBeNull();
  });

  it('pages forward and back, and disables the ends', async () => {
    stubFetch([artist({ id: 1 })], 150, 2);
    const { router } = renderPage('/library');
    await screen.findByText(/Page 1 of 2/);
    expect(
      (document.querySelector('#prev-page-btn, .pagination-btn') as HTMLButtonElement).disabled,
    ).toBe(true);

    fireEvent.click(screen.getByText('Next →'));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ page: 2 }));
  });
});

describe('LibraryPage unmatched-imports banner (#1202)', () => {
  const banner = () => document.querySelector('.library-unmatched-banner');

  /**
   * Absence only proves something once the query has actually settled.
   * Asserting straight after the grid renders passes while the request is
   * still in flight, which made these pass against a banner with no guard
   * at all.
   */
  const settled = async (queryClient: ReturnType<typeof createTestQueryClient>) => {
    await waitFor(() =>
      expect(queryClient.getQueryState(['library', 'unmatched'])?.status).not.toBe('pending'),
    );
  };

  it('names the count and links straight at the Unknown Artist row', async () => {
    stubFetch([artist({ id: 1, name: 'Aphex Twin' })], 1, 1, { count: 7, artist_id: 'u1' });
    renderPage();
    await waitFor(() => expect(banner()).not.toBeNull());
    expect(banner()!.textContent).toContain('7 tracks imported without a match');
    // the whole point is getting to where re-identify lives
    expect(document.querySelector('.library-unmatched-btn')?.getAttribute('href')).toBe(
      '/artist-detail/library/u1',
    );
  });

  it('says track, not tracks, for a single one', async () => {
    stubFetch([artist({ id: 1 })], 1, 1, { count: 1, artist_id: 'u1' });
    renderPage();
    await waitFor(() => expect(banner()).not.toBeNull());
    expect(banner()!.textContent).toContain('1 track imported');
    expect(banner()!.textContent).not.toContain('1 tracks');
  });

  it('stays away entirely on a clean library', async () => {
    stubFetch([artist({ id: 1, name: 'Aphex Twin' })], 1, 1, { count: 0, artist_id: null });
    const { queryClient } = renderPage();
    await screen.findByText('Aphex Twin');
    await settled(queryClient);
    expect(banner()).toBeNull();
  });

  it('stays away on a zero count even if a row id comes back with it', async () => {
    // The backend nulls the id whenever the count is 0, so this combination
    // should never arrive — but "0 tracks imported without a match" is the
    // worst thing this banner could say, so the count guard stands on its own.
    stubFetch([artist({ id: 1, name: 'Aphex Twin' })], 1, 1, { count: 0, artist_id: 'u1' });
    const { queryClient } = renderPage();
    await screen.findByText('Aphex Twin');
    await settled(queryClient);
    expect(banner()).toBeNull();
  });

  it('stays away when the count exists but the row id does not', async () => {
    // Without an id there is nowhere to send them, and a banner that cannot
    // answer "show me" is worse than no banner.
    stubFetch([artist({ id: 1, name: 'Aphex Twin' })], 1, 1, { count: 4, artist_id: null });
    const { queryClient } = renderPage();
    await screen.findByText('Aphex Twin');
    await settled(queryClient);
    expect(banner()).toBeNull();
  });

  it('stays away when the request fails, and the grid still renders', async () => {
    stubFetch([artist({ id: 1, name: 'Aphex Twin' })], 1, 1, 'fail');
    const { queryClient } = renderPage();
    await screen.findByText('Aphex Twin');
    await settled(queryClient);
    expect(banner()).toBeNull();
  });
});
