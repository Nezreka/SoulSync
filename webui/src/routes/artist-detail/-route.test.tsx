import { createMemoryHistory } from '@tanstack/react-router';
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createAppQueryClient } from '@/app/query-client';
import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createShellBridge } from '@/test/shell-bridge';

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

  it('cancels the similar artists stream when the route unmounts', async () => {
    const { unmount } = renderArtistDetailRoute(['/artist-detail/spotify/2YZyLoL8N0Wb9xBt1NhZWg']);

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

    const cancelSimilarArtistsLoad = window.SoulSyncWebShellBridge
      ?.cancelSimilarArtistsLoad as ReturnType<typeof vi.fn>;
    cancelSimilarArtistsLoad.mockClear();

    unmount();

    await waitFor(() => {
      expect(cancelSimilarArtistsLoad).toHaveBeenCalledTimes(1);
    });
  });
});
