import { createMemoryHistory } from '@tanstack/react-router';
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

function renderArtistDetailRoute(initialEntries = ['/artist-detail/library/42']) {
  const queryClient = createTestQueryClient();
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

  it('survives an all-digits artist name (311) in ?name=', async () => {
    // TanStack's search parser JSON-parses param values, so name=311 arrives
    // as a NUMBER. A bare z.string() schema threw SearchParamError, the route
    // died in its error boundary, and clicking the artist "did nothing".
    renderArtistDetailRoute(['/artist-detail/deezer/2481?name=311']);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.navigateToArtistDetail).toHaveBeenCalledWith(
        '2481',
        '311',
        'deezer',
        {
          skipRouteChange: true,
        },
      );
    });
  });

  it('passes the ?name= search param through to the legacy shell', async () => {
    // Bandcamp (and any other source with no numeric-ID lookup API) can only
    // resolve an artist by name — the URL is the only channel that survives
    // a page load / browser-back, so this must round-trip correctly.
    renderArtistDetailRoute(['/artist-detail/bandcamp/3957198221?name=Radiohead']);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.navigateToArtistDetail).toHaveBeenCalledWith(
        '3957198221',
        'Radiohead',
        'bandcamp',
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
