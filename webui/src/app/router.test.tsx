import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ShellBridge, ShellPageId } from '@/platform/shell/bridge';

import { createAppQueryClient } from './query-client';
import { AppRouterProvider, createAppRouter } from './router';

function mockIssuesFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = input instanceof Request ? input.url : String(input);

    if (url.includes('/api/issues/counts')) {
      return new Response(
        JSON.stringify({
          success: true,
          counts: { open: 2, in_progress: 1, resolved: 0, dismissed: 0, total: 3 },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    }

    if (url.includes('/api/issues?')) {
      return new Response(
        JSON.stringify({
          success: true,
          total: 1,
          issues: [
            {
              id: 7,
              entity_type: 'album',
              entity_id: 'album-7',
              category: 'wrong_cover',
              title: 'Wrong cover art',
              status: 'open',
              priority: 'normal',
              snapshot_data: '{}',
            },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    }

    throw new Error(`Unexpected fetch request: ${url}`);
  });
}

function createShellBridge(overrides: Partial<ShellBridge> = {}): ShellBridge {
  return {
    getCurrentPageId: vi.fn<() => ShellPageId>(() => 'dashboard'),
    getCurrentProfileContext: vi.fn(() => ({ profileId: 1, isAdmin: false })),
    isPageAllowed: vi.fn(() => true),
    getProfileHomePage: vi.fn<() => ShellPageId>(() => 'discover'),
    resolveLegacyPath: vi.fn<(pathname: string) => ShellPageId | null>(() => 'search'),
    setActivePageChrome: vi.fn(),
    activateLegacyPath: vi.fn(),
    showReactHost: vi.fn(),
    ...overrides,
  };
}

describe('createAppRouter', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockIssuesFetch());
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.SoulSyncWebShellBridge = undefined;
    window.SoulSyncIssueDomain = undefined;
  });

  it('creates one shared query client and applies router defaults', () => {
    const queryClient = createAppQueryClient();
    const router = createAppRouter({ queryClient });

    expect(router.options.context?.queryClient).toBe(queryClient);
    expect(router.options.defaultPreload).toBe('intent');
    expect(router.options.defaultPreloadStaleTime).toBe(0);
    expect(router.options.scrollRestoration).toBe(true);
    expect(router.options.defaultErrorComponent).toBeDefined();
    expect(router.options.defaultNotFoundComponent).toBeDefined();
  });

  it('renders migrated React routes directly and updates shell chrome', async () => {
    window.SoulSyncWebShellBridge = createShellBridge();

    const queryClient = createAppQueryClient();
    const history = createMemoryHistory({ initialEntries: ['/issues'] });
    const router = createAppRouter({ history, queryClient });

    render(<AppRouterProvider router={router} queryClient={queryClient} />);

    await waitFor(() => {
      expect(screen.getByTestId('issues-board')).toBeInTheDocument();
    });

    expect(window.SoulSyncWebShellBridge?.showReactHost).toHaveBeenCalledWith('issues');
    expect(window.SoulSyncWebShellBridge?.setActivePageChrome).toHaveBeenCalledWith('issues');
    expect(window.SoulSyncWebShellBridge?.activateLegacyPath).not.toHaveBeenCalled();
  });

  it('routes non-migrated paths through the legacy fallback handler', async () => {
    window.SoulSyncWebShellBridge = createShellBridge();

    const queryClient = createAppQueryClient();
    const history = createMemoryHistory({ initialEntries: ['/search'] });
    const router = createAppRouter({ history, queryClient });

    render(<AppRouterProvider router={router} queryClient={queryClient} />);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.activateLegacyPath).toHaveBeenCalledWith('/search');
    });
  });

  it('redirects disallowed React routes back to the profile home page', async () => {
    window.SoulSyncWebShellBridge = createShellBridge({
      isPageAllowed: vi.fn((pageId) => pageId !== 'issues'),
    });

    const queryClient = createAppQueryClient();
    const history = createMemoryHistory({ initialEntries: ['/issues'] });
    const router = createAppRouter({ history, queryClient });

    render(<AppRouterProvider router={router} queryClient={queryClient} />);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.activateLegacyPath).toHaveBeenCalledWith('/discover');
    });

    expect(history.location.pathname).toBe('/discover');
  });

  it('redirects the root route to the profile home page', async () => {
    window.SoulSyncWebShellBridge = createShellBridge({
      getProfileHomePage: vi.fn<() => ShellPageId>(() => 'search'),
    });

    const queryClient = createAppQueryClient();
    const history = createMemoryHistory({ initialEntries: ['/'] });
    const router = createAppRouter({ history, queryClient });

    render(<AppRouterProvider router={router} queryClient={queryClient} />);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.activateLegacyPath).toHaveBeenCalledWith('/search');
    });

    expect(history.location.pathname).toBe('/search');
  });
});
