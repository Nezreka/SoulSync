import { createMemoryHistory } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * the audiobook tab of the wishlist page, driven through the real route.
 *
 * the layout test reads the source; this one exercises the behaviour a user
 * sees: the status filters, "Look again" on the rows that can be wanted
 * again (cancelled downloads are never retried on their own, and a miss
 * waits out a backoff), and the search-now notice.
 */

const res = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

function entry(asin: string, title: string, status: string, over: Record<string, unknown> = {}) {
  return {
    id: asin,
    asin,
    profile_id: 1,
    title,
    subtitle: '',
    authors: ['Brandon Sanderson'],
    narrators: ['Michael Kramer'],
    series_title: 'The Mistborn Saga',
    series_sequence: '1',
    cover_url: '',
    runtime_minutes: 1479,
    release_date: '2006-07-17',
    language: 'english',
    status,
    download_status: null,
    narrator_mode: 'exact',
    attempt_count: 3,
    last_attempt_at: Date.now() / 1000 - 3600,
    last_error: status === 'failed' ? 'No releases found' : '',
    added_at: 1,
    ...over,
  };
}

type Recorded = { method: string; url: string; body: string };

function stubFetch(items: ReturnType<typeof entry>[], record: Recorded[] = []) {
  const counts: Record<string, number> = {
    wanted: 0,
    searching: 0,
    grabbed: 0,
    done: 0,
    failed: 0,
    cancelled: 0,
    total: items.length,
  };
  for (const item of items) counts[item.status] = (counts[item.status] ?? 0) + 1;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(String(input), init);
      const body = request.method === 'GET' ? '' : await request.clone().text();
      record.push({ method: request.method, url: request.url, body });
      const url = request.url;
      if (/\/api\/audiobooks\/wishlist\/[^/]+\/search/.test(url) && request.method === 'POST')
        return res({ success: true, outcome: { asin: 'B1', found: 1, grabbed: true } });
      if (url.includes('/api/audiobooks/wishlist/search'))
        return res({ success: true, summary: { checked: 2, grabbed: 1 } });
      if (url.includes('/api/audiobooks/wishlist/') && request.method === 'PATCH')
        return res({ success: true, status: 'wanted' });
      if (url.includes('/api/audiobooks/wishlist/') && request.method === 'DELETE')
        return res({ success: true, removed: true });
      if (url.includes('/api/audiobooks/wishlist'))
        return res({
          success: true,
          items,
          counts,
          worker: {
            retry_after_seconds: 21600,
            batch_size: 5,
            action_type: 'audiobook_process_wishlist',
            automation_name: 'Auto-Process Audiobook Wishlist',
          },
        });
      if (url.includes('/api/wishlist/stats'))
        return res({
          total: 0,
          albums: 0,
          singles: 0,
          next_run_in_seconds: 0,
          is_auto_processing: false,
        });
      if (url.includes('/api/active-processes')) return res({ active_processes: [] });
      if (url.includes('/api/wishlist/cycle')) return res({ cycle: 'albums' });
      if (url.includes('/api/watchlist/artists')) return res({ success: true, artists: [] });
      if (url.includes('category=')) return res({ tracks: [], artist_images: {} });
      throw new Error(`unexpected fetch: ${url}`);
    }),
  );
  return record;
}

function renderTab() {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: ['/wishlist?media=audiobooks'] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
});

