import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

// The React watchlist page is still dormant in the shipped manifest — the
// vanilla page owns /watchlist until it reaches parity. These tests exercise
// the React page, so they declare it owned. Everything else in the manifest is
// left exactly as it ships.
vi.mock('@/platform/shell/route-manifest', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/platform/shell/route-manifest')>();
  return {
    ...actual,
    getShellRouteByPageId: (pageId: string) =>
      pageId === 'watchlist'
        ? { pageId: 'watchlist', path: '/watchlist', kind: 'react' }
        : actual.getShellRouteByPageId(pageId as never),
  };
});

function createResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function artistRow(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    artist_name: 'Aphex Twin',
    date_added: '2026-01-01T00:00:00Z',
    last_scan_timestamp: '2026-01-02T00:00:00Z',
    created_at: null,
    updated_at: null,
    image_url: null,
    spotify_artist_id: 'sp-aphex',
    itunes_artist_id: null,
    deezer_artist_id: null,
    discogs_artist_id: null,
    musicbrainz_artist_id: null,
    amazon_artist_id: null,
    include_albums: true,
    include_eps: false,
    include_singles: true,
    include_live: false,
    include_remixes: false,
    include_acoustic: false,
    include_compilations: false,
    ...overrides,
  };
}

interface StubOptions {
  count?: number;
  nextRunInSeconds?: number;
  artists?: Record<string, unknown>[];
  globalOverride?: boolean;
  labels?: Record<string, unknown>[];
  scanCompletedAt?: string | null;
}

function stubFetch(options: StubOptions = {}) {
  const {
    artists = [artistRow()],
    count = artists.length,
    nextRunInSeconds = 3600,
    globalOverride = false,
    labels = [],
    scanCompletedAt = null,
  } = options;

  const calls: string[] = [];

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      calls.push(url);

      if (url.includes('/api/watchlist/count')) {
        return createResponse({ success: true, count, next_run_in_seconds: nextRunInSeconds });
      }
      if (url.includes('/api/watchlist/artists')) {
        return createResponse({ success: true, artists });
      }
      if (url.includes('/api/watchlist/scan/status')) {
        return createResponse({
          success: true,
          status: 'idle',
          completed_at: scanCompletedAt,
          summary: scanCompletedAt
            ? { total_artists: 4, new_tracks_found: 3, tracks_added_to_wishlist: 2 }
            : {},
        });
      }
      if (url.includes('/api/watchlist/global-config')) {
        return createResponse({
          success: true,
          config: {
            global_override_enabled: globalOverride,
            include_albums: true,
            include_eps: true,
            include_singles: true,
            include_live: false,
            include_remixes: false,
            include_acoustic: false,
            include_compilations: false,
            include_instrumentals: false,
            exclude_terms: '',
          },
        });
      }
      if (url.includes('/api/labels/watchlist')) {
        return createResponse({ labels });
      }

      throw new Error(`unexpected fetch: ${url}`);
    }),
  );

  return calls;
}

function renderWatchlistRoute(initialEntries = ['/watchlist']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });

  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

