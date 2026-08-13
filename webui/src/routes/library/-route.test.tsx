import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * /library is LIVE — the manifest hands it to React.
 *
 * These were the dormant-route tests; they were written to fail the moment the
 * manifest flipped, and this is that rewrite. The point of the suite is
 * unchanged: the route must not hand the page back to vanilla, validateSearch
 * must survive whatever is in the URL, and the permission gate still applies.
 *
 * Unlike library-page.test.tsx this file does NOT mock the manifest — the flip
 * itself is what it is checking.
 */

function renderRoute(entries = ['/library']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: entries });
  const router = createAppRouter({ history, queryClient });
  return { router, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

let requested: string[] = [];

describe('library route (live)', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showLibraryDownloadsSection = vi.fn();
    requested = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        requested.push(url);
        const body = url.includes('/api/library/artists')
          ? {
              success: true,
              artists: [{ id: 1, name: 'Aphex Twin' }],
              pagination: {
                page: 1,
                limit: 75,
                total_count: 1,
                total_pages: 1,
                has_prev: false,
                has_next: false,
              },
            }
          : {};
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
    delete window.showLibraryDownloadsSection;
  });

  it('is owned by React', () => {
    expect(getShellRouteByPageId('library')?.kind).toBe('react');
  });

  it('renders the React page instead of handing /library to vanilla', async () => {
    renderRoute();
    await screen.findByText('Aphex Twin');
    // The vanilla page must not activate underneath — two live library pages
    // would fight over #library-artists-grid and the downloads section.
    expect(window.SoulSyncWebShellBridge!.activateLegacyPath).not.toHaveBeenCalledWith('/library');
  });

  it('loads the artists through the route loader', async () => {
    renderRoute();
    await waitFor(() =>
      expect(requested.filter((u) => u.includes('/api/library/artists'))).not.toEqual([]),
    );
  });

  it('accepts every filter in the URL without throwing the route down', async () => {
    // An all-digits q arrives as a NUMBER from TanStack's JSON parse, which a
    // bare z.string() would reject and take the route down with.
    renderRoute(['/library?q=123&letter=A&page=3&watchlist=unwatched&source=spotify']);
    await waitFor(() =>
      expect(requested.filter((u) => u.includes('/api/library/artists'))).not.toEqual([]),
    );
    const url = new URL(requested.find((u) => u.includes('/api/library/artists'))!, 'http://x');
    expect(url.searchParams.get('search')).toBe('123');
    expect(url.searchParams.get('letter')).toBe('a');
  });

  it('falls back rather than crashing on a nonsense page number', async () => {
    renderRoute(['/library?page=notanumber']);
    await waitFor(() =>
      expect(requested.filter((u) => u.includes('/api/library/artists'))).not.toEqual([]),
    );
    const url = new URL(requested.find((u) => u.includes('/api/library/artists'))!, 'http://x');
    expect(url.searchParams.get('page')).toBe('1');
  });

  it('still respects the page permission gate', async () => {
    window.SoulSyncWebShellBridge = createShellBridge({ isPageAllowed: vi.fn(() => false) });
    const { router } = renderRoute();
    await waitFor(() => expect(router.state.location.pathname).not.toBe('/library'));
  });
});
