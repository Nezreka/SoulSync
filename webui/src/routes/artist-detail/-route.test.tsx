import { createMemoryHistory } from '@tanstack/react-router';
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ShellBridge, ShellPageId } from '@/platform/shell/bridge';

import { createAppQueryClient } from '@/app/query-client';
import { AppRouterProvider, createAppRouter } from '@/app/router';

function createShellBridge(overrides: Partial<ShellBridge> = {}): ShellBridge {
  return {
    getCurrentProfileContext: vi.fn(() => ({ profileId: 2, isAdmin: false })),
    isPageAllowed: vi.fn(() => true),
    getProfileHomePage: vi.fn<() => ShellPageId>(() => 'discover'),
    resolveLegacyPath: vi.fn<(pathname: string) => ShellPageId | null>(() => 'artist-detail'),
    setActivePageChrome: vi.fn(),
    activateLegacyPath: vi.fn(),
    navigateToArtistDetail: vi.fn(),
    showReactHost: vi.fn(),
    ...overrides,
  };
}

function renderArtistDetailRoute(initialEntries = ['/artist-detail/library/42']) {
  const queryClient = createAppQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });

  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

describe('artist-detail route', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });

  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
  });

  it('hands off canonical artist-detail URLs to the legacy shell', async () => {
    renderArtistDetailRoute(['/artist-detail/spotify/2YZyLoL8N0Wb9xBt1NhZWg']);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.navigateToArtistDetail).toHaveBeenCalledWith(
        '2YZyLoL8N0Wb9xBt1NhZWg',
        '',
        'spotify',
        {
          skipRouteChange: true,
        },
      );
    });
  });

  it('normalizes library sources before handing off', async () => {
    renderArtistDetailRoute(['/artist-detail/library/42']);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.navigateToArtistDetail).toHaveBeenCalledWith(
        '42',
        '',
        null,
        {
          skipRouteChange: true,
        },
      );
    });
  });
});