describe('audiobook wishlist tab', () => {
  it('shows every book with its status and the filters that have members', async () => {
    stubFetch([
      entry('B1', 'The Final Empire', 'failed'),
      entry('B2', 'The Well of Ascension', 'grabbed', { download_status: 'downloading' }),
      entry('B3', 'The Hero of Ages', 'done'),
    ]);
    renderTab();

    await screen.findByText('The Final Empire');
    // badge + filter label each: the state is named twice on purpose
    expect(screen.getAllByText('Not found yet')).toHaveLength(2);
    expect(screen.getByText('Downloading')).toBeTruthy();
    expect(screen.getAllByText('In your library').length).toBeGreaterThanOrEqual(1);
    // filters: All + the three states present; the empty ones are not offered
    const filters = screen.getByRole('navigation', { name: 'Filter by status' });
    expect(filters.textContent).toContain('All3');
    expect(filters.textContent).toContain('Not found yet1');
    expect(filters.textContent).not.toContain('Cancelled');
    // the trail is honest about the waiting
    expect(screen.getAllByText(/Looked 3× · last 1h ago/).length).toBe(3);
  });

  it('offers Look again only where it means something, and sends the book back to wanted', async () => {
    const record = stubFetch([
      entry('B1', 'The Final Empire', 'failed'),
      entry('B2', 'The Well of Ascension', 'cancelled'),
      entry('B3', 'The Hero of Ages', 'wanted'),
      entry('B4', 'Elantris', 'done'),
    ]);
    renderTab();
    await screen.findByText('The Final Empire');

    const buttons = screen.getAllByRole('button', { name: 'Look again' });
    expect(buttons).toHaveLength(2);

    fireEvent.click(buttons[1]);
    await waitFor(() => {
      const patch = record.find((r) => r.method === 'PATCH');
      expect(patch?.url).toContain('/api/audiobooks/wishlist/B2');
    });
    const patch = record.find((r) => r.method === 'PATCH')!;
    expect(JSON.parse(patch.body)).toEqual({ status: 'wanted' });
    // the row flips at once: its button is gone and it reads Looking (the
    // filter counts come from the server and refresh on the next load)
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: 'Look again' })).toHaveLength(1),
    );
    expect(screen.getAllByText('Looking').length).toBeGreaterThanOrEqual(2);
  });

  it('search now reports what the pass did', async () => {
    stubFetch([entry('B1', 'The Final Empire', 'failed')]);
    renderTab();
    await screen.findByText('The Final Empire');
    fireEvent.click(screen.getByRole('button', { name: 'Search now' }));
    await screen.findByText('Checked 2, sent 1 to downloads.');
  });

  it('remove takes the row off the list', async () => {
    const record = stubFetch([entry('B1', 'The Final Empire', 'failed')]);
    renderTab();
    await screen.findByText('The Final Empire');
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(record.some((r) => r.method === 'DELETE')).toBe(true));
    expect(record.find((r) => r.method === 'DELETE')?.url).toContain('/api/audiobooks/wishlist/B1');
  });

  it('quick remove corner button takes the row off the list', async () => {
    const record = stubFetch([entry('B1', 'The Final Empire', 'failed')]);
    renderTab();
    await screen.findByText('The Final Empire');
    fireEvent.click(screen.getByRole('button', { name: 'Remove The Final Empire from wishlist' }));
    await waitFor(() => expect(record.some((r) => r.method === 'DELETE')).toBe(true));
    expect(record.find((r) => r.method === 'DELETE')?.url).toContain('/api/audiobooks/wishlist/B1');
  });

  it('filters books in realtime using the search input', async () => {
    stubFetch([
      entry('B1', 'The Final Empire', 'failed'),
      entry('B2', 'The Way of Kings', 'wanted'),
    ]);
    renderTab();
    await screen.findByText('The Final Empire');
    await screen.findByText('The Way of Kings');

    const searchInput = screen.getByRole('textbox', { name: 'Filter audiobook wishlist' });
    fireEvent.change(searchInput, { target: { value: 'Kings' } });

    expect(screen.queryByText('The Final Empire')).toBeNull();
    expect(screen.getByText('The Way of Kings')).toBeTruthy();
  });

  it('targeted book search triggers individual search and grab', async () => {
    const record = stubFetch([entry('B1', 'The Final Empire', 'failed')]);
    renderTab();
    await screen.findByText('The Final Empire');
    fireEvent.click(screen.getByRole('button', { name: 'Search for The Final Empire now' }));
    await waitFor(() =>
      expect(
        record.some(
          (r) => r.method === 'POST' && r.url.includes('/api/audiobooks/wishlist/B1/search'),
        ),
      ).toBe(true),
    );
    await screen.findByText('Found and sent to downloads!');
  });
});
