import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

// The React wishlist page is dormant in the shipped manifest; these tests
// exercise it, so they declare it owned. Everything else ships as-is.
vi.mock('@/platform/shell/route-manifest', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/platform/shell/route-manifest')>();
  return {
    ...actual,
    getShellRouteByPageId: (pageId: string) =>
      pageId === 'wishlist'
        ? { pageId: 'wishlist', path: '/wishlist', kind: 'react' }
        : actual.getShellRouteByPageId(pageId as never),
  };
});

const res = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

function albumRow(artist: string, album: string, name: string, retry = 0) {
  return {
    spotify_track_id: `${artist}-${album}-${name}`,
    retry_count: retry,
    spotify_data: {
      name,
      album: { name: album, images: [{ url: `${album}.jpg` }] },
      artists: [{ name: artist }],
    },
  };
}

function stubFetch(opts: { total?: number; albums?: unknown[]; singles?: unknown[] } = {}) {
  const { albums = [albumRow('Aphex Twin', 'SAW', 'Xtal')], singles = [] } = opts;
  const total = opts.total ?? albums.length + singles.length;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes('/api/wishlist/stats'))
        return res({
          total,
          albums: albums.length,
          singles: singles.length,
          next_run_in_seconds: 600,
        });
      if (url.includes('/api/wishlist/cycle')) return res({ cycle: 'albums' });
      if (url.includes('category=albums'))
        return res({ tracks: albums, artist_images: { 'Aphex Twin': 'library.jpg' } });
      if (url.includes('category=singles')) return res({ tracks: singles, artist_images: {} });
      if (url.includes('/api/watchlist/artists')) return res({ success: true, artists: [] });
      throw new Error(`unexpected fetch: ${url}`);
    }),
  );
}

function renderRoute(entries = ['/wishlist']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: entries });
  const router = createAppRouter({ history, queryClient });
  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

describe('wishlist route', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
  });

  it('renders the nebula with orbs, counts and the stats strip', async () => {
    stubFetch({
      albums: [albumRow('Aphex Twin', 'SAW', 'Xtal'), albumRow('Aphex Twin', 'SAW', 'Ageispolis')],
      singles: [],
    });
    renderRoute();

    await waitFor(() => expect(screen.getAllByText('Aphex Twin').length).toBeGreaterThan(0));
    // "2 tracks" legitimately appears in the header count AND in the orb meta,
    // so scope to the header rather than asserting uniqueness.
    expect(document.querySelector('.wishlist-page-count')?.textContent).toBe('2 tracks');
    // both album tracks land under one album tile on one orb
    expect(document.querySelectorAll('.wl-orb-group')).toHaveLength(1);
    expect(document.querySelectorAll('.wl-album-tile')).toHaveLength(1);
    expect(screen.getByText('Album Tracks')).toBeInTheDocument();
    // cycle 'albums' renders as the friendly label
    expect(screen.getByText('Albums/EPs')).toBeInTheDocument();
  });

  it('shows the empty state and hides the stats strip when nothing is wishlisted', async () => {
    stubFetch({ total: 0, albums: [], singles: [] });
    renderRoute();

    await waitFor(() => expect(screen.getByText('Your wishlist is empty')).toBeInTheDocument());
    expect(screen.queryByText('Album Tracks')).not.toBeInTheDocument();
  });

  it('marks an artist as failing once a track hits the threshold', async () => {
    stubFetch({ albums: [albumRow('Aphex Twin', 'SAW', 'Xtal', 4)] });
    renderRoute();

    await waitFor(() => expect(screen.getByText(/1 failing/)).toBeInTheDocument());
  });

  it('does not mark failing below the threshold', async () => {
    stubFetch({ albums: [albumRow('Aphex Twin', 'SAW', 'Xtal', 2)] });
    renderRoute();

    await waitFor(() => expect(screen.getAllByText('Aphex Twin').length).toBeGreaterThan(0));
    expect(screen.queryByText(/failing/)).not.toBeInTheDocument();
  });

  it('redirects away when the profile may not see the wishlist', async () => {
    stubFetch();
    window.SoulSyncWebShellBridge = createShellBridge({ isPageAllowed: (p) => p !== 'wishlist' });
    const { history } = renderRoute();
    await waitFor(() => expect(history.location.pathname).not.toBe('/wishlist'));
  });
});
