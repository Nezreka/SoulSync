import { createMemoryHistory } from '@tanstack/react-router';
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * /library exists as a route but is DORMANT.
 *
 * TanStack matches from the generated route tree, not the manifest, so this
 * route began matching the moment the file landed. The isReactOwned() guard is
 * what keeps the vanilla page in charge; without it both would activate.
 *
 * Honest note on strength: until P2 adds the page component, the route returns
 * LegacyRouteController down BOTH branches, so only the "does not fetch" case
 * can detect a defeated guard. The render assertions gain teeth once the page
 * exists — the same progression the automations route went through.
 */

function renderRoute(entries = ['/library']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: entries });
  const router = createAppRouter({ history, queryClient });
  return { router, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

let requested: string[] = [];

describe('library route (dormant)', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    requested = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        requested.push(input instanceof Request ? input.url : String(input));
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
  });

  it('is still owned by the shell', () => {
    // Fails the moment the manifest flips — which is the point. Rewrite it
    // then, deliberately, rather than letting it rot.
    expect(getShellRouteByPageId('library')?.kind).toBe('legacy');
  });

  it('hands /library to the vanilla page', async () => {
    renderRoute();
    await waitFor(() =>
      expect(window.SoulSyncWebShellBridge!.activateLegacyPath).toHaveBeenCalledWith('/library'),
    );
  });

  it('does not touch the library API while dormant', async () => {
    renderRoute();
    await waitFor(() =>
      expect(window.SoulSyncWebShellBridge!.activateLegacyPath).toHaveBeenCalled(),
    );
    // The shell fetches on its own regardless of route, so this checks the
    // library endpoint specifically rather than forbidding all traffic.
    expect(requested.filter((u) => u.includes('/api/library'))).toEqual([]);
  });

  it('accepts every filter in the URL without throwing the route down', async () => {
    // validateSearch runs even while dormant. An all-digits q arrives as a
    // NUMBER from TanStack's JSON parse, which a bare z.string() would reject.
    renderRoute(['/library?q=123&letter=A&page=3&watchlist=unwatched&source=spotify']);
    await waitFor(() =>
      expect(window.SoulSyncWebShellBridge!.activateLegacyPath).toHaveBeenCalled(),
    );
  });

  it('falls back rather than crashing on a nonsense page number', async () => {
    renderRoute(['/library?page=notanumber']);
    await waitFor(() =>
      expect(window.SoulSyncWebShellBridge!.activateLegacyPath).toHaveBeenCalled(),
    );
  });

  it('still respects the page permission gate', async () => {
    window.SoulSyncWebShellBridge = createShellBridge({ isPageAllowed: vi.fn(() => false) });
    const { router } = renderRoute();
    await waitFor(() => expect(router.state.location.pathname).not.toBe('/library'));
  });
});
