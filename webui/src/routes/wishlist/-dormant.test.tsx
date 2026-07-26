import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * Deliberately does NOT mock the manifest, unlike -route.test.tsx.
 *
 * While the route is dormant it must hand off to the legacy shell and fetch
 * nothing — otherwise a user on /wishlist gets the vanilla page and the React
 * host fighting over the same screen.
 */
describe('wishlist route while dormant', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
  });

  it('hands /wishlist to the legacy shell and issues no wishlist requests', async () => {
    const fetchSpy = vi.fn(
      async (_input: RequestInfo | URL) => new Response('{}', { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchSpy);

    const queryClient = createTestQueryClient();
    const history = createMemoryHistory({ initialEntries: ['/wishlist'] });
    render(
      <AppRouterProvider
        router={createAppRouter({ history, queryClient })}
        queryClient={queryClient}
      />,
    );

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.activateLegacyPath).toHaveBeenCalledWith('/wishlist');
    });

    expect(screen.queryByText('Your wishlist is empty')).not.toBeInTheDocument();
    expect(window.SoulSyncWebShellBridge?.showReactHost).not.toHaveBeenCalled();

    const urls = fetchSpy.mock.calls.map(([i]) => (i instanceof Request ? i.url : String(i)));
    expect(urls.filter((u) => u.includes('/api/wishlist'))).toEqual([]);
  });
});
