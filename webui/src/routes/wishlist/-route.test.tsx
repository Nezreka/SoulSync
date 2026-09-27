import { createMemoryHistory } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

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
    // both album tracks land under one orb...
    expect(document.querySelectorAll('.wl-orb-group')).toHaveLength(1);
    // ...whose album fan is NOT mounted until the orb is opened — a
    // 400-track wishlist used to render every track row on first paint
    // (perf sweep, Aug 2026). Expanding mounts the single grouped tile.
    expect(document.querySelectorAll('.wl-album-tile')).toHaveLength(0);
    fireEvent.click(document.querySelector('.wl-orb')!);
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

  it('refreshes when vanilla code announces a wishlist change', async () => {
    // Cleanup / Clear All run in downloads.js and change the wishlist under the
    // page. Without this the orbs keep showing removed tracks until you
    // navigate away and back.
    const calls: string[] = [];
    const albums = [albumRow('Aphex Twin', 'SAW', 'Xtal')];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (i: RequestInfo | URL) => {
        const url = i instanceof Request ? i.url : String(i);
        calls.push(url);
        if (url.includes('/api/wishlist/stats'))
          return res({ total: 1, albums: 1, singles: 0, next_run_in_seconds: 600 });
        if (url.includes('/api/wishlist/cycle')) return res({ cycle: 'albums' });
        if (url.includes('category=albums')) return res({ tracks: albums, artist_images: {} });
        if (url.includes('category=singles')) return res({ tracks: [], artist_images: {} });
        if (url.includes('/api/active-processes')) return res({ active_processes: [] });
        if (url.includes('/api/watchlist/artists')) return res({ success: true, artists: [] });
        throw new Error(`unexpected fetch: ${url}`);
      }),
    );
    renderRoute();

    await waitFor(() => expect(document.querySelector('.wl-orb-group')).toBeTruthy());
    const before = calls.filter((u) => u.includes('category=albums')).length;

    act(() => {
      window.dispatchEvent(new CustomEvent('ss:wishlist-changed'));
    });

    await waitFor(() => {
      expect(calls.filter((u) => u.includes('category=albums')).length).toBeGreaterThan(before);
    });
  });

  it('keeps the audiobook tab on its original presentation', async () => {
    stubFetch();
    renderRoute(['/wishlist?media=audiobooks']);

    // Legacy header, not the music hero: the star title and the count meta.
    await waitFor(() =>
      expect(document.querySelector('.wishlist-page-title')?.textContent).toContain('Wishlist'),
    );
    expect(document.querySelector('.wlp-hero')).not.toBeInTheDocument();
    // Its original Clear All action is still there.
    expect(screen.getByRole('button', { name: 'Clear All' })).toBeInTheDocument();
  });

  it('redirects away when the profile may not see the wishlist', async () => {
    stubFetch();
    window.SoulSyncWebShellBridge = createShellBridge({ isPageAllowed: (p) => p !== 'wishlist' });
    const { history } = renderRoute();
    await waitFor(() => expect(history.location.pathname).not.toBe('/wishlist'));
  });

  it('offers a one-click retry for every stuck track from the triage banner', async () => {
    const bulkCalls: { action: string; ids: string[] }[] = [];
    stubFetch({
      albums: [
        albumRow('Aphex Twin', 'SAW', 'Xtal', 5),
        albumRow('Boards of Canada', 'MHTRTC', 'Roygbiv', 0),
      ],
    });
    const base = globalThis.fetch as unknown as (
      i: RequestInfo | URL,
      init?: RequestInit,
    ) => Promise<Response>;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (i: RequestInfo | URL, init?: RequestInit) => {
        const url = i instanceof Request ? i.url : String(i);
        if (url.includes('wishlist/bulk')) {
          // ky POSTs a Request object; the JSON body lives on it, not init.
          const body = (await (i as Request).json()) as {
            action: string;
            track_ids: string[];
          };
          bulkCalls.push({ action: body.action, ids: body.track_ids });
          return res({
            success: true,
            results: body.track_ids.map((id) => ({ id, ok: true, message: 'Retrying' })),
          });
        }
        return base(i, init);
      }),
    );
    renderRoute();

    // The banner names the stuck count…
    await waitFor(() => expect(screen.getByText('Retry all now')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent('1 track keeps failing');

    fireEvent.click(screen.getByText('Retry all now'));
    await waitFor(() => expect(bulkCalls).toHaveLength(1));
    // …and retries ONLY the stuck track, not the healthy one.
    expect(bulkCalls[0].action).toBe('retry');
    expect(bulkCalls[0].ids).toEqual(['Aphex Twin-SAW-Xtal']);
  });

  it('grabs a whole artist and a single album from the orb fan', async () => {
    const bulkCalls: { action: string; ids: string[] }[] = [];
    stubFetch({
      albums: [albumRow('Aphex Twin', 'SAW', 'Xtal'), albumRow('Aphex Twin', 'SAW', 'Ageispolis')],
    });
    const base = globalThis.fetch as unknown as (
      i: RequestInfo | URL,
      init?: RequestInit,
    ) => Promise<Response>;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (i: RequestInfo | URL, init?: RequestInit) => {
        const url = i instanceof Request ? i.url : String(i);
        if (url.includes('wishlist/bulk')) {
          // ky POSTs a Request object; the JSON body lives on it, not init.
          const body = (await (i as Request).json()) as {
            action: string;
            track_ids: string[];
          };
          bulkCalls.push({ action: body.action, ids: body.track_ids });
          return res({
            success: true,
            results: body.track_ids.map((id) => ({ id, ok: true, message: 'Queued' })),
          });
        }
        return base(i, init);
      }),
    );
    renderRoute();

    await waitFor(() => expect(document.querySelector('.wl-orb')).toBeTruthy());
    fireEvent.click(document.querySelector('.wl-orb')!);

    // Fan header: one click queues the artist's whole wishlist share.
    fireEvent.click(screen.getByLabelText('Download all tracks by Aphex Twin now'));
    await waitFor(() => expect(bulkCalls).toHaveLength(1));
    expect(bulkCalls[0]).toEqual({
      action: 'grab',
      ids: ['Aphex Twin-SAW-Xtal', 'Aphex Twin-SAW-Ageispolis'],
    });

    // Album tile: grab just that album.
    fireEvent.click(screen.getByLabelText('Download album SAW now'));
    await waitFor(() => expect(bulkCalls).toHaveLength(2));
    expect(bulkCalls[1].action).toBe('grab');
    expect(bulkCalls[1].ids).toHaveLength(2);
  });

  it('removes a whole artist by exact track id, never by album name', async () => {
    const bulkCalls: { action: string; ids: string[] }[] = [];
    stubFetch({
      albums: [albumRow('Aphex Twin', 'SAW', 'Xtal'), albumRow('Aphex Twin', 'SAW', 'Ageispolis')],
    });
    const base = globalThis.fetch as unknown as (
      i: RequestInfo | URL,
      init?: RequestInit,
    ) => Promise<Response>;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (i: RequestInfo | URL, init?: RequestInit) => {
        const url = i instanceof Request ? i.url : String(i);
        if (url.includes('wishlist/bulk')) {
          // ky POSTs a Request object; the JSON body lives on it, not init.
          const body = (await (i as Request).json()) as {
            action: string;
            track_ids: string[];
          };
          bulkCalls.push({ action: body.action, ids: body.track_ids });
          return res({
            success: true,
            results: body.track_ids.map((id) => ({ id, ok: true, message: 'Skipped' })),
          });
        }
        // The name-based album endpoint must NOT be touched: it matches by
        // album title across the whole wishlist, so a shared title would
        // nuke another artist's tracks.
        if (url.includes('remove-album')) {
          throw new Error('remove-album must not be used for artist removal');
        }
        return base(i, init);
      }),
    );
    window.showConfirmDialog = vi.fn(async () => true);
    renderRoute();

    await waitFor(() => expect(document.querySelector('.wl-orb')).toBeTruthy());
    fireEvent.click(document.querySelector('.wl-orb')!);
    fireEvent.click(screen.getByLabelText('Remove Aphex Twin from the wishlist'));

    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    await waitFor(() => expect(bulkCalls).toHaveLength(1));
    // Exact ids, one call — no per-album name matching.
    expect(bulkCalls[0]).toEqual({
      action: 'skip',
      ids: ['Aphex Twin-SAW-Xtal', 'Aphex Twin-SAW-Ageispolis'],
    });
  });

  it('sorts the nebula without touching the list sort', async () => {
    stubFetch({
      albums: [
        albumRow('Boards of Canada', 'MHTRTC', 'Roygbiv'),
        albumRow('Aphex Twin', 'SAW', 'Xtal'),
      ],
    });
    renderRoute();

    await waitFor(() => expect(document.querySelectorAll('.wl-orb-group')).toHaveLength(2));
    const names = () =>
      [...document.querySelectorAll('.wl-orb-group')].map(
        (o) => o.getAttribute('data-artist') ?? '',
      );
    // Both hold one track: busiest-first keeps API order.
    expect(names()).toEqual(['Boards of Canada', 'Aphex Twin']);

    fireEvent.change(screen.getByLabelText('Sort nebula'), { target: { value: 'name' } });
    expect(names()).toEqual(['Aphex Twin', 'Boards of Canada']);
  });
});

describe('wishlist route survives a backend outage', () => {
  /**
   * The loader WARMS the cache; it must not gate the route. With Promise.all a
   * single failing request rejected the loader and TanStack replaced the whole
   * page with defaultErrorComponent — where the vanilla page stayed usable.
   *
   * Asserting the absence of that fallback also catches the other half: a page
   * that crashes on missing data throws into the same boundary.
   */
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
  });

  it('renders the page instead of "Something went wrong"', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: 'backend down' }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    );

    const { router } = renderRoute(['/wishlist']);

    // Positive proof the PAGE component mounted — useReactPageShell calls this
    // on mount, so it cannot pass while the error component is showing instead.
    await waitFor(() =>
      expect(window.SoulSyncWebShellBridge!.showReactHost).toHaveBeenCalledWith('wishlist'),
    );
    expect(screen.queryByText('Something went wrong')).toBeNull();
    expect(router.state.location.pathname).toBe('/wishlist');
  });
});
