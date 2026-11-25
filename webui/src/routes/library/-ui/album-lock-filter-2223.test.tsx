import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * #2223: "a filter in the album view to show only locked or unlocked albums".
 * the artist list had it (#2046, as Reviewed); the album list now shares the
 * same `review` filter, labelled by the padlock, sent to the server (the list
 * is paged there) and kept in the URL with the other filters.
 */

function renderRoute(entry: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return { router, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

let requested: string[] = [];
const albumRequests = () =>
  requested.filter((u) => u.includes('library/v2/albums')).map((u) => new URL(u, 'http://x'));
const page = {
  page: 1,
  limit: 75,
  total_count: 1,
  total_pages: 1,
  has_prev: false,
  has_next: false,
};

describe('#2223: the album list filters by lock', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    requested = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        requested.push(url);
        let body: unknown = { success: true };
        if (url.includes('library/v2/enabled')) {
          body = { success: true, enabled: true, can_write: true };
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
            pagination: page,
          };
        } else if (url.includes('library/v2/artists')) {
          body = { success: true, artists: [{ id: 1, name: 'Aphex Twin' }], pagination: page };
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
  });

  it('a filter in the URL reaches the server and shows as a chip', async () => {
    renderRoute('/library?section=albums&review=reviewed&monitored=monitored');
    await screen.findByRole('button', { name: 'Remove filter: Locked' }, { timeout: 10_000 });
    const sent = albumRequests().at(-1)!;
    expect(sent.searchParams.get('review')).toBe('reviewed');
    // it stacks with the others
    expect(sent.searchParams.get('monitored')).toBe('monitored');
  }, 30_000);

  it('picking Locked, then Unlocked, refetches and keeps it in the URL', async () => {
    const { router } = renderRoute('/library?section=albums&page=2');
    fireEvent.click(await screen.findByRole('button', { name: /Filters/ }, { timeout: 10_000 }));
    const group = screen.getByRole('group', { name: 'Filter by lock' });
    fireEvent.click(within(group).getByRole('button', { name: 'Locked' }));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ review: 'reviewed', page: 1 }),
    );
    await waitFor(() =>
      expect(albumRequests().some((u) => u.searchParams.get('review') === 'reviewed')).toBe(true),
    );
    // the Filters button counts it
    expect(screen.getByRole('button', { name: /Filters/ }).textContent).toContain('1');

    fireEvent.click(within(group).getByRole('button', { name: 'Unlocked' }));
    await waitFor(() =>
      expect(albumRequests().some((u) => u.searchParams.get('review') === 'unreviewed')).toBe(true),
    );
    expect(await screen.findByRole('button', { name: 'Remove filter: Unlocked' })).toBeTruthy();

    fireEvent.click(within(group).getByRole('button', { name: 'All' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ review: '' }));
    await waitFor(() => expect(albumRequests().at(-1)!.searchParams.has('review')).toBe(false));
  }, 30_000);

  it('the chip takes it off', async () => {
    const { router } = renderRoute('/library?section=albums&review=unreviewed');
    fireEvent.click(
      await screen.findByRole('button', { name: 'Remove filter: Unlocked' }, { timeout: 10_000 }),
    );
    await waitFor(() => expect(router.state.location.search).toMatchObject({ review: '' }));
  }, 30_000);

  it('the artist list keeps its own words for the same filter', async () => {
    renderRoute('/library?review=reviewed');
    await screen.findByRole('button', { name: 'Remove filter: Reviewed' }, { timeout: 10_000 });
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }));
    expect(screen.getByRole('group', { name: 'Filter by review' })).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'Filter by lock' })).toBeNull();
  }, 30_000);
});
