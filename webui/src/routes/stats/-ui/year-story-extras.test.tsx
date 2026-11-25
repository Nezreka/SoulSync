import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { YearInListening } from '../-year.types';

/**
 * The Year story's extras: the streak, busiest day, genre and calendar
 * slides, save as playlist, and the soundtrack pill.
 */

const YEAR: YearInListening = {
  period: { start: '2026-01-01', end: '2026-12-03', label: 'Jan 2026 — Dec 2026', months: 12 },
  has_data: true,
  totals: { plays: 500, minutes: 1800, artists: 20, albums: 30, tracks: 120, active_days: 3 },
  months: [{ month: '2026-05', label: 'May 2026', plays: 500, minutes: 1800, top_artist: 'A' }],
  top_artists: [{ name: 'Lead Act', plays: 200, months_on_top: 3 }],
  top_albums: [],
  top_tracks: [],
  discoveries: [],
  peak_day: { date: '2026-05-02', plays: 47, minutes: 150, top_artist: 'Lead Act' },
  top_hour: { hour: null, plays: 0 },
  days: [
    { date: '2026-05-01', plays: 10, minutes: 30 },
    { date: '2026-05-02', plays: 47, minutes: 150 },
    { date: '2026-05-03', plays: 5, minutes: 15 },
  ],
  streak: { days: 3, start: '2026-05-01', end: '2026-05-03' },
  top_genres: [
    { genre: 'shoegaze', plays: 60, share: 0.6 },
    { genre: 'dream pop', plays: 40, share: 0.4 },
  ],
};

let posted: unknown[] = [];

function mount(soundtrack: unknown[] = []) {
  server.use(
    http.get('/api/stats/cached', () => HttpResponse.json({ success: true })),
    http.get('/api/listening-stats/status', () => HttpResponse.json({ success: true })),
    http.get('/api/stats/db-storage', () => HttpResponse.json({ success: true, tables: [] })),
    http.get('/api/stats/library-disk-usage', () => HttpResponse.json({ success: true })),
    http.get('/api/stats/year', () => HttpResponse.json({ success: true, ...YEAR })),
    http.get('/api/stats/year/soundtrack', () =>
      HttpResponse.json({ success: true, tracks: soundtrack }),
    ),
    http.post('/api/stats/year/playlist', async ({ request }) => {
      posted.push(await request.json());
      return HttpResponse.json({
        success: true,
        id: 7,
        name: 'Your 2026',
        track_count: 50,
        created: posted.length === 1,
      });
    }),
  );
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: ['/stats?story=year'] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

async function advanceTo(label: string | RegExp) {
  const next = await screen.findByRole('button', { name: 'Next' });
  for (let i = 0; i < 20; i += 1) {
    if (screen.queryByText(label)) return;
    fireEvent.click(next);
  }
  throw new Error(`never reached the slide showing "${String(label)}"`);
}

beforeEach(() => {
  posted = [];
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showToast = vi.fn();
  window.playTrackList = vi.fn(async () => ({ status: 'played' }));
});

describe('the year story extras', () => {
  it('names the top genre and its share', async () => {
    mount();
    await advanceTo('Your sound');
    expect(screen.getByRole('heading', { name: 'shoegaze' })).toBeInTheDocument();
    expect(screen.getByText('60% of what you played')).toBeInTheDocument();
  });

  it('shows the longest streak and when it ran', async () => {
    mount();
    await advanceTo('Your longest streak');
    expect(screen.getByText('days in a row with music')).toBeInTheDocument();
    expect(screen.getByText(/^From .+ to .+\.$/)).toBeInTheDocument();
  });

  it('shows the busiest day and who led it', async () => {
    mount();
    await advanceTo('Your biggest day');
    expect(screen.getByText('Mostly Lead Act.')).toBeInTheDocument();
  });

  it('draws the calendar with one square per day of the window', async () => {
    const { baseElement } = mount();
    await advanceTo('Every day of it');
    const cal = screen.getByRole('img', { name: /A calendar of your year: 3 days with music/ });
    const cells = cal.querySelectorAll('[data-level]');
    expect(cells.length).toBeGreaterThanOrEqual(337); // Jan 1 to Dec 3, plus padding
    expect(baseElement.querySelectorAll('[data-level="4"]').length).toBeGreaterThanOrEqual(1);
  });

  it('saves the year as a playlist, top 50 or 100, and updates it after', async () => {
    mount();
    await advanceTo('Save your year as a playlist');
    fireEvent.click(screen.getByRole('radio', { name: 'Top 100' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save as playlist' }));
    await waitFor(() => expect(posted).toEqual([{ size: 100 }]));
    expect(await screen.findByRole('button', { name: 'Update playlist' })).toBeInTheDocument();
    expect(window.showToast).toHaveBeenCalledWith('Saved Your 2026: 50 songs', 'success');
  });
});

describe('the soundtrack pill', () => {
  const SONG = {
    id: 1,
    title: 'Anthem',
    artist: 'Lead Act',
    album: 'Al',
    file_path: '/m/1.flac',
    start_ms: 60_000,
    duration_ms: 240_000,
  };

  it('starts the top owned song through the player when nothing is playing', async () => {
    mount([SONG]);
    await waitFor(() =>
      expect(window.playTrackList).toHaveBeenCalledWith(
        [expect.objectContaining({ title: 'Anthem', file_path: '/m/1.flac' })],
        'Your Year',
      ),
    );
  });

  it('stays hidden with no owned songs', async () => {
    mount([]);
    await screen.findByRole('dialog', { name: 'Your Year in Listening' });
    expect(screen.queryByText(/your soundtrack/i)).toBeNull();
  });
});
