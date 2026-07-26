import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * The React watchlist route exists but the shipped manifest still says the
 * vanilla page owns /watchlist.
 *
 * This file deliberately does NOT mock the manifest, unlike -route.test.tsx.
 * It pins the half of the contract that protects the running app: while the
 * route is dormant it must hand off to the legacy shell and must not fetch, or
 * a user on /watchlist would get the vanilla page and the React host fighting
 * over the same screen.
 */
describe('watchlist route while dormant', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
  });

  it('hands /watchlist to the legacy shell and issues no watchlist requests', async () => {
    const fetchSpy = vi.fn(
      async (_input: RequestInfo | URL) => new Response('{}', { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchSpy);

    const queryClient = createTestQueryClient();
    const history = createMemoryHistory({ initialEntries: ['/watchlist'] });
    const router = createAppRouter({ history, queryClient });

    render(<AppRouterProvider router={router} queryClient={queryClient} />);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.activateLegacyPath).toHaveBeenCalledWith('/watchlist');
    });

    // The React page renders neither its header nor its empty state.
    expect(screen.queryByText('Watchlist')).not.toBeInTheDocument();
    expect(screen.queryByText('Your watchlist is empty')).not.toBeInTheDocument();

    // And the loader short-circuits, so the vanilla page's own fetches are not
    // duplicated.
    const urls = fetchSpy.mock.calls.map(([input]) =>
      input instanceof Request ? input.url : String(input),
    );
    expect(urls.filter((url) => url.includes('/api/watchlist'))).toEqual([]);
  });

  it('never shows the React host for a dormant page', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{}', { status: 200 })),
    );

    const queryClient = createTestQueryClient();
    const history = createMemoryHistory({ initialEntries: ['/watchlist'] });
    const router = createAppRouter({ history, queryClient });

    render(<AppRouterProvider router={router} queryClient={queryClient} />);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.activateLegacyPath).toHaveBeenCalled();
    });

    // showReactHost is what hides every `.page` element; calling it here is
    // exactly the bug this guard exists to prevent.
    expect(window.SoulSyncWebShellBridge?.showReactHost).not.toHaveBeenCalled();
  });
});
