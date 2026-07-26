import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
      if (url.includes('/link-provider')) {
        return createResponse({ success: true });
      }
      if (url.includes('/config')) {
        return createResponse({
          success: true,
          config: {
            include_albums: true,
            include_eps: false,
            include_singles: true,
            include_live: false,
            include_remixes: false,
            include_acoustic: false,
            include_compilations: false,
            include_instrumentals: false,
            lookback_days: 90,
            preferred_metadata_source: null,
            auto_download: true,
            quality_profile_id: null,
          },
          artist: {
            id: 'sp-aphex',
            name: 'Aphex Twin',
            image_url: null,
            followers: 1234567,
            popularity: 71,
            genres: ['idm', 'ambient', 'braindance', 'electronic'],
          },
          spotify_artist_id: 'sp-aphex',
          itunes_artist_id: null,
          deezer_artist_id: 'dz-aphex',
          discogs_artist_id: null,
          amazon_artist_id: null,
          musicbrainz_artist_id: null,
          watchlist_name: 'Aphex Twin',
          global_metadata_source: 'deezer',
          quality_profiles: [
            { id: 1, name: 'Lossless', is_default: true },
            { id: 2, name: 'MP3 320' },
          ],
        });
      }
      if (url.includes('/api/library/search-service')) {
        return createResponse({
          success: true,
          results: [{ id: 'dz-999', name: 'Aphex Twin', extra: '2.1M fans', image: null }],
        });
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

  it('selects an artist and removes it, hitting remove-batch with its id', async () => {
    const calls = stubFetch({
      artists: [
        artistRow({ id: 1, artist_name: 'Aphex Twin', spotify_artist_id: 'sp-1' }),
        artistRow({ id: 2, artist_name: 'Boards of Canada', spotify_artist_id: 'sp-2' }),
      ],
    });
    window.showConfirmDialog = vi.fn(async () => true);
    renderWatchlistRoute();

    await waitFor(() => expect(screen.getByText('Aphex Twin')).toBeInTheDocument());

    // Remove Selected is hidden until something is ticked.
    expect(screen.queryByRole('button', { name: 'Remove Selected' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Select Aphex Twin'));
    expect(screen.getByText('1 selected')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Remove Selected' }));

    await waitFor(() => {
      expect(calls.some((url) => url.includes('/api/watchlist/remove-batch'))).toBe(true);
    });
    expect(window.showConfirmDialog).toHaveBeenCalled();
  });

  it('does not remove anything when the confirm is declined', async () => {
    const calls = stubFetch();
    window.showConfirmDialog = vi.fn(async () => false);
    renderWatchlistRoute();

    await waitFor(() => expect(screen.getByText('Aphex Twin')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Select Aphex Twin'));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Selected' }));

    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(calls.some((url) => url.includes('remove-batch'))).toBe(false);
  });

  it('Select All takes only the visible artists when a filter is applied', async () => {
    stubFetch({
      artists: [
        artistRow({ id: 1, artist_name: 'Aphex Twin', spotify_artist_id: 'sp-1' }),
        artistRow({ id: 2, artist_name: 'Boards of Canada', spotify_artist_id: 'sp-2' }),
        artistRow({ id: 3, artist_name: 'Clark', spotify_artist_id: 'sp-3' }),
      ],
    });
    renderWatchlistRoute(['/watchlist?q=canada']);

    await waitFor(() => expect(screen.getByText('Boards of Canada')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('Select all visible artists'));

    // One visible, so one selected — not all three. This is what stops a
    // filtered Select All from wiping the whole watchlist.
    expect(screen.getByText('1 selected')).toBeInTheDocument();
  });

  it('ticking the checkbox does not also open the artist detail view', async () => {
    stubFetch();
    const { router } = renderWatchlistRoute();

    await waitFor(() => expect(screen.getByText('Aphex Twin')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Select Aphex Twin'));

    expect(screen.getByText('1 selected')).toBeInTheDocument();
    // detailId in the router state is what opens the detail view; the checkbox
    // has to stop the card's own click from firing. Asserted on router state
    // rather than window.location, which memory history never touches.
    expect(router.state.location.search).not.toMatchObject({ detailId: 'sp-aphex' });
  });

  it('the gear opens artist config, the card body opens artist detail', async () => {
    stubFetch();
    const { router } = renderWatchlistRoute();

    await waitFor(() => expect(screen.getByText('Aphex Twin')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Settings for Aphex Twin' }));
    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ configId: 'sp-aphex' });
    });
    // The gear must not also trip the card's detail click.
    expect(router.state.location.search).not.toMatchObject({ detailId: 'sp-aphex' });

    fireEvent.click(screen.getByText('Aphex Twin'));
    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ detailId: 'sp-aphex' });
    });
  });

  it('renders followed labels and switches the count chip to labels', async () => {
    stubFetch({
      labels: [
        {
          id: 5,
          musicbrainz_label_id: 'mb-warp',
          discogs_label_id: null,
          label_name: 'Warp Records',
          source: 'musicbrainz',
          backlog: true,
          date_added: '2026-01-01T00:00:00Z',
          last_scan_timestamp: null,
        },
      ],
    });
    renderWatchlistRoute(['/watchlist?tab=labels']);

    await waitFor(() => expect(screen.getByText('Warp Records')).toBeInTheDocument());

    expect(screen.getByText('1 label')).toBeInTheDocument();
    expect(screen.getByText('Full backlog')).toBeInTheDocument();
    expect(screen.getByText('Not scanned yet')).toBeInTheDocument();
  });

  it('toggles label backlog without opening the label', async () => {
    const calls = stubFetch({
      labels: [
        {
          id: 5,
          musicbrainz_label_id: 'mb-warp',
          discogs_label_id: null,
          label_name: 'Warp Records',
          source: 'musicbrainz',
          backlog: false,
          date_added: null,
          last_scan_timestamp: null,
        },
      ],
    });
    renderWatchlistRoute(['/watchlist?tab=labels']);

    await waitFor(() => expect(screen.getByText('Warp Records')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('Monitoring new releases only — click for full backlog'));

    await waitFor(() => {
      expect(calls.some((url) => url.includes('/api/labels/watchlist/backlog'))).toBe(true);
    });
    // The action buttons sit inside the card; they must not navigate.
    expect(window.SoulSyncWebShellBridge?.navigateToLabelDetail).not.toHaveBeenCalled();
  });

  it('opens the label when the card body is clicked', async () => {
    stubFetch({
      labels: [
        {
          id: 5,
          musicbrainz_label_id: 'mb-warp',
          discogs_label_id: null,
          label_name: 'Warp Records',
          source: 'musicbrainz',
          backlog: false,
          date_added: null,
          last_scan_timestamp: null,
        },
      ],
    });
    renderWatchlistRoute(['/watchlist?tab=labels']);

    await waitFor(() => expect(screen.getByText('Warp Records')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Warp Records'));

    expect(window.SoulSyncWebShellBridge?.navigateToLabelDetail).toHaveBeenCalledWith(
      'mb-warp',
      'Warp Records',
    );
  });

  it('shows the labels empty state when nothing is followed', async () => {
    stubFetch({ labels: [] });
    renderWatchlistRoute(['/watchlist?tab=labels']);

    await waitFor(() => {
      expect(screen.getByText('No labels followed yet')).toBeInTheDocument();
    });
    expect(screen.getByText('0 labels')).toBeInTheDocument();
  });

  it('opens global settings from the URL and saves', async () => {
    const calls = stubFetch({ globalOverride: true });
    renderWatchlistRoute(['/watchlist?settings=true']);

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Global Watchlist Settings' })).toBeInTheDocument();
    });

    // Populated from the loaded config, not from blank defaults.
    const overrideToggle = screen.getByRole('checkbox', { name: /Enable Global Override/ });
    expect(overrideToggle).toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: 'Save Global Settings' }));

    await waitFor(() => {
      const posts = calls.filter((url) => url.includes('/api/watchlist/global-config'));
      expect(posts.length).toBeGreaterThan(1);
    });
  });

  it('refuses to save an override with no release type, and says why', async () => {
    stubFetch({ globalOverride: true });
    window.showToast = vi.fn();
    renderWatchlistRoute(['/watchlist?settings=true']);

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Global Watchlist Settings' })).toBeInTheDocument();
    });

    // The accessible name is the whole label, icon included ("💿Albums Full-length
    // studio albums"), so these match on a distinctive substring.
    for (const label of [/Full-length studio albums/, /Extended plays/, /Single tracks and/]) {
      const box = screen.getByRole('checkbox', { name: label });
      if ((box as HTMLInputElement).checked) fireEvent.click(box);
    }
    fireEvent.click(screen.getByRole('button', { name: 'Save Global Settings' }));

    await waitFor(() => {
      expect(window.showToast).toHaveBeenCalledWith(
        'Please select at least one release type',
        'error',
      );
    });
  });

  it('the Global Settings button reports an active override', async () => {
    stubFetch({ globalOverride: true });
    renderWatchlistRoute();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Global Override ON/ })).toBeInTheDocument();
    });
  });

  it('opens artist config from the URL with the stored values', async () => {
    stubFetch();
    renderWatchlistRoute(['/watchlist?configId=sp-aphex']);

    // Waiting on the dialog alone is not enough: it mounts before its config
    // query resolves, so assertions on stored values would race the fetch.
    await waitFor(() => {
      expect(screen.getByLabelText('Quality Profile')).toBeInTheDocument();
    });

    expect(screen.getByText('1,234,567')).toBeInTheDocument();
    expect(screen.getByText('71/100')).toBeInTheDocument();
    // Genres cap at three even when the payload carries four.
    expect(screen.getByText('braindance')).toBeInTheDocument();
    expect(screen.queryByText('electronic')).not.toBeInTheDocument();

    // Stored config, not defaults: EPs off, singles on, lookback 90.
    expect(screen.getByRole('checkbox', { name: /Extended plays/ })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Single tracks and/ })).toBeChecked();
    expect(screen.getByLabelText('Scan lookback')).toHaveValue('90');
  });

  it('defaults the quality profile to Use default rather than the first profile', async () => {
    stubFetch();
    renderWatchlistRoute(['/watchlist?configId=sp-aphex']);

    // Waiting on the dialog alone is not enough: it mounts before its config
    // query resolves, so assertions on stored values would race the fetch.
    await waitFor(() => {
      expect(screen.getByLabelText('Quality Profile')).toBeInTheDocument();
    });

    // quality_profile_id is null, so the select must sit on "" — landing on
    // "Lossless" would silently pin a profile the user never chose.
    expect(screen.getByLabelText('Quality Profile')).toHaveValue('');
    expect(screen.getByRole('option', { name: 'Use default' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Lossless (Default)' })).toBeInTheDocument();
  });

  it('only offers matched providers as a scan source', async () => {
    stubFetch();
    renderWatchlistRoute(['/watchlist?configId=sp-aphex']);

    // Waiting on the dialog alone is not enough: it mounts before its config
    // query resolves, so assertions on stored values would race the fetch.
    await waitFor(() => {
      expect(screen.getByLabelText('Quality Profile')).toBeInTheDocument();
    });

    // Matched on Spotify + Deezer only.
    expect(screen.getByRole('button', { name: 'Spotify' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Deezer' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Apple Music' })).not.toBeInTheDocument();
    // And the global default is named, not just "Default".
    expect(screen.getByRole('button', { name: /Default \(Deezer\)/ })).toBeInTheDocument();
  });

  it('saves the artist config with the edited values', async () => {
    const calls = stubFetch();
    renderWatchlistRoute(['/watchlist?configId=sp-aphex']);

    // Waiting on the dialog alone is not enough: it mounts before its config
    // query resolves, so assertions on stored values would race the fetch.
    await waitFor(() => {
      expect(screen.getByLabelText('Quality Profile')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox', { name: /Extended plays/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Preferences' }));

    await waitFor(() => {
      expect(calls.filter((url) => url.includes('/config')).length).toBeGreaterThan(1);
    });
  });

  it('refuses to save an artist with no release type', async () => {
    stubFetch();
    window.showToast = vi.fn();
    renderWatchlistRoute(['/watchlist?configId=sp-aphex']);

    // Waiting on the dialog alone is not enough: it mounts before its config
    // query resolves, so assertions on stored values would race the fetch.
    await waitFor(() => {
      expect(screen.getByLabelText('Quality Profile')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox', { name: /Full-length studio albums/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: /Single tracks and/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Preferences' }));

    await waitFor(() => {
      expect(window.showToast).toHaveBeenCalledWith(
        'Please select at least one release type',
        'error',
      );
    });
  });

  it('shows matched and unmatched providers, and links a new one', async () => {
    const calls = stubFetch();
    renderWatchlistRoute(['/watchlist?configId=sp-aphex']);

    // Waiting on the dialog alone is not enough: it mounts before its config
    // query resolves, so assertions on stored values would race the fetch.
    await waitFor(() => {
      expect(screen.getByLabelText('Quality Profile')).toBeInTheDocument();
    });

    // Matched rows offer Fix + clear; unmatched offer Match.
    expect(screen.getAllByRole('button', { name: 'Fix' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Match' })).toHaveLength(4);
    expect(screen.getByLabelText('Clear Spotify match')).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'Match' })[0]);
    // The search box seeds with the stored watchlist name.
    expect(screen.getByLabelText('Search Apple Music')).toHaveValue('Aphex Twin');

    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => {
      expect(screen.getByText('2.1M fans')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Select' }));
    await waitFor(() => {
      expect(calls.some((url) => url.includes('/link-provider'))).toBe(true);
    });
  });

  it('clears a provider match through the same endpoint', async () => {
    const calls = stubFetch();
    renderWatchlistRoute(['/watchlist?configId=sp-aphex']);

    await waitFor(() => {
      expect(screen.getByLabelText('Clear Spotify match')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText('Clear Spotify match'));

    await waitFor(() => {
      expect(calls.some((url) => url.includes('/link-provider'))).toBe(true);
    });
  });

  it('warns inside the config modal while the global override is on', async () => {
    stubFetch({ globalOverride: true });
    renderWatchlistRoute(['/watchlist?configId=sp-aphex']);

    await waitFor(() => {
      expect(
        screen.getByText(/these per-artist settings are currently ignored during scans/),
      ).toBeInTheDocument();
    });
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
