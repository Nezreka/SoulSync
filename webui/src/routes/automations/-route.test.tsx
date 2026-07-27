import { createMemoryHistory } from '@tanstack/react-router';
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * The /automations route exists but is DORMANT.
 *
 * TanStack matches a route from the generated tree, not from the manifest — so
 * the moment this route file landed, /automations started matching even though
 * the shell still owns the page. The guard inside route.tsx is what keeps the
 * vanilla page in charge; without it the legacy page and a React host would
 * both activate on the same URL.
 *
 * These tests pin that handover. When the manifest flips to 'react' in P5 they
 * are expected to be rewritten — deliberately, not by drift.
 *
 * Honest caveat on their strength right now: defeating the guard (forcing
 * isReactOwned() true) is caught ONLY by the "does not touch the automations
 * API" case. The others still pass, because until P2 builds the real page the
 * component returns LegacyRouteController down BOTH branches, so there is
 * nothing to tell them apart. The loader is the only observable difference at
 * this phase. Once the page exists, the render assertions gain their teeth.
 */

function renderRoute(entries = ['/automations']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: entries });
  const router = createAppRouter({ history, queryClient });
  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

let requested: string[] = [];

describe('automations route (dormant)', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    // The app shell fetches on its own (profile context, status) no matter
    // which route is active, so this records URLs rather than forbidding calls
    // outright — the invariant is that no AUTOMATIONS endpoint is touched.
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
    // If this fails the page was handed to React without its UI being built.
    expect(getShellRouteByPageId('automations')?.kind).toBe('legacy');
  });

  it('hands /automations to the vanilla page instead of rendering React', async () => {
    renderRoute();

    const bridge = window.SoulSyncWebShellBridge!;
    await waitFor(() => {
      expect(bridge.activateLegacyPath).toHaveBeenCalledWith('/automations');
    });
  });

  it('renders no React page markup of its own', async () => {
    const { container } = renderRoute();

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge!.activateLegacyPath).toHaveBeenCalled();
    });
    // LegacyRouteController renders null — the vanilla DOM in index.html is
    // what the user sees. Anything here would be a second, competing page.
    expect(container.querySelector('.automations-container')).toBeNull();
  });

  it('does not touch the automations API while dormant', async () => {
    renderRoute();

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge!.activateLegacyPath).toHaveBeenCalled();
    });
    // The loader returns early before ensureQueryData, so nothing should ask
    // for the list, the master toggle or progress.
    expect(requested.filter((u) => u.includes('/api/automations'))).toEqual([]);
  });

  it('still respects the page permission gate', async () => {
    // A profile denied the page must be redirected even while dormant —
    // otherwise the dormant period is a hole in the IDOR gate.
    // createShellBridge already homes to 'discover'; only the gate changes.
    window.SoulSyncWebShellBridge = createShellBridge({
      isPageAllowed: vi.fn(() => false),
    });
    const { router } = renderRoute();

    await waitFor(() => {
      expect(router.state.location.pathname).not.toBe('/automations');
    });
  });
});
