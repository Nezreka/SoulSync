import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * /automations is now React-owned.
 *
 * This file previously pinned the DORMANT state — that the route existed but
 * deferred to the vanilla page. Those assertions were written to fail the
 * moment the manifest flipped, which is exactly what happened; they are
 * replaced here rather than deleted, so the route keeps a guard on both the
 * handover and the permission gate.
 */

function renderRoute(entries = ['/automations']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: entries });
  const router = createAppRouter({ history, queryClient });
  return {
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

let requested: string[] = [];

describe('automations route', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    requested = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        requested.push(url);
        const body = url.includes('/api/automations/master')
          ? { music: true }
          : url.includes('/api/automations/progress')
            ? {}
            : [{ id: 1, name: 'Nightly', enabled: 1, is_system: 1 }];
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
  });

  it('is owned by React in the manifest', () => {
    expect(getShellRouteByPageId('automations')?.kind).toBe('react');
  });

  it('renders the React page instead of handing off to the vanilla shell', async () => {
    renderRoute();

    await screen.findByText('Nightly');
    expect(document.querySelector('.automations-container')).not.toBeNull();
    // The legacy controller must NOT have been asked to activate the old page.
    expect(window.SoulSyncWebShellBridge!.activateLegacyPath).not.toHaveBeenCalled();
  });

  it('loads the list and the master state before painting', async () => {
    renderRoute();
    await screen.findByText('Nightly');
    expect(requested.some((u) => u.endsWith('/api/automations'))).toBe(true);
    expect(requested.some((u) => u.includes('/api/automations/master'))).toBe(true);
  });

  it('still respects the page permission gate', async () => {
    // A profile denied the page must be redirected — the gate has to survive
    // the handover to React, not just exist while the route was dormant.
    window.SoulSyncWebShellBridge = createShellBridge({
      isPageAllowed: vi.fn(() => false),
    });
    const { router } = renderRoute();

    await waitFor(() => {
      expect(router.state.location.pathname).not.toBe('/automations');
    });
  });

  it('carries the filter controls in the URL', async () => {
    renderRoute(['/automations?q=night&trigger=schedule&action=scan_library']);
    await waitFor(() => expect(document.querySelector('.automations-container')).not.toBeNull());
    // validateSearch must accept all three without throwing the route down.
    expect(document.querySelector('.automations-container')).not.toBeNull();
  });
});
