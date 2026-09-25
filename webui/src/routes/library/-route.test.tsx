import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * /library is LIVE — the manifest hands it to React, and the page behind it is
 * Library v2, which replaced the vanilla list rather than porting it.
 *
 * What this file guards is unchanged from the port's own route suite: the route
 * must not hand the page back to vanilla, validateSearch must survive whatever
 * is in the URL, and the permission gate still applies. Only the page and the
 * endpoint it loads from changed.
 */

function renderRoute(entries = ['/library']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: entries });
  const router = createAppRouter({ history, queryClient });
  return { router, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

let requested: string[] = [];
let requestedWrites: string[] = [];
let canWrite = true;

const artistsRequests = () => requested.filter((u) => u.includes('library/v2/artists'));

describe('library route (live)', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showLibraryDownloadsSection = vi.fn();
    requested = [];
    requestedWrites = [];
    canWrite = true;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        requested.push(url);
        const method = input instanceof Request ? input.method : (init?.method ?? 'GET');
        if (method !== 'GET') requestedWrites.push(`${method} ${url}`);
        let body: unknown = {};
        if (url.includes('library/v2/enabled')) {
          body = { success: true, enabled: true, can_write: canWrite };
        } else if (url.includes('library/v2/artists')) {
          body = {
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
          };
        }
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
    const { router } = renderRoute();
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
    // The vanilla page must not activate underneath — it is deleted, and a
    // handoff would only blank the route.
    expect(router.state.location.pathname).toBe('/library');
    expect(window.SoulSyncWebShellBridge!.activateLegacyPath).not.toHaveBeenCalledWith('/library');
  });

  it('loads the artists through the route loader', async () => {
    renderRoute();
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
  });

  it('accepts every filter in the URL without throwing the route down', async () => {
    // An all-digits q arrives as a NUMBER from TanStack's JSON parse, which a
    // bare z.string() would reject and take the route down with.
    renderRoute(['/library?q=123&sort=name&page=3&monitored=monitored']);
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
    const url = new URL(artistsRequests()[0]!, 'http://x');
    expect(url.searchParams.get('search')).toBe('123');
    expect(url.searchParams.get('page')).toBe('3');
    expect(url.searchParams.get('monitored')).toBe('monitored');
  });

  it('falls back rather than crashing on a nonsense page number', async () => {
    renderRoute(['/library?page=notanumber']);
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
    const url = new URL(artistsRequests()[0]!, 'http://x');
    expect(url.searchParams.get('page')).toBe('1');
  });

  it('normalizes the removed import-review section without requesting it', async () => {
    renderRoute(['/library?section=import-review']);
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
    expect(requested.some((url) => url.includes('library/v2/acquisition'))).toBe(false);
    expect(requested.some((url) => url.includes('library/v2/artists'))).toBe(true);
  });

  it('fails closed when can_write is false and never submits a mutation', async () => {
    canWrite = false;
    const { container } = renderRoute();
    await screen.findByText(/Read-only: library changes require the admin profile/);
    const mutations = [...container.querySelectorAll<HTMLElement>('[data-requires-write]')];
    expect(mutations.length).toBeGreaterThan(0);
    expect(mutations.every((control) => control.matches(':disabled'))).toBe(true);
    mutations.forEach((control) => fireEvent.click(control));
    expect(requestedWrites).toEqual([]);
  });

  it('still respects the page permission gate', async () => {
    window.SoulSyncWebShellBridge = createShellBridge({ isPageAllowed: vi.fn(() => false) });
    const { router } = renderRoute();
    await waitFor(() => expect(router.state.location.pathname).not.toBe('/library'));
  });

  it('does not bounce a denied profile back into /library (iss29-B10)', async () => {
    // Home page resolves to `library` (directly, or via the legacy `library-v2`
    // page id the shell normalizes to it) while allowed_pages excludes it. The
    // guard used to redirect to the very page it had just refused.
    window.SoulSyncWebShellBridge = createShellBridge({
      isPageAllowed: vi.fn((pageId) => pageId !== 'library'),
      getProfileHomePage: vi.fn(() => 'library' as const),
    });
    const { router } = renderRoute();
    await waitFor(() => expect(router.state.location.pathname).not.toBe('/library'));
  });
});