describe('watchlist route', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
  });

  it('renders the artist grid with counts, pills and source badges', async () => {
    stubFetch();
    renderWatchlistRoute();

    await waitFor(() => {
      expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
    });

    expect(screen.getByText('1 artist')).toBeInTheDocument();
    expect(screen.getByText('Next Auto: 1h 00m')).toBeInTheDocument();
    expect(screen.getByText('Spotify')).toBeInTheDocument();
    expect(screen.getByText('Albums')).toBeInTheDocument();
    expect(screen.getByText('Singles')).toBeInTheDocument();
    // Not enabled on this artist, so it must not be rendered at all.
    expect(screen.queryByText('EPs')).not.toBeInTheDocument();
  });

  it('shows the empty state instead of the grid when nothing is watched', async () => {
    stubFetch({ artists: [], count: 0 });
    renderWatchlistRoute();

    await waitFor(() => {
      expect(screen.getByText('Your watchlist is empty')).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: 'Open Search' })).toBeInTheDocument();
  });

  it('only shows the global override banner when the override is on', async () => {
    stubFetch({ globalOverride: false });
    const { unmount } = renderWatchlistRoute();

    await waitFor(() => {
      expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
    });
    expect(screen.queryByText(/Global override is active/)).not.toBeInTheDocument();

    unmount();
    vi.unstubAllGlobals();

    stubFetch({ globalOverride: true });
    renderWatchlistRoute();

    await waitFor(() => {
      expect(screen.getByText(/Global override is active/)).toBeInTheDocument();
    });
  });

  it('shows the last-scan strip only once a scan has completed', async () => {
    stubFetch({ scanCompletedAt: null });
    const { unmount } = renderWatchlistRoute();

    await waitFor(() => {
      expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
    });
    expect(screen.queryByText(/Last scan:/)).not.toBeInTheDocument();

    unmount();
    vi.unstubAllGlobals();

    stubFetch({ scanCompletedAt: '2026-07-25T00:00:00Z' });
    renderWatchlistRoute();

    await waitFor(() => {
      expect(screen.getByText(/3 new tracks found, 2 added to wishlist/)).toBeInTheDocument();
    });
  });

  it('applies the sort and filter carried in the URL', async () => {
    stubFetch({
      artists: [
        artistRow({ id: 1, artist_name: 'Aphex Twin', spotify_artist_id: 'sp-1' }),
        artistRow({ id: 2, artist_name: 'Boards of Canada', spotify_artist_id: 'sp-2' }),
        artistRow({ id: 3, artist_name: 'Clark', spotify_artist_id: 'sp-3' }),
      ],
    });
    renderWatchlistRoute(['/watchlist?sort=name-desc']);

    await waitFor(() => {
      expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
    });

    const names = screen
      .getAllByText(/Aphex Twin|Boards of Canada|Clark/)
      .map((node) => node.textContent);
    expect(names).toEqual(['Clark', 'Boards of Canada', 'Aphex Twin']);
  });

  it('narrows the grid to the filter carried in the URL', async () => {
    stubFetch({
      artists: [
        artistRow({ id: 1, artist_name: 'Aphex Twin', spotify_artist_id: 'sp-1' }),
        artistRow({ id: 2, artist_name: 'Boards of Canada', spotify_artist_id: 'sp-2' }),
        artistRow({ id: 3, artist_name: 'Clark', spotify_artist_id: 'sp-3' }),
      ],
    });
    renderWatchlistRoute(['/watchlist?q=canada']);

    await waitFor(() => {
      expect(screen.getByText('Boards of Canada')).toBeInTheDocument();
    });

    expect(screen.queryByText('Aphex Twin')).not.toBeInTheDocument();
    expect(screen.queryByText('Clark')).not.toBeInTheDocument();
    // The input reflects the URL rather than starting blank, so a shared link
    // does not lie about what is being filtered.
    expect(screen.getByPlaceholderText('Filter watchlist…')).toHaveValue('canada');
  });

  it('does not fetch labels until the Labels tab is opened', async () => {
    const calls = stubFetch();
    renderWatchlistRoute(['/watchlist']);

    await waitFor(() => {
      expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
    });

    // The labels blueprint is a separate round trip; the artists tab must not
    // pay for it.
    expect(calls.some((url) => url.includes('/api/labels/watchlist'))).toBe(false);
  });

  it('loads labels when the route opens straight onto the Labels tab', async () => {
    const calls = stubFetch({
      labels: [
        {
          id: 5,
          musicbrainz_label_id: 'mb-warp',
          discogs_label_id: null,
          label_name: 'Warp Records',
          source: 'musicbrainz',
          backlog: false,
          date_added: '2026-01-01T00:00:00Z',
          last_scan_timestamp: null,
        },
      ],
    });
    renderWatchlistRoute(['/watchlist?tab=labels']);

    await waitFor(() => {
      expect(calls.some((url) => url.includes('/api/labels/watchlist'))).toBe(true);
    });

    // The artist grid belongs to the other tab and must not be on screen.
    expect(screen.queryByText('Aphex Twin')).not.toBeInTheDocument();
  });

  it('redirects away when the profile may not see the watchlist', async () => {
    stubFetch();
    window.SoulSyncWebShellBridge = createShellBridge({
      isPageAllowed: (pageId) => pageId !== 'watchlist',
    });

    const { history } = renderWatchlistRoute();

    await waitFor(() => {
      expect(history.location.pathname).not.toBe('/watchlist');
    });
  });

  it('survives a search param that arrives as a non-string', async () => {
    stubFetch();
    // TanStack JSON-parses search values, so an all-digits filter arrives as a
    // number. A bare z.string() would throw SearchParamError and kill the route.
    renderWatchlistRoute(['/watchlist?q=311']);

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Filter watchlist…')).toHaveValue('311');
    });
  });
});
