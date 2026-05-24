import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createAppQueryClient } from '@/app/query-client';
import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createShellBridge } from '@/test/shell-bridge';

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
    window.showToast = vi.fn();
    server.use(
      http.get('/api/stats/cached', () =>
        HttpResponse.json({
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
        }),
      ),
      http.get('/api/listening-stats/status', () =>
        HttpResponse.json({ stats: { last_poll: '2026-05-14 10:00:00' } }),
      ),
      http.get('/status', () =>
        HttpResponse.json({ media_server: { type: 'plex', connected: true } }),
      ),
      http.get('/api/stats/db-storage', () =>
        HttpResponse.json({
          success: true,
          tables: [{ name: 'tracks', size: 2048 }],
          total_file_size: 4096,
          method: 'dbstat',
        }),
      ),
      http.get('/api/stats/library-disk-usage', () =>
        HttpResponse.json({
          success: true,
          has_data: true,
          total_bytes: 2048,
          tracks_with_size: 12,
          tracks_without_size: 0,
          by_format: { flac: 2048 },
        }),
      ),
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

  it('still renders when listening stats status prefetch fails', async () => {
    server.use(
      http.get('/api/listening-stats/status', () =>
        HttpResponse.json({ error: 'status unavailable' }, { status: 500 }),
      ),
    );

    renderStatsRoute();

    await waitFor(() => expect(screen.getByTestId('stats-page')).toBeInTheDocument());
    expect(await screen.findByText('Listening Stats')).toBeInTheDocument();
    expect(screen.getByText('Not synced yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sync listening stats' })).toBeInTheDocument();
  });

  it('shows an explicit standalone notice instead of the sync button', async () => {
    server.use(
      http.get('/status', () =>
        HttpResponse.json({ media_server: { type: 'soulsync', connected: true } }),
      ),
    );

    renderStatsRoute();

    await waitFor(() => expect(screen.getByTestId('stats-page')).toBeInTheDocument());
    expect(await screen.findByText('Listening Stats')).toBeInTheDocument();
    expect(screen.getByText('Standalone mode: manual sync unavailable')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sync listening stats' })).not.toBeInTheDocument();
  });

  it('stores the time range in route search state', async () => {
    const { history } = renderStatsRoute();

    fireEvent.click(await screen.findByRole('button', { name: '30 Days' }));

    await waitFor(() => expect(history.location.search).toContain('range=30d'));
  });

  it('links artist names to the artist-detail route', async () => {
    const { history } = renderStatsRoute();

    const bubbleLink = await screen.findByRole('link', {
      name: 'Open artist detail for Artist A',
    });
    expect(bubbleLink).toHaveAttribute('href', '/artist-detail/library/7');

    const rankedLink = screen.getByRole('link', { name: 'Artist A' });
    expect(rankedLink).toHaveAttribute('href', '/artist-detail/library/7');

    fireEvent.click(bubbleLink);

    await waitFor(() => expect(history.location.pathname).toBe('/artist-detail/library/7'));
    await waitFor(() =>
      expect(window.SoulSyncWebShellBridge?.navigateToArtistDetail).toHaveBeenCalledWith(
        '7',
        '',
        null,
        {
          skipRouteChange: true,
        },
      ),
    );
  });

  it('falls back to streaming when track resolution fails', async () => {
    window.SoulSyncWebShellBridge = createShellBridge({
      startStream: vi.fn(),
    });

    server.use(
      http.post('/api/stats/resolve-track', () =>
        HttpResponse.json({ error: 'resolve unavailable' }, { status: 500 }),
      ),
      http.post('/api/enhanced-search/stream-track', () =>
        HttpResponse.json({
          success: true,
          result: { stream_url: '/api/stream/1' },
        }),
      ),
    );

    renderStatsRoute();

    fireEvent.click((await screen.findAllByTitle('Play'))[0]);

    await waitFor(() =>
      expect(window.SoulSyncWebShellBridge?.startStream).toHaveBeenCalledWith({
        stream_url: '/api/stream/1',
      }),
    );
    expect(window.SoulSyncWebShellBridge?.playLibraryTrack).not.toHaveBeenCalled();
  });

  it('redirects back home when the page is not allowed', async () => {
    window.SoulSyncWebShellBridge = createShellBridge({
      isPageAllowed: vi.fn((pageId) => pageId !== 'stats'),
    });

    const { history } = renderStatsRoute(['/stats']);

    await waitFor(() => expect(history.location.pathname).toBe('/discover'));
  });
});
