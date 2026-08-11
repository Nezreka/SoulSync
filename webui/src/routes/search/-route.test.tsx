import { createMemoryHistory } from '@tanstack/react-router';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { SEARCH_DEBOUNCE_MS } from './-search.helpers';
import { resetPersistedSearch } from './-search.use-controller';

function renderRoute(path: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

/**
 * Wait for the page to be on screen.
 *
 * The subtitle rather than the word "Search": the page title and the basic
 * panel's submit button both read "Search", so matching on it is ambiguous.
 */
const settled = () =>
  screen.findByText('Find artists, albums, and tracks from any metadata source');

beforeEach(() => {
  resetPersistedSearch();
  // Without a bridge the route's beforeLoad cannot ask whether the page is
  // allowed, and redirects to the profile home before rendering anything.
  window.SoulSyncWebShellBridge = createShellBridge();
  server.use(
    http.get('/status', () => HttpResponse.json({ metadata_source: { source: 'spotify' } })),
    // soulseek included on purpose: it needs an slskd URL like any other
    // credentialed source, so leaving it out makes the picker (correctly) send
    // its clicks to Settings instead of switching to basic mode.
    // Basic search asks for its chip row on mount.
    http.get('/api/search/sources', () =>
      HttpResponse.json({
        mode: 'soulseek',
        sources: [{ name: 'soulseek', display_name: 'Soulseek' }],
      }),
    ),
    http.post('/api/search', () => HttpResponse.json({ results: [] })),
    http.get('/api/settings/config-status', () =>
      HttpResponse.json({
        spotify: { configured: true },
        deezer: { configured: true },
        soulseek: { configured: true },
      }),
    ),
  );
});

afterEach(() => {
  cleanup();
  document.querySelectorAll('#search-page').forEach((node) => node.remove());
  delete window._searchPageSetQuery;
});

describe('the search route', () => {
  it('renders without the shell’s .page class', async () => {
    // `.page { display: none }` and the shell only adds `.active` for legacy
    // pages, so a React page wearing it renders invisibly — with every test
    // still passing. This is the label-detail trap.
    await renderRoute('/search');
    await settled();

    const host = document.getElementById('webui-react-root') ?? document.body;
    expect(host.querySelector('.page')).toBeNull();
  });

  it('shows the header and an idle page with no dropdown', async () => {
    await renderRoute('/search');
    await screen.findByText('Find artists, albums, and tracks from any metadata source');

    expect(document.getElementById('enhanced-dropdown')?.className).toContain('hidden');
    expect(document.getElementById('enhanced-search-input')).not.toBeNull();
  });

  it('renders #enhanced-main-results-area, where download bubbles land', async () => {
    // showSearchDownloadBubbles renders into this id and silently returns
    // without it, so every download started from search would draw nowhere.
    await renderRoute('/search');
    await settled();
    expect(document.getElementById('enhanced-main-results-area')).not.toBeNull();
  });

  it('renders the basic-search panel itself', async () => {
    // It used to be the vanilla #basic-search-section, MOVED into the React
    // tree by an adoption hook. React owns it now, so the panel and its
    // controls must exist without any legacy markup in the document.
    const { container } = renderRoute('/search');
    await settled();

    const section = container.querySelector('#basic-search-section') as HTMLElement;
    expect(section).not.toBeNull();
    expect(section.querySelector('#downloads-search-input')).not.toBeNull();
    expect(section.querySelector('#downloads-search-btn')).not.toBeNull();
    expect(section.querySelector('#bs-source-row')).not.toBeNull();
    expect(section.querySelector('#search-results-area')).not.toBeNull();
    // Asserted against `container`, not document.body: the question is whether
    // REACT rendered it, and a body-scoped query answers yes to anything.
  });

  it('shows the basic panel only when Soulseek is the active source', async () => {
    // `.search-section` is display:none until `.active`, so this class IS
    // whether basic search is on screen at all.
    renderRoute('/search');
    await settled();

    const section = document.getElementById('basic-search-section') as HTMLElement;
    const enhanced = document.getElementById('enhanced-search-section') as HTMLElement;
    expect(section.classList.contains('active')).toBe(false);
    expect(enhanced.classList.contains('active')).toBe(true);

    const icon = document.querySelector('#enh-source-row [data-source="soulseek"]');
    act(() => (icon as HTMLButtonElement).click());

    await waitFor(() => expect(section.classList.contains('active')).toBe(true));
    // And exactly one of the two is showing.
    expect(enhanced.classList.contains('active')).toBe(false);
  });

  it('takes the whole panel with it on unmount', async () => {
    // The adoption hook had to put the borrowed node back or basic search
    // stayed broken until a reload. Owning the markup removes that hazard —
    // this pins that nothing is left behind in the document.
    const { unmount } = renderRoute('/search');
    await settled();
    expect(document.getElementById('basic-search-section')).not.toBeNull();

    unmount();
    expect(document.getElementById('basic-search-section')).toBeNull();
  });

  it('exposes _basicDownloadUnmatched for the vanilla matched-download modal', async () => {
    // skipMatching() in wishlist-tools.js calls this; that modal has no way to
    // run a download itself.
    const { unmount } = renderRoute('/search');
    await settled();
    expect(typeof window._basicDownloadUnmatched).toBe('function');

    unmount();
    await waitFor(() => expect(window._basicDownloadUnmatched).toBeUndefined());
  });

  it('exposes _searchPageSetQuery for the global widget handoff', async () => {
    const { unmount } = renderRoute('/search');
    await settled();
    expect(typeof window._searchPageSetQuery).toBe('function');

    unmount();
    // Cleaned up, so a stale page cannot answer for a live one.
    await waitFor(() => expect(window._searchPageSetQuery).toBeUndefined());
  });
});

describe('the dropdown state machine', () => {
  /** Type into the real input the way a keystroke does. */
  function type(value: string) {
    const input = document.getElementById('enhanced-search-input') as HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set as (
      v: string,
    ) => void;
    act(() => {
      setter.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it('stays shut for a query shorter than two characters', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderRoute('/search');
      await settled();

      type('a');
      act(() => vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS + 50));
      expect(document.getElementById('enhanced-dropdown')?.className).toContain('hidden');
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * Which body is SHOWING.
   *
   * All three are always in the DOM and only the `hidden` class differs, so
   * asserting on text alone proves nothing — "No results found" is present even
   * while results are on screen.
   */
  function visibleBody(): string {
    const shown = (id: string) => !document.getElementById(id)?.className.includes('hidden');
    if (shown('enhanced-loading')) return 'loading';
    if (shown('enhanced-empty')) return 'empty';
    if (shown('enhanced-results-container')) return 'results';
    return 'none';
  }

  it('names the source it is searching while the request is in flight', async () => {
    let settle: (() => void) | null = null;
    server.use(
      http.post('/api/enhanced-search', () => {
        return new Promise<Response>((resolve) => {
          settle = () => resolve(HttpResponse.json({ spotify_albums: [] }));
        });
      }),
      http.post('/api/enhanced-search/library-check', () => HttpResponse.json({})),
    );

    renderRoute('/search');
    await settled();
    type('aphex twin');

    // The text names the ACTIVE source rather than a hardcoded "Spotify", and
    // the loading body is the one on screen.
    await screen.findByText('Searching Spotify and your library...', undefined, { timeout: 3000 });
    await waitFor(() => expect(visibleBody()).toBe('loading'));
    act(() => settle?.());
  });

  it('shows the results body once they land', async () => {
    server.use(
      http.post('/api/enhanced-search', () =>
        HttpResponse.json({ spotify_albums: [{ id: 'a1', name: 'Drukqs' }] }),
      ),
      http.post('/api/labels/search', () => HttpResponse.json({ labels: [] })),
      http.post('/api/enhanced-search/library-check', () => HttpResponse.json({})),
    );

    renderRoute('/search');
    await settled();
    type('aphex twin');

    await waitFor(() => expect(visibleBody()).toBe('results'), { timeout: 3000 });
    expect(screen.getAllByText('Drukqs').length).toBeGreaterThan(0);
    expect(document.getElementById('enhanced-dropdown')?.className).not.toContain('hidden');
  });

  it('says so when a settled search found nothing', async () => {
    server.use(
      http.post('/api/enhanced-search', () => HttpResponse.json({ spotify_albums: [] })),
      http.post('/api/enhanced-search/library-check', () => HttpResponse.json({})),
    );

    renderRoute('/search');
    await settled();

    type('nothing at all');
    await waitFor(() => expect(visibleBody()).toBe('empty'), { timeout: 3000 });
  });

  it('closes on an outside click and reopens on the next search', async () => {
    server.use(
      http.post('/api/enhanced-search', () =>
        HttpResponse.json({ spotify_albums: [{ id: 'a1', name: 'Drukqs' }] }),
      ),
      http.post('/api/labels/search', () => HttpResponse.json({ labels: [] })),
      http.post('/api/enhanced-search/library-check', () => HttpResponse.json({})),
    );

    renderRoute('/search');
    await settled();
    type('aphex twin');
    await screen.findAllByText('Drukqs');

    act(() => {
      document.body.click();
    });
    await waitFor(() =>
      expect(document.getElementById('enhanced-dropdown')?.className).toContain('hidden'),
    );
  });
});

describe('the global widget handoff', () => {
  /** Record every basic search the page actually runs. */
  function watchBasicSearches() {
    const queries: string[] = [];
    server.use(
      http.post('/api/search', async ({ request }) => {
        queries.push(((await request.json()) as { query: string }).query);
        return HttpResponse.json({ results: [] });
      }),
    );
    return queries;
  }

  it('runs the basic search ONCE, not once per path into it', async () => {
    // Three ways to trigger it converge here: this sync, the icon click the
    // widget makes next, and the debounce catching up. Only the click should
    // search — the vanilla's equivalent line is a plain state assignment
    // (downloads.js:5729-5731), and every extra call is another slskd search.
    const queries = watchBasicSearches();

    renderRoute('/search');
    await settled();

    act(() => {
      window._searchPageSetQuery?.('aphex twin');
    });
    // The sync alone must not search.
    expect(queries).toEqual([]);

    // Now the widget's icon click, which is the part that does.
    const icon = document.querySelector('#enh-source-row [data-source="soulseek"]');
    await act(async () => {
      (icon as HTMLButtonElement).click();
    });
    await waitFor(() => expect(queries).toEqual(['aphex twin']));

    // And the debounce does not add a second: the sync never touched the
    // enhanced input, so there is nothing pending.
    await act(async () => {
      await new Promise((r) => setTimeout(r, SEARCH_DEBOUNCE_MS + 200));
    });
    expect(queries).toEqual(['aphex twin']);
  });

  it('hands the widget’s query to basic search, not a remembered one', async () => {
    // The page keeps its query across navigation, so without the sync the icon
    // click would search whatever was last looked up here.
    const queries = watchBasicSearches();

    renderRoute('/search');
    await settled();

    act(() => {
      window._searchPageSetQuery?.('from the widget');
    });
    const icon = document.querySelector('#enh-source-row [data-source="soulseek"]');
    await act(async () => {
      (icon as HTMLButtonElement).click();
    });

    await waitFor(() => expect(queries).toEqual(['from the widget']));
    // The input shows what was searched, so the results are not sitting under
    // somebody else's query.
    const basicInput = document.getElementById('downloads-search-input') as HTMLInputElement;
    expect(basicInput.value).toBe('from the widget');
  });

  it('switches to the basic panel without searching when there is no query', async () => {
    // Clicking Soulseek on a page nobody has typed into should show the panel,
    // not scold the user for an empty search.
    const queries = watchBasicSearches();

    renderRoute('/search');
    await settled();

    const icon = document.querySelector('#enh-source-row [data-source="soulseek"]');
    await act(async () => {
      (icon as HTMLButtonElement).click();
    });

    const section = document.getElementById('basic-search-section') as HTMLElement;
    await waitFor(() => expect(section.classList.contains('active')).toBe(true));
    expect(queries).toEqual([]);
  });
});

describe('where a result card points', () => {
  function type(value: string) {
    const input = document.getElementById('enhanced-search-input') as HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set as (
      v: string,
    ) => void;
    act(() => {
      setter.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it('links a library artist to /artist-detail/library/<id> and a found one to its source', async () => {
    // The href IS the feature. The first version guessed `/artist-detail/<id>`,
    // which matches no route and resolves to nothing — clicking an artist did
    // nothing at all, and no test noticed.
    server.use(
      http.post('/api/enhanced-search', () =>
        HttpResponse.json({
          db_artists: [{ id: 7, name: 'Owned Artist' }],
          spotify_artists: [{ id: 'sp1', name: 'Found Artist', source: 'spotify' }],
        }),
      ),
      http.post('/api/labels/search', () => HttpResponse.json({ labels: [] })),
      http.post('/api/enhanced-search/library-check', () => HttpResponse.json({})),
      http.get('/api/artist/:id/image', () => HttpResponse.json({ success: false })),
    );

    renderRoute('/search');
    await settled();
    type('aphex twin');

    // Twice on screen — the spotlight and the card — both linking home.
    const owned = await screen.findAllByText('Owned Artist', undefined, { timeout: 3000 });
    for (const el of owned) {
      expect(el.closest('a')?.getAttribute('href')).toBe('/artist-detail/library/7');
    }

    const found = screen.getByText('Found Artist');
    expect(found.closest('a')?.getAttribute('href')).toBe(
      '/artist-detail/spotify/sp1?name=Found%20Artist',
    );
  });

  it('links a label to /label-detail/<id>', async () => {
    server.use(
      http.post('/api/enhanced-search', () =>
        HttpResponse.json({ spotify_albums: [{ id: 'a1', name: 'Drukqs', artist: 'Aphex Twin' }] }),
      ),
      http.post('/api/labels/search', () =>
        HttpResponse.json({ labels: [{ id: 'l1', name: 'Warp' }] }),
      ),
      http.post('/api/enhanced-search/library-check', () => HttpResponse.json({})),
    );

    renderRoute('/search');
    await settled();
    type('warp');

    const label = await screen.findByText('Warp', undefined, { timeout: 3000 });
    expect(label.closest('a')?.getAttribute('href')).toBe('/label-detail/l1?name=Warp');
  });
});
