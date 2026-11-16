import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * /library is LIVE — the manifest hands it to React, and the page behind it is
 * Library v2, which replaced the vanilla list rather than porting it.
 *
 * What this file guards is unchanged from the port's own route suite: the route
 * must not hand the page back to vanilla, validateSearch must survive whatever
 * is in the URL, and the permission gate still applies. Only the page and the
 * endpoint it loads from changed.
 */

function renderRoute(entries = ['/library']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: entries });
  const router = createAppRouter({ history, queryClient });
  return { router, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

let requested: string[] = [];
let requestedWrites: string[] = [];
let canWrite = true;
let artistRows: Record<string, unknown>[] = [{ id: 1, name: 'Aphex Twin' }];

const artistsRequests = () => requested.filter((u) => u.includes('library/v2/artists'));

describe('library route (live)', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showLibraryDownloadsSection = vi.fn();
    requested = [];
    requestedWrites = [];
    canWrite = true;
    artistRows = [{ id: 1, name: 'Aphex Twin' }];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        requested.push(url);
        const method = input instanceof Request ? input.method : (init?.method ?? 'GET');
        if (method !== 'GET') requestedWrites.push(`${method} ${url}`);
        let body: unknown = {};
        if (url.includes('library/v2/enabled')) {
          body = { success: true, enabled: true, can_write: canWrite };
        } else if (url.includes('library/v2/albums')) {
          body = {
            success: true,
            albums: [
              {
                id: 7,
                title: 'Selected Ambient Works',
                album_type: 'album',
                year: 1992,
                image_url: null,
                added_at: null,
                monitored: false,
                artist_id: 1,
                artist_name: 'Aphex Twin',
                track_count: 13,
                tracks_present: 13,
                tracks_missing: 0,
              },
            ],
            pagination: {
              page: 1,
              limit: 75,
              total_count: 1,
              total_pages: 1,
              has_prev: false,
              has_next: false,
            },
          };
        } else if (url.includes('library/v2/artists')) {
          body = {
            success: true,
            artists: artistRows,
            pagination: {
              page: 1,
              limit: 75,
              total_count: 1,
              total_pages: 1,
              has_prev: false,
              has_next: false,
            },
          };
        }
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
    delete window.showLibraryDownloadsSection;
  });

  // The album route loads lazily; under a full-suite run that alone outlasts
  // the 1 s default of findBy/waitFor.
  it('browses the library by album and opens one (A07)', async () => {
    const { router } = renderRoute(['/library?section=albums&albumSort=year_desc']);
    fireEvent.click(
      await screen.findByRole(
        'button',
        { name: 'Open Selected Ambient Works' },
        { timeout: 10_000 },
      ),
    );
    expect(
      requested.some((u) => u.includes('library/v2/albums') && u.includes('sort=year_desc')),
    ).toBe(true);
    await waitFor(() => expect(router.state.location.search).toMatchObject({ album: 7 }), {
      timeout: 10_000,
    });
  }, 30_000);

  // Artists, Albums and Wanted each render their own header; one without the
  // `library-header` padding or with an empty subtitle moved the tabs and the
  // search box on every switch.
  it('keeps the same header in every section', async () => {
    for (const section of ['artists', 'albums', 'wanted']) {
      const { unmount } = renderRoute([`/library?section=${section}`]);
      const title = await screen.findByRole('heading', { name: 'Library' }, { timeout: 10_000 });
      const header = title.closest('header');
      expect(header?.classList.contains('library-header')).toBe(true);
      expect(header?.querySelector('p')?.textContent).not.toBe('');
      unmount();
    }
  }, 30_000);

  it('is owned by React', () => {
    expect(getShellRouteByPageId('library')?.kind).toBe('react');
  });

  it('renders the React page instead of handing /library to vanilla', async () => {
    const { router } = renderRoute();
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
    // The vanilla page must not activate underneath — it is deleted, and a
    // handoff would only blank the route.
    expect(router.state.location.pathname).toBe('/library');
    expect(window.SoulSyncWebShellBridge!.activateLegacyPath).not.toHaveBeenCalledWith('/library');
  });

  it('loads the artists through the route loader', async () => {
    renderRoute();
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
  });

  it('accepts every filter in the URL without throwing the route down', async () => {
    // An all-digits q arrives as a NUMBER from TanStack's JSON parse, which a
    // bare z.string() would reject and take the route down with.
    renderRoute(['/library?q=123&sort=name&page=3&monitored=monitored']);
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
    const url = new URL(artistsRequests()[0]!, 'http://x');
    expect(url.searchParams.get('search')).toBe('123');
    expect(url.searchParams.get('page')).toBe('3');
    expect(url.searchParams.get('monitored')).toBe('monitored');
  });

  it('falls back rather than crashing on a nonsense page number', async () => {
    renderRoute(['/library?page=notanumber']);
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
    const url = new URL(artistsRequests()[0]!, 'http://x');
    expect(url.searchParams.get('page')).toBe('1');
  });

  it('normalizes the removed import-review section without requesting it', async () => {
    renderRoute(['/library?section=import-review']);
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
    expect(requested.some((url) => url.includes('library/v2/acquisition'))).toBe(false);
    expect(requested.some((url) => url.includes('library/v2/artists'))).toBe(true);
  });

  it('fails closed when can_write is false and never submits a mutation', async () => {
    canWrite = false;
    const { container } = renderRoute();
    await screen.findByText(/Read-only: library changes require the admin profile/);
    const mutations = [...container.querySelectorAll<HTMLElement>('[data-requires-write]')];
    expect(mutations.length).toBeGreaterThan(0);
    expect(mutations.every((control) => control.matches(':disabled'))).toBe(true);
    mutations.forEach((control) => fireEvent.click(control));
    expect(requestedWrites).toEqual([]);
  });

  it('still respects the page permission gate', async () => {
    window.SoulSyncWebShellBridge = createShellBridge({ isPageAllowed: vi.fn(() => false) });
    const { router } = renderRoute();
    await waitFor(() => expect(router.state.location.pathname).not.toBe('/library'));
  });

  it("opens Search's In Your Library artist for a profile allowed Search alone", async () => {
    // third #1062 review G15-2: the card links /library?artist=<id> straight;
    // only the discovery form redirected, so the profile landed home
    window.SoulSyncWebShellBridge = createShellBridge({
      isPageAllowed: vi.fn((pageId) => pageId !== 'library'),
    });
    const { router } = renderRoute(['/library?artist=42&releases=all&releaseView=cards']);
    await waitFor(() => expect(router.state.location.pathname).toBe('/artist-detail/library/42'));
  });

  it('does not bounce a denied profile back into /library (iss29-B10)', async () => {
    // Home page resolves to `library` (directly, or via the legacy `library-v2`
    // page id the shell normalizes to it) while allowed_pages excludes it. The
    // guard used to redirect to the very page it had just refused.
    window.SoulSyncWebShellBridge = createShellBridge({
      isPageAllowed: vi.fn((pageId) => pageId !== 'library'),
      getProfileHomePage: vi.fn(() => 'library' as const),
    });
    const { router } = renderRoute();
    await waitFor(() => expect(router.state.location.pathname).not.toBe('/library'));
  });

  // #2106: the cards show the table's owned / expected, and the missing count
  // opens that artist's missing tracks
  it('shows completeness on a card and opens the missing tracks (#2106)', async () => {
    artistRows = [
      {
        id: 1,
        name: '311',
        track_count: 345,
        tracks_present: 341,
        tracks_missing: 4,
        completion: 'incomplete',
        genres: [],
      },
      {
        id: 2,
        name: '10 Years',
        track_count: 140,
        tracks_present: 140,
        completion: 'complete',
        genres: [],
      },
      {
        id: 3,
        name: 'Nothing Yet',
        track_count: 0,
        tracks_present: 0,
        completion: 'unknown',
        genres: [],
      },
    ];
    const { router } = renderRoute(['/library']);
    expect(await screen.findByText('341 / 345 tracks', {}, { timeout: 10_000 })).toBeTruthy();
    expect(screen.getByText(/Complete$/)).toBeTruthy();
    // nothing expected is never "complete"
    expect(screen.getByText('Nothing expected yet')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '4 missing' }));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        section: 'wanted',
        wantedKind: 'missing',
        wantedArtist: 1,
      }),
    );
    await waitFor(() =>
      expect(requested.some((u) => u.includes('library/v2/wanted') && u.includes('artist=1'))).toBe(
        true,
      ),
    );
  }, 30_000);

  it('sorts by completion either way round (#2107)', async () => {
    renderRoute(['/library']);
    const select = await screen.findByLabelText('Sort artists', {}, { timeout: 10_000 });
    fireEvent.change(select, { target: { value: 'completion' } });
    await waitFor(() =>
      expect(artistsRequests().some((u) => u.includes('sort=completion'))).toBe(true),
    );
    fireEvent.click(screen.getByRole('button', { name: /turn around/ }));
    await waitFor(() => expect(artistsRequests().some((u) => u.includes('dir=desc'))).toBe(true));
  }, 30_000);

  it('passes the collection filters from the url (#2107)', async () => {
    renderRoute(['/library?completion=incomplete&missingMin=2&pctMax=90&credit=featured']);
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
    const url = new URL(artistsRequests().at(-1)!, 'http://x');
    expect(url.searchParams.get('completion')).toBe('incomplete');
    expect(url.searchParams.get('missing_min')).toBe('2');
    expect(url.searchParams.get('pct_max')).toBe('90');
    expect(url.searchParams.get('credit')).toBe('featured');
  });

  it('opens a plain /library the way you left the list (#2107)', async () => {
    const first = renderRoute(['/library?view=table&sort=missing&completion=incomplete']);
    await waitFor(() => expect(artistsRequests()).not.toEqual([]));
    first.unmount();
    const { router } = renderRoute(['/library']);
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        view: 'table',
        sort: 'missing',
        completion: 'incomplete',
      }),
    );
    // anything in the url wins over what was remembered
    const again = renderRoute(['/library?sort=name']);
    await waitFor(() => expect(again.router.state.location.search).toMatchObject({ sort: 'name' }));
    expect(again.router.state.location.search).not.toHaveProperty('completion', 'incomplete');
  }, 30_000);

  it('adds several artists to the Watchlist at once (#2109)', async () => {
    artistRows = [
      { id: 1, name: 'Alpha', monitored: false, track_count: 1, tracks_present: 1, genres: [] },
      { id: 2, name: 'Beta', monitored: false, track_count: 1, tracks_present: 1, genres: [] },
      { id: 3, name: 'Gamma', monitored: true, track_count: 1, tracks_present: 1, genres: [] },
    ];
    renderRoute(['/library']);
    const first = await screen.findByLabelText('Select Alpha', {}, { timeout: 10_000 });
    fireEvent.click(first);
    // shift-click takes the range in page order
    fireEvent.click(screen.getByLabelText('Select Gamma'), { shiftKey: true });
    expect(await screen.findByText('3 artists selected')).toBeTruthy();
    // the cards have their own add buttons: this is the bar's
    const bar = screen.getByRole('region', { name: 'Selected artists' });
    fireEvent.click(within(bar).getByRole('button', { name: 'Add to Watchlist' }));
    await waitFor(() =>
      expect(requestedWrites.filter((w) => w.includes('/monitor'))).toHaveLength(2),
    );
    // Gamma was already on it: skipped, not sent
    expect(requestedWrites.some((w) => w.includes('artists/3/monitor'))).toBe(false);
  }, 30_000);
});
