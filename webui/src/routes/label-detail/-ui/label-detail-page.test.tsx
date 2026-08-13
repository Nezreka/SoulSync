import { createMemoryHistory } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * Rendered through the real router: the page calls useReactPageShell, which
 * needs a RouterProvider, and the route is what supplies the id + name. The
 * same harness artist-detail uses.
 */
function renderLabel(entry = '/label-detail/mb-1?name=Warp') {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

const release = (album: string, over: Record<string, unknown> = {}) => ({
  album,
  artist: 'Aphex Twin',
  year: '2001',
  ...over,
});

function stubCatalog(body: Record<string, unknown>) {
  server.use(http.get('/api/labels/:id/catalog', () => HttpResponse.json(body)));
}

function stubOwnership(albums: boolean[]) {
  server.use(http.post('/api/enhanced-search/library-check', () => HttpResponse.json({ albums })));
}

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showToast = vi.fn();
  window.updateWatchlistButtonCount = vi.fn();
  stubOwnership([]);
});

afterEach(() => {
  cleanup();
  delete window.SoulSyncWebShellBridge;
  delete window._labelDetailReturnTo;
  delete window.SoulSyncWebRouter;
  vi.unstubAllGlobals();
});

describe('LabelDetailPage', () => {
  it('shows the label, its counts and its releases', async () => {
    stubCatalog({
      label: { name: 'Warp Records' },
      total: 2,
      artist_count: 1,
      releases: [release('Drukqs'), release('Syro')],
    });

    renderLabel('/label-detail/mb-1');
    await screen.findByText('Drukqs');
    expect(screen.getByText('Warp Records')).toBeInTheDocument();
    expect(screen.getByText('2 releases · 1 artist')).toBeInTheDocument();
  });

  it('hides the counts and the toolbar until the first page lands', async () => {
    // "0 releases · 0 artists" reads as an answer rather than a wait.
    server.use(http.get('/api/labels/:id/catalog', () => new Promise(() => new Response())));
    renderLabel();
    // The router resolves the route asynchronously, so wait for the page's own
    // static chrome before asserting about what it has NOT filled in yet.
    await screen.findByText('Record Label');
    expect(document.getElementById('label-detail-meta')?.textContent).toBe('');
    expect(document.getElementById('label-detail-toolbar')).toBeNull();
    expect(screen.getByText('Loading label catalog…')).toBeInTheDocument();
  });

  it('filters to missing and back, without changing the pill counts', async () => {
    // Album titles deliberately unlike the pill labels: naming one "Owned"
    // makes queryByText match the FILTER BUTTON as well as the card.
    stubCatalog({ total: 2, releases: [release('In Library'), release('Not Yet')] });
    stubOwnership([true, false]);

    renderLabel();
    await screen.findByText('In Library');
    await waitFor(() => expect(document.getElementById('lf-count-owned')?.textContent).toBe('1'));

    fireEvent.click(document.querySelector('[data-lf="missing"]') as HTMLElement);
    expect(screen.queryByText('In Library')).toBeNull();
    expect(screen.getByText('Not Yet')).toBeInTheDocument();
    // Counts describe the catalog, not the current view.
    expect(document.getElementById('lf-count-all')?.textContent).toBe('2');
    expect(document.getElementById('lf-count-owned')?.textContent).toBe('1');
  });

  it('re-sorts without refetching', async () => {
    let calls = 0;
    server.use(
      http.get('/api/labels/:id/catalog', () => {
        calls += 1;
        return HttpResponse.json({
          total: 2,
          releases: [release('Newer', { year: '2020' }), release('Older', { year: '1990' })],
        });
      }),
    );

    renderLabel();
    await screen.findByText('Newer');
    fireEvent.change(document.getElementById('label-detail-sort') as HTMLElement, {
      target: { value: 'oldest' },
    });

    const names = Array.from(document.querySelectorAll('.album-card-name')).map(
      (n) => n.textContent,
    );
    expect(names).toEqual(['Older', 'Newer']);
    expect(calls).toBe(1);
  });

  it('marks owned releases and leaves unchecked ones bare', async () => {
    stubCatalog({ total: 1, releases: [release('Drukqs')] });
    stubOwnership([true]);

    renderLabel();
    await waitFor(() => expect(document.querySelector('.completion-overlay')).not.toBeNull());
    expect(document.querySelector('.completion-overlay')?.className).toContain('completed');
    expect(screen.getByText('✓ Owned')).toBeInTheDocument();
  });

  it('follows the label and refreshes the nav badge', async () => {
    stubCatalog({ total: 0, releases: [], is_watching: false });
    server.use(http.post('/api/labels/watchlist/add', () => HttpResponse.json({ success: true })));

    renderLabel();
    const button = await screen.findByText('Add to Watchlist');
    fireEvent.click(button);

    await screen.findByText('Watching...');
    expect(window.updateWatchlistButtonCount).toHaveBeenCalled();
    // Backlog only appears for a followed label.
    expect(document.getElementById('label-detail-backlog')?.hasAttribute('hidden')).toBe(false);
  });

  it('leaves the button alone and says so when the follow is refused', async () => {
    stubCatalog({ total: 0, releases: [], is_watching: false });
    server.use(http.post('/api/labels/watchlist/add', () => HttpResponse.json({ success: false })));

    renderLabel();
    fireEvent.click(await screen.findByText('Add to Watchlist'));

    await waitFor(() => expect(window.showToast).toHaveBeenCalled());
    expect(screen.getByText('Add to Watchlist')).toBeInTheDocument();
  });

  it('reverts the backlog toggle when the server refuses', async () => {
    stubCatalog({ total: 0, releases: [], is_watching: true, backlog: false });
    server.use(
      http.post('/api/labels/watchlist/backlog', () => HttpResponse.json({ success: false })),
    );

    renderLabel();
    const full = await screen.findByText('Full backlog');
    fireEvent.click(full);
    // Optimistic first...
    expect(full.className).toContain('active');
    // ...then put back.
    await waitFor(() => expect(full.className).not.toContain('active'));
  });

  it('opens the artist page from the card button, without opening the release', async () => {
    stubCatalog({ total: 1, releases: [release('Drukqs', { artist_id: 'mb-artist' })] });
    renderLabel();
    await screen.findByText('Drukqs');

    fireEvent.click(document.querySelector('[data-role="artist"]') as HTMLElement);
    expect(window.SoulSyncWebShellBridge?.navigateToArtistDetail).toHaveBeenCalledWith(
      'mb-artist',
      'Aphex Twin',
      'musicbrainz',
    );
    // The card's own click must not have fired underneath it.
    expect(window.showLoadingOverlay).toBeUndefined();
  });

  it('returns to the page you came from, not through history', async () => {
    // The vanilla's own comment: raw history.back() is unreliable through the
    // SPA router, which is why the origin is recorded.
    window._labelDetailReturnTo = 'watchlist';
    stubCatalog({ total: 0, releases: [] });

    // The shell installs SoulSyncWebRouter at app start; under the test router
    // it is absent, so the page's optional call would silently no-op.
    const navigateToPage = vi.fn();
    window.SoulSyncWebRouter = { navigateToPage } as unknown as Window['SoulSyncWebRouter'];

    renderLabel();
    await screen.findByText('Record Label');
    fireEvent.click(document.getElementById('label-detail-back-btn') as HTMLElement);
    expect(navigateToPage).toHaveBeenCalledWith('watchlist');
  });

  it('falls back to search when nothing recorded an origin', async () => {
    stubCatalog({ total: 0, releases: [] });

    // The shell installs SoulSyncWebRouter at app start; under the test router
    // it is absent, so the page's optional call would silently no-op.
    const navigateToPage = vi.fn();
    window.SoulSyncWebRouter = { navigateToPage } as unknown as Window['SoulSyncWebRouter'];

    renderLabel();
    await screen.findByText('Record Label');
    fireEvent.click(document.getElementById('label-detail-back-btn') as HTMLElement);
    expect(navigateToPage).toHaveBeenCalledWith('search');
  });

  it('names the filter in the empty state, not just "nothing here"', async () => {
    stubCatalog({ total: 1, releases: [release('Drukqs')] });
    stubOwnership([false]);

    renderLabel();
    await screen.findByText('Drukqs');
    fireEvent.click(document.querySelector('[data-lf="owned"]') as HTMLElement);
    // Tells the user their FILTER is empty, not the label.
    expect(screen.getByText('No owned releases in this label.')).toBeInTheDocument();
  });

  it('reports a failed catalog without pretending the label is empty', async () => {
    server.use(http.get('/api/labels/:id/catalog', () => HttpResponse.error()));
    renderLabel();
    await screen.findByText('Could not load this label’s catalog.');
    expect(screen.queryByText('No releases to show.')).toBeNull();
    expect(document.getElementById('label-detail-toolbar')).toBeNull();
  });
});
