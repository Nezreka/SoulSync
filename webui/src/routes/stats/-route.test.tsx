import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ShellBridge, ShellPageId } from '@/platform/shell/bridge';

import { createAppQueryClient } from '@/app/query-client';
import { AppRouterProvider, createAppRouter } from '@/app/router';

function createResponse(body: unknown, ok = true, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function createShellBridge(overrides: Partial<ShellBridge> = {}): ShellBridge {
  return {
    getCurrentProfileContext: vi.fn(() => ({ profileId: 2, isAdmin: true })),
    isPageAllowed: vi.fn(() => true),
    getProfileHomePage: vi.fn<() => ShellPageId>(() => 'discover'),
    resolveLegacyPath: vi.fn<(pathname: string) => ShellPageId | null>(() => 'search'),
    setActivePageChrome: vi.fn(),
    activateLegacyPath: vi.fn(),
    showReactHost: vi.fn(),
    ...overrides,
  };
}

function renderStatsRoute(initialEntries = ['/stats']) {
  const queryClient = createAppQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });

  return {
    history,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

describe('stats route', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.navigateToArtistDetail = vi.fn();
    window.playLibraryTrack = vi.fn();
    window.startStream = vi.fn();
    window.showLoadingOverlay = vi.fn();
    window.hideLoadingOverlay = vi.fn();
    window.showToast = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes('/api/stats/cached')) {
          return createResponse({
            success: true,
            overview: {
              total_plays: 24,
              total_time_ms: 6_600_000,
              unique_artists: 3,
              unique_albums: 4,
              unique_tracks: 12,
            },
            top_artists: [{ id: 7, name: 'Artist A', play_count: 10 }],
            top_albums: [],
            top_tracks: [],
            timeline: [{ date: 'May 10', plays: 4 }],
            genres: [{ genre: 'House', play_count: 10, percentage: 80 }],
            recent: [{ title: 'Track A', artist: 'Artist A', played_at: '2026-05-14T08:00:00Z' }],
            health: { total_tracks: 12, format_breakdown: { FLAC: 12 } },
          });
        }
        if (url.includes('/api/listening-stats/status')) {
          return createResponse({ stats: { last_poll: '2026-05-14 10:00:00' } });
        }
        if (url.includes('/api/stats/db-storage')) {
          return createResponse({
            success: true,
            tables: [{ name: 'tracks', size: 2048 }],
            total_file_size: 4096,
            method: 'dbstat',
          });
        }
        if (url.includes('/api/stats/library-disk-usage')) {
          return createResponse({
            success: true,
            has_data: true,
            total_bytes: 2048,
            tracks_with_size: 12,
            tracks_without_size: 0,
            by_format: { flac: 2048 },
          });
        }
        return createResponse({ success: true });
      }) as unknown as typeof fetch,
    );
  });

  it('renders the stats page through the app router', async () => {
    renderStatsRoute();

    await waitFor(() => expect(screen.getByTestId('stats-page')).toBeInTheDocument());
    expect(await screen.findByText('Listening Stats')).toBeInTheDocument();
    expect(screen.getByText('24')).toBeInTheDocument();
    expect(window.SoulSyncWebShellBridge?.showReactHost).toHaveBeenCalledWith('stats');
    expect(window.SoulSyncWebShellBridge?.setActivePageChrome).toHaveBeenCalledWith('stats');
  });

  it('stores the time range in route search state', async () => {
    const { history } = renderStatsRoute();

    fireEvent.click(await screen.findByRole('button', { name: '30 Days' }));

    await waitFor(() => expect(history.location.search).toContain('range=30d'));
  });

  it('redirects back home when the page is not allowed', async () => {
    window.SoulSyncWebShellBridge = createShellBridge({
      isPageAllowed: vi.fn((pageId) => pageId !== 'stats'),
    });

    const { history } = renderStatsRoute(['/stats']);

    await waitFor(() => expect(history.location.pathname).toBe('/discover'));
  });
});
