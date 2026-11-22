import { QueryClientProvider } from '@tanstack/react-query';
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { monthYear, rankLabel, sinceLabel } from '../-artist-your-plays';
import { ArtistYourPlays } from './artist-your-plays';

const PLAYS = {
  play_count: 1234,
  first_played: '2019-03-02 21:12:00',
  last_played: '2026-11-18 10:00:00',
  plays_30d: 12,
  rank: 3,
  artists_played: 410,
  months: [
    { month: '2025-12', plays: 0 },
    { month: '2026-11', plays: 12 },
  ],
  top_tracks: [
    {
      title: 'Song',
      album: 'Album',
      play_count: 300,
      last_played: '2026-11-18 10:00:00',
      track_id: 7,
      album_id: 3,
      thumb_url: '/api/library/v2/artwork/album/3?size=thumb',
    },
    {
      title: 'Not In Library',
      album: '',
      play_count: 1,
      last_played: null,
      track_id: null,
      album_id: null,
      thumb_url: '',
    },
  ],
};

function renderPanel() {
  const rootRoute = createRootRoute();
  const page = createRoute({
    getParentRoute: () => rootRoute,
    path: '/library',
    component: () => <ArtistYourPlays artistId={5} artistName="Band" />,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([page]),
    history: createMemoryHistory({ initialEntries: ['/library?artist=5'] }),
  });
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe('ArtistYourPlays', () => {
  it('shows you and the artist, and the songs you play most', async () => {
    server.use(
      http.get('/api/library/v2/artists/5/your-plays', () =>
        HttpResponse.json({ success: true, listening: PLAYS }),
      ),
    );
    renderPanel();

    const panel = await screen.findByRole('region', { name: 'You and Band' });
    expect(panel).toHaveTextContent('1,234plays');
    expect(panel).toHaveTextContent('Your #3 artist of 410');
    expect(panel).toHaveTextContent('First listened Mar 2019');
    expect(screen.getByRole('link', { name: /Song/ })).toHaveAttribute(
      'href',
      expect.stringContaining('album=3'),
    );
    // a song you played that the library doesn't have is listed, not linked
    expect(screen.getByText('Not In Library').closest('a')).toBeNull();
  });

  it('renders nothing when you never played them', async () => {
    let asked = 0;
    server.use(
      http.get('/api/library/v2/artists/5/your-plays', () => {
        asked += 1;
        return HttpResponse.json({ success: true, listening: null });
      }),
    );
    renderPanel();

    await expect.poll(() => asked).toBe(1);
    expect(screen.queryByRole('region', { name: 'You and Band' })).toBeNull();
  });
});

describe('your plays words', () => {
  const now = new Date('2026-11-20T12:00:00Z');

  it('says how long ago', () => {
    expect(sinceLabel('2026-11-20 08:00:00', now)).toBe('today');
    expect(sinceLabel('2026-11-17 08:00:00', now)).toBe('3 days ago');
    expect(sinceLabel('2026-10-01 08:00:00', now)).toBe('7 weeks ago');
    expect(sinceLabel('2019-03-02 21:12:00', now)).toBe('Mar 2019');
    expect(sinceLabel(null, now)).toBe('');
  });

  it('names the month and the rank', () => {
    expect(monthYear('2019-03-02 21:12:00')).toBe('Mar 2019');
    expect(rankLabel(1, 10)).toBe('Your most played artist');
    expect(rankLabel(12, 1400)).toBe('Your #12 artist of 1,400');
  });
});
