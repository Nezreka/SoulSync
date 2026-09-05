import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  SHELL_PROFILE_CONTEXT_CHANGED_EVENT,
  type ShellProfileContext,
  type ShellPageId,
} from '@/platform/shell/bridge';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { AppRouterProvider, createAppRouter, DefaultErrorComponent, errorReport } from './router';

describe('createAppRouter', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/issues/counts', () =>
        HttpResponse.json({
          success: true,
          counts: { open: 2, in_progress: 1, resolved: 0, dismissed: 0, total: 3 },
        }),
      ),
      http.get('/api/issues', () =>
        HttpResponse.json({
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
      ),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.SoulSyncWebShellBridge = undefined;
    window.SoulSyncIssueDomain = undefined;
  });

  it('creates one shared query client and applies router defaults', () => {
    const queryClient = createTestQueryClient();
    const router = createAppRouter({ queryClient });

    expect(router.options.context?.queryClient).toBe(queryClient);
    expect(router.options.defaultPreload).toBe('intent');
    expect(router.options.defaultPreloadStaleTime).toBe(0);
    // A predicate now, not a flag: every route restores scroll EXCEPT artist
    // detail, which always opens at the top. Its similar-artist row sits at the
    // very bottom, so the position worth saving there is the footer.
    const shouldRestore = router.options.scrollRestoration as (opts: {
      location: { pathname: string };
    }) => boolean;
    expect(typeof shouldRestore).toBe('function');
    expect(shouldRestore({ location: { pathname: '/library' } })).toBe(true);
    expect(shouldRestore({ location: { pathname: '/wishlist' } })).toBe(true);
    expect(shouldRestore({ location: { pathname: '/artist-detail/library/42' } })).toBe(false);
    expect(router.options.defaultErrorComponent).toBeDefined();
    expect(router.options.defaultNotFoundComponent).toBeDefined();
  });

  it('renders migrated React routes directly and updates shell chrome', async () => {
    window.SoulSyncWebShellBridge = createShellBridge();

    const queryClient = createTestQueryClient();
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
    // /settings, because /sync is React now (it was the example here until the
    // sync flip, and /search before that). Any still-legacy path does.
    window.SoulSyncWebShellBridge = createShellBridge();

    const queryClient = createTestQueryClient();
    const history = createMemoryHistory({ initialEntries: ['/settings'] });
    const router = createAppRouter({ history, queryClient });

    render(<AppRouterProvider router={router} queryClient={queryClient} />);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.activateLegacyPath).toHaveBeenCalledWith('/settings');
    });
  });

  it('redirects disallowed React routes back to the profile home page', async () => {
    window.SoulSyncWebShellBridge = createShellBridge({
      isPageAllowed: vi.fn((pageId) => pageId !== 'issues'),
    });

    const queryClient = createTestQueryClient();
    const history = createMemoryHistory({ initialEntries: ['/issues'] });
    const router = createAppRouter({ history, queryClient });

    render(<AppRouterProvider router={router} queryClient={queryClient} />);

    await waitFor(() => {
      expect(history.location.pathname).toBe('/discover');
    });
  });

  it('waits for profile context before rendering React routes', async () => {
    const getCurrentProfileContext = vi.fn<() => ShellProfileContext | null>(() => null);
    window.SoulSyncWebShellBridge = createShellBridge({
      getCurrentProfileContext,
    });

    const queryClient = createTestQueryClient();
    const history = createMemoryHistory({ initialEntries: ['/issues'] });
    const router = createAppRouter({ history, queryClient });

    render(<AppRouterProvider router={router} queryClient={queryClient} />);

    expect(screen.queryByTestId('issues-board')).not.toBeInTheDocument();

    getCurrentProfileContext.mockReturnValue({ profileId: 1, isAdmin: false });
    window.dispatchEvent(new CustomEvent(SHELL_PROFILE_CONTEXT_CHANGED_EVENT));

    await waitFor(() => {
      expect(screen.getByTestId('issues-board')).toBeInTheDocument();
    });
  });

  it('redirects the root route to the profile home page', async () => {
    // A LEGACY home page, so the assertion is about the redirect reaching the
    // legacy handler — sync is React now and would render in place instead.
    window.SoulSyncWebShellBridge = createShellBridge({
      getProfileHomePage: vi.fn<() => ShellPageId>(() => 'settings'),
    });

    const queryClient = createTestQueryClient();
    const history = createMemoryHistory({ initialEntries: ['/'] });
    const router = createAppRouter({ history, queryClient });

    render(<AppRouterProvider router={router} queryClient={queryClient} />);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.activateLegacyPath).toHaveBeenCalledWith('/settings');
    });

    expect(history.location.pathname).toBe('/settings');
  });
});

/**
 * The route error boundary.
 *
 * It said "Something went wrong. Please refresh." and nothing else, while the
 * console is muted by default — so a user hitting it had nothing to report and
 * we had nothing to act on.
 */
describe('the error boundary', () => {
  it('shows the message and stack of a real error', () => {
    const boom = new Error("Cannot read properties of undefined (reading 'map')");
    render(<DefaultErrorComponent error={boom} />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/reading 'map'/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy error' })).toBeInTheDocument();
  });

  it('copies exactly what the report needs', () => {
    const writeText = vi.fn();
    Object.assign(navigator, { clipboard: { writeText } });
    render(<DefaultErrorComponent error={new Error('kaboom')} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy error' }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('kaboom'));
  });

  it('falls back to the old copy when there is no error to show', () => {
    render(<DefaultErrorComponent />);
    expect(screen.getByText('Please refresh the page and try again.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Copy error' })).toBeNull();
  });

  it('reports strings and thrown non-errors too', () => {
    expect(errorReport('plain string')).toBe('plain string');
    expect(errorReport({ status: 500 })).toContain('500');
    expect(errorReport(null)).toBeNull();
  });

  it('trims a runaway stack instead of dumping the whole thing', () => {
    const err = new Error('deep');
    err.stack = ['Error: deep', ...Array.from({ length: 80 }, (_, i) => `  at frame${i}`)].join(
      '\n',
    );
    expect(errorReport(err)!.split('\n')).toHaveLength(12);
  });
});
