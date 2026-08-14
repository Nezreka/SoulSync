import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

function renderStatsRoute(initialEntries = ['/stats']) {
  const queryClient = createTestQueryClient();
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
          previous: {
            total_plays: 12,
            total_time_ms: 3_300_000,
            unique_artists: 3,
            unique_albums: 2,
            unique_tracks: 6,
          },
          clock: {
            grid: Array.from({ length: 7 }, (_, d) =>
              Array.from({ length: 24 }, (_, h) => (d === 3 && h === 21 ? 9 : 0)),
            ),
            peak: { weekday: 3, hour: 21, plays: 9 },
            total: 9,
          },
          rhythm: {
            current_streak: 4,
            longest_streak: 11,
            busiest_day: { date: '2026-08-12', plays: 9 },
            active_days: 20,
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
    // Artist detail is React-owned now, so the click routes there directly
    // rather than handing the URL back to the legacy shell.
    expect(window.SoulSyncWebShellBridge?.navigateToArtistDetail).not.toHaveBeenCalled();
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
  /**
   * The tiles printed totals with nothing to measure them against. The delta is
   * the whole point of stats P1 — a render guard so it cannot quietly stop
   * appearing (the download-chip lesson: half a feature can ship inert).
   */
  it('shows each tile its change against the previous period', async () => {
    renderStatsRoute();
    // plays 24/12, time 6.6M/3.3M, albums 4/2, tracks 12/6 all doubled.
    const doubled = await screen.findAllByText(/↑ 100% vs previous 7 days/);
    expect(doubled).toHaveLength(4);
    // Artists were 3 both periods — flat, and deliberately not an arrow.
    expect(screen.getByText(/· 0% vs previous 7 days/)).toBeTruthy();
  });

  /**
   * Defence against a STALE cache. The worker writes previous: null for 'all',
   * but a cache written before that existed — or a future backend slip — could
   * hand the page a previous it has no label for, and the tile would read
   * "↑ 5% undefined". The label is the gate, not just the data.
   */
  it('renders no delta for a range it has no label for, even with data', async () => {
    server.use(
      http.get('/api/stats/cached', () =>
        HttpResponse.json({
          success: true,
          overview: { total_plays: 24, total_time_ms: 1, unique_artists: 1,
                      unique_albums: 1, unique_tracks: 1 },
          // A previous window that should not exist for 'all'.
          previous: { total_plays: 12, total_time_ms: 1, unique_artists: 1,
                      unique_albums: 1, unique_tracks: 1 },
          top_artists: [], top_albums: [], top_tracks: [],
          timeline: [], genres: [], recent: [],
          health: { total_tracks: 12 },
        }),
      ),
    );
    renderStatsRoute(['/stats?range=all']);
    expect(await screen.findByText('Total Plays')).toBeTruthy();
    // The symptom is NOT the string "undefined" — React renders a null label
    // as empty. It is a naked "↑ 100%" with nothing saying what it is against.
    expect(screen.queryByText(/↑\s*100%/)).toBeNull();
    expect(screen.queryByText(/vs previous/)).toBeNull();
  });

  /**
   * P2: the page carried two kinds of fact in one visual language — your top
   * artists sitting beside database table sizes. They are read by different
   * people at different frequencies. Split, with the tab in the URL so it is
   * linkable and survives a reload.
   */
  it('shows listening facts by default and hides the operational ones', async () => {
    renderStatsRoute();
    expect(await screen.findByText('Top Artists')).toBeTruthy();
    expect(screen.queryByText('Library Disk Usage')).toBeNull();
    expect(screen.queryByText('Database Storage')).toBeNull();
  });

  it('the library tab shows the operational facts and none of the personal ones', async () => {
    renderStatsRoute(['/stats?tab=library']);
    expect(await screen.findByText('Library Disk Usage')).toBeTruthy();
    expect(screen.getByText('Database Storage')).toBeTruthy();
    expect(screen.getByText('Library Health')).toBeTruthy();
    expect(screen.queryByText('Top Artists')).toBeNull();
  });

  it('hides the range picker on the library tab', async () => {
    // Disk usage is what it is today — a range picker there is a control that
    // does nothing, which is worse than no control.
    const { container } = renderStatsRoute(['/stats?tab=library']);
    await screen.findByText('Library Disk Usage');
    expect(container.querySelector('#stats-time-range')?.hasAttribute('hidden')).toBe(true);
  });

  it('keeps the chosen range when switching tabs', async () => {
    // Switching to Library and back must not silently reset a chosen range.
    const { history } = renderStatsRoute(['/stats?range=30d']);
    await screen.findByText('Top Artists');
    fireEvent.click(screen.getByRole('tab', { name: 'Library' }));
    await screen.findByText('Library Disk Usage');
    expect(history.location.search).toContain('range=30d');
    expect(history.location.search).toContain('tab=library');
  });

  /**
   * P3: the page could say how MUCH you listened and never WHEN. The heatmap
   * is the most personal thing the data supports, and it is one GROUP BY.
   */
  it('names the slot you listen most', async () => {
    renderStatsRoute();
    expect(await screen.findByText('Wed 9pm')).toBeTruthy();
    expect(screen.getByText('peak slot')).toBeTruthy();
  });

  it('shows listening as a habit, not just a total', async () => {
    renderStatsRoute();
    expect(await screen.findByText('longest streak')).toBeTruthy();
    expect(screen.getByText('11')).toBeTruthy();
    expect(screen.getByText('days in a row')).toBeTruthy();
  });

  it('says so plainly when there are no plays to shape', async () => {
    // An empty heatmap is a grid of blank squares, which reads as broken.
    server.use(
      http.get('/api/stats/cached', () =>
        HttpResponse.json({
          success: true,
          overview: { total_plays: 3, total_time_ms: 1, unique_artists: 1,
                      unique_albums: 1, unique_tracks: 1 },
          clock: { grid: Array.from({ length: 7 }, () => Array(24).fill(0)),
                   peak: { weekday: null, hour: null, plays: 0 }, total: 0 },
          top_artists: [], top_albums: [], top_tracks: [],
          timeline: [], genres: [], recent: [],
          health: { total_tracks: 1 },
        }),
      ),
    );
    renderStatsRoute();
    expect(await screen.findByText(/No plays in this range yet/)).toBeTruthy();
  });

  it('omits the comparison entirely on the all-time range', async () => {
    // 'all' has no period before it — the backend sends previous: null and the
    // page must render nothing rather than a delta against zero.
    server.use(
      http.get('/api/stats/cached', () =>
        HttpResponse.json({
          success: true,
          overview: { total_plays: 24, total_time_ms: 1, unique_artists: 1,
                      unique_albums: 1, unique_tracks: 1 },
          previous: null,
          top_artists: [], top_albums: [], top_tracks: [],
          timeline: [], genres: [], recent: [],
          health: { total_tracks: 12 },
        }),
      ),
    );
    renderStatsRoute(['/stats?range=all']);
    expect(await screen.findByText('Total Plays')).toBeTruthy();
    expect(screen.queryByText(/vs previous/)).toBeNull();
  });

});

describe('stats route survives a backend outage', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
  });

  it('renders the page instead of "Something went wrong"', async () => {
    // Only the cached-stats query was unguarded; the status probe already had
    // its own catch. allSettled covers both.
    server.use(
      http.get('/api/stats/cached', () => HttpResponse.json({ error: 'down' }, { status: 500 })),
      http.get('/api/listening/stats/status', () =>
        HttpResponse.json({ error: 'down' }, { status: 500 }),
      ),
    );

    renderStatsRoute(['/stats']);

    await waitFor(() =>
      expect(window.SoulSyncWebShellBridge!.showReactHost).toHaveBeenCalledWith('stats'),
    );
    expect(screen.queryByText('Something went wrong')).toBeNull();
  });
});
