import { createMemoryHistory } from '@tanstack/react-router';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { SEARCH_DEBOUNCE_MS } from './-search.helpers';
import { resetBasicSectionBinding } from './-search.use-basic-section';
import { resetPersistedSearch } from './-search.use-controller';

function renderRoute(path: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

/**
 * The vanilla basic-search panel, as index.html ships it.
 *
 * The React page MOVES this element into its own tree; these tests need a real
 * one in the document to move.
 */
function mountVanillaBasicSection() {
  const page = document.createElement('div');
  page.className = 'page';
  page.id = 'search-page';
  page.innerHTML = `
    <div id="basic-search-section" class="search-section">
      <input id="downloads-search-input" />
      <button id="downloads-search-btn">Search</button>
    </div>`;
  document.body.appendChild(page);
  return page;
}

beforeEach(() => {
  resetPersistedSearch();
  resetBasicSectionBinding();
  // Without a bridge the route's beforeLoad cannot ask whether the page is
  // allowed, and redirects to the profile home before rendering anything.
  window.SoulSyncWebShellBridge = createShellBridge();
  server.use(
    http.get('/status', () => HttpResponse.json({ metadata_source: { source: 'spotify' } })),
    http.get('/api/settings/config-status', () =>
      HttpResponse.json({ spotify: { configured: true }, deezer: { configured: true } }),
    ),
  );
});

afterEach(() => {
  cleanup();
  document.querySelectorAll('#search-page').forEach((node) => node.remove());
  delete window.initializeSearch;
  delete window.initializeFilters;
  delete window._searchPageSetQuery;
});

describe('the search route', () => {
  it('renders without the shell’s .page class', async () => {
    // `.page { display: none }` and the shell only adds `.active` for legacy
    // pages, so a React page wearing it renders invisibly — with every test
    // still passing. This is the label-detail trap.
    await renderRoute('/search');
    await screen.findByText('Search');

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
    await screen.findByText('Search');
    expect(document.getElementById('enhanced-main-results-area')).not.toBeNull();
  });

  it('adopts the vanilla basic-search panel and binds its listeners once', async () => {
    // The shell hides #search-page to show a React page, so the panel has to be
    // MOVED into the React tree — and initializeSearch, which used to run from
    // init.js's legacy `case 'search'`, has to be called or its Search button
    // does nothing.
    mountVanillaBasicSection();
    const initializeSearch = vi.fn();
    const initializeFilters = vi.fn();
    window.initializeSearch = initializeSearch;
    window.initializeFilters = initializeFilters;

    const { unmount } = renderRoute('/search');
    await screen.findByText('Search');

    const section = document.getElementById('basic-search-section');
    const reactHost = document.getElementById('webui-react-root') ?? document.body;
    expect(reactHost.contains(section as Node)).toBe(true);
    expect(initializeSearch).toHaveBeenCalledOnce();
    expect(initializeFilters).toHaveBeenCalledOnce();

    // And it goes home again, or basic search stays broken until a reload.
    unmount();
    expect(document.getElementById('search-page')?.contains(section as Node)).toBe(true);
  });

  it('does not re-bind the basic listeners on a second visit', async () => {
    // initializeSearch uses addEventListener with no guard of its own; calling
    // it twice makes every basic search fire twice.
    mountVanillaBasicSection();
    const initializeSearch = vi.fn();
    window.initializeSearch = initializeSearch;

    const first = renderRoute('/search');
    await screen.findByText('Search');
    first.unmount();

    const second = renderRoute('/search');
    await screen.findByText('Search');
    expect(initializeSearch).toHaveBeenCalledOnce();
    second.unmount();
  });

  it('exposes _searchPageSetQuery for the global widget handoff', async () => {
    const { unmount } = renderRoute('/search');
    await screen.findByText('Search');
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
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!
      .set as (v: string) => void;
    act(() => {
      setter.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it('stays shut for a query shorter than two characters', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderRoute('/search');
      await screen.findByText('Search');

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
    await screen.findByText('Search');
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
    await screen.findByText('Search');
    type('aphex twin');

    await waitFor(() => expect(visibleBody()).toBe('results'), { timeout: 3000 });
    expect(screen.getByText('Drukqs')).toBeInTheDocument();
    expect(document.getElementById('enhanced-dropdown')?.className).not.toContain('hidden');
  });

  it('says so when a settled search found nothing', async () => {
    server.use(
      http.post('/api/enhanced-search', () => HttpResponse.json({ spotify_albums: [] })),
      http.post('/api/enhanced-search/library-check', () => HttpResponse.json({})),
    );

    renderRoute('/search');
    await screen.findByText('Search');

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
    await screen.findByText('Search');
    type('aphex twin');
    await screen.findByText('Drukqs');

    act(() => {
      document.body.click();
    });
    await waitFor(() =>
      expect(document.getElementById('enhanced-dropdown')?.className).toContain('hidden'),
    );
  });
});
