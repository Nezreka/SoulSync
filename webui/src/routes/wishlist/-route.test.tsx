import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

function stubFetch(
  opts: { total?: number; albums?: unknown[]; singles?: unknown[]; processing?: boolean } = {},
) {
  const {
    albums = [albumRow('Aphex Twin', 'SAW', 'Xtal')],
    singles = [],
    processing = false,
  } = opts;
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
          is_auto_processing: processing,
        });
      // The live poller hits this on mount; stub it so nothing silently 404s.
      if (url.includes('/api/active-processes')) return res({ active_processes: [] });
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

  it('expands one orb at a time and reveals its album fan', async () => {
    stubFetch({
      albums: [
        albumRow('Aphex Twin', 'SAW', 'Xtal'),
        albumRow('Boards of Canada', 'MHTRTC', 'Roygbiv'),
      ],
    });
    renderRoute();

    await waitFor(() => expect(document.querySelectorAll('.wl-orb-group')).toHaveLength(2));
    const orbs = () => [...document.querySelectorAll('.wl-orb-group')];
    expect(orbs().filter((o) => o.classList.contains('expanded'))).toHaveLength(0);

    fireEvent.click(orbs()[0].querySelector('.wl-orb')!);
    expect(orbs()[0].classList.contains('expanded')).toBe(true);

    // Accordion: opening the second closes the first.
    fireEvent.click(orbs()[1].querySelector('.wl-orb')!);
    expect(orbs()[0].classList.contains('expanded')).toBe(false);
    expect(orbs()[1].classList.contains('expanded')).toBe(true);

    // Clicking the open one closes it.
    fireEvent.click(orbs()[1].querySelector('.wl-orb')!);
    expect(orbs()[1].classList.contains('expanded')).toBe(false);
  });

  it('opening the artist link does not also expand the orb', async () => {
    stubFetch();
    window._navigateToArtistFromWishlist = vi.fn();
    renderRoute();

    await waitFor(() => expect(document.querySelector('.wl-orb-label')).toBeTruthy());
    fireEvent.click(document.querySelector('.wl-orb-label')!);

    expect(window._navigateToArtistFromWishlist).toHaveBeenCalledWith('Aphex Twin');
    expect(document.querySelector('.wl-orb-group')!.classList.contains('expanded')).toBe(false);
  });

  it('filters orbs by the query in the URL', async () => {
    stubFetch({
      albums: [
        albumRow('Aphex Twin', 'SAW', 'Xtal'),
        albumRow('Boards of Canada', 'MHTRTC', 'Roygbiv'),
      ],
    });
    renderRoute(['/wishlist?q=canada']);

    await waitFor(() => expect(document.querySelectorAll('.wl-orb-group')).toHaveLength(1));
    expect(screen.getAllByText('Boards of Canada').length).toBeGreaterThan(0);
    expect(screen.queryByText('Aphex Twin')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Filter wishlist')).toHaveValue('canada');
  });

  it('the failing chip narrows to artists with stuck tracks', async () => {
    stubFetch({
      albums: [
        albumRow('Aphex Twin', 'SAW', 'Xtal', 5),
        albumRow('Boards of Canada', 'MHTRTC', 'Roygbiv', 0),
      ],
    });
    renderRoute(['/wishlist?failing=true']);

    await waitFor(() => expect(document.querySelectorAll('.wl-orb-group')).toHaveLength(1));
    expect(screen.getAllByText('Aphex Twin').length).toBeGreaterThan(0);
  });

  it('removing an album confirms first, removing a track does not', async () => {
    const calls: string[] = [];
    stubFetch();
    // capture POSTs on top of the standard stub
    const base = globalThis.fetch as unknown as (
      i: RequestInfo | URL,
      init?: RequestInit,
    ) => Promise<Response>;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (i: RequestInfo | URL, init?: RequestInit) => {
        const url = i instanceof Request ? i.url : String(i);
        if (url.includes('remove-album') || url.includes('remove-track')) {
          calls.push(url);
          return res({ success: true });
        }
        return base(i, init);
      }),
    );
    window.showConfirmDialog = vi.fn(async () => true);
    renderRoute();

    await waitFor(() => expect(document.querySelector('.wl-orb')).toBeTruthy());
    fireEvent.click(document.querySelector('.wl-orb')!);

    const tile = document.querySelector('.wl-album-tile')!;
    expect(tile.classList.contains('tile-expanded')).toBe(false);

    fireEvent.click(screen.getByLabelText('Remove album SAW'));
    await waitFor(() => expect(calls.some((u) => u.includes('remove-album'))).toBe(true));
    expect(window.showConfirmDialog).toHaveBeenCalled();
    // The remove button sits INSIDE the clickable tile, so it must not also
    // toggle the tile open — that is what its stopPropagation is for.
    expect(document.querySelector('.wl-album-tile')!.classList.contains('tile-expanded')).toBe(
      false,
    );

    // Track removal is deliberately confirm-free, matching the vanilla handler.
    const confirmCallsBefore = (window.showConfirmDialog as ReturnType<typeof vi.fn>).mock.calls
      .length;
    fireEvent.click(screen.getByLabelText('Remove Xtal'));
    await waitFor(() => expect(calls.some((u) => u.includes('remove-track'))).toBe(true));
    expect((window.showConfirmDialog as ReturnType<typeof vi.fn>).mock.calls.length).toBe(
      confirmCallsBefore,
    );
  });

  it('declining the album confirm removes nothing', async () => {
    const calls: string[] = [];
    stubFetch();
    const base = globalThis.fetch as unknown as (
      i: RequestInfo | URL,
      init?: RequestInit,
    ) => Promise<Response>;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (i: RequestInfo | URL, init?: RequestInit) => {
        const url = i instanceof Request ? i.url : String(i);
        if (url.includes('remove-album')) {
          calls.push(url);
          return res({ success: true });
        }
        return base(i, init);
      }),
    );
    window.showConfirmDialog = vi.fn(async () => false);
    renderRoute();

    await waitFor(() => expect(document.querySelector('.wl-orb')).toBeTruthy());
    fireEvent.click(document.querySelector('.wl-orb')!);
    fireEvent.click(screen.getByLabelText('Remove album SAW'));

    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(calls).toEqual([]);
  });

  it('delegates the action bar and download button to the downloads.js globals', async () => {
    stubFetch();
    window.openWishlistIgnoreModal = vi.fn();
    window.cleanupWishlistOverview = vi.fn();
    window.clearEntireWishlist = vi.fn();
    window._nebulaDownload = vi.fn();
    renderRoute();

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Ignored' })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Ignored' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cleanup' }));
    fireEvent.click(screen.getByRole('button', { name: 'Clear All' }));
    fireEvent.click(screen.getByRole('button', { name: 'Download Wishlist' }));

    // These own module-scoped state (activeDownloadProcesses, WishlistModalState),
    // so they are invoked rather than reimplemented.
    expect(window.openWishlistIgnoreModal).toHaveBeenCalled();
    expect(window.cleanupWishlistOverview).toHaveBeenCalled();
    expect(window.clearEntireWishlist).toHaveBeenCalled();
    expect(window._nebulaDownload).toHaveBeenCalled();
  });

  it('hands the countdown to downloads.js with the cycle and remaining seconds', async () => {
    stubFetch();
    window.startWishlistCountdownTimer = vi.fn();
    renderRoute();

    await waitFor(() =>
      expect(window.startWishlistCountdownTimer).toHaveBeenCalledWith('albums', 600),
    );
    // and renders the element that helper writes into
    expect(document.getElementById('wishlist-next-auto-timer')).toBeTruthy();
  });

  it('renders the ids the vanilla download dialog reads its counts from', async () => {
    stubFetch({ albums: [albumRow('A', 'X', 'a1')], singles: [] });
    renderRoute();

    await waitFor(() => expect(document.getElementById('wishlist-stat-albums')).toBeTruthy());
    expect(document.getElementById('wishlist-stat-albums')?.textContent).toBe('1');
    expect(document.getElementById('wishlist-stat-singles')?.textContent).toBe('0');
  });

  it('marks the field and orbs as processing while a run is in flight', async () => {
    stubFetch({ processing: true });
    renderRoute();

    await waitFor(() =>
      expect(document.querySelector('.wl-nebula-field.nebula-processing')).toBeTruthy(),
    );
    expect(document.querySelector('.wl-orb-group.orb-processing')).toBeTruthy();
  });

  it('does not mark processing when nothing is running', async () => {
    stubFetch({ processing: false });
    renderRoute();

    await waitFor(() => expect(document.querySelector('.wl-orb-group')).toBeTruthy());
    expect(document.querySelector('.nebula-processing')).toBeNull();
    expect(document.querySelector('.orb-processing')).toBeNull();
  });

  it('redirects away when the profile may not see the wishlist', async () => {
    stubFetch();
    window.SoulSyncWebShellBridge = createShellBridge({ isPageAllowed: (p) => p !== 'wishlist' });
    const { history } = renderRoute();
    await waitFor(() => expect(history.location.pathname).not.toBe('/wishlist'));
  });
});
