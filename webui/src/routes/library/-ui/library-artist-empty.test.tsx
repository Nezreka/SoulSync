import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * review 5 R5-L-2: an artist filter with no hits said "Your library is empty"
 * and offered Import. only a library with no artists at all may say that.
 */

let totalCount = 0;

function stubFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      let body: unknown = {};
      if (url.includes('/api/library/v2/enabled')) {
        body = { success: true, enabled: true, can_write: true };
      } else if (url.includes('/api/library/v2/import/status')) {
        body = { running: false, stage: 'idle', artwork_cache: { running: false } };
      } else if (url.includes('/api/library/v2/mirror-status')) {
        body = { success: true, pending: 0, failed: 0 };
      } else if (url.includes('/api/library/v2/artists')) {
        body = {
          success: true,
          artists: [],
          pagination: {
            page: 1,
            limit: 75,
            total_count: totalCount,
            total_pages: 1,
            has_prev: false,
            has_next: false,
          },
        };
      } else if (url.includes('/api/library/unmatched-summary')) {
        body = { success: true, count: 0, artist_id: null };
      }
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

function renderPage(entry: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

beforeEach(() => {
  totalCount = 0;
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showLibraryDownloadsSection = vi.fn();
  window.showToast = vi.fn();
  stubFetch();
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
  delete window.showLibraryDownloadsSection;
});

describe('an artist view with no rows', () => {
  it.each([
    ['watched only', '/library?monitored=monitored'],
    ['a letter', '/library?letter=q'],
    ['a source', '/library?source=plex'],
    ['could be better', '/library?quality=upgradable'],
  ])('%s with no hits says no artists match, not import', async (_label, entry) => {
    renderPage(entry);
    expect(await screen.findByText('No artists match this view')).toBeTruthy();
    expect(screen.getByText('Reset filters')).toBeTruthy();
    expect(screen.queryByText('Your library is empty')).toBeNull();
  });

  it('a page past the end of a full library is not an empty library', async () => {
    totalCount = 120;
    renderPage('/library?page=9');
    expect(await screen.findByText('No artists match this view')).toBeTruthy();
    expect(screen.queryByText('Your library is empty')).toBeNull();
  });

  it('a library with no artists at all still offers the import', async () => {
    renderPage('/library');
    expect(await screen.findByText('Your library is empty')).toBeTruthy();
    expect(screen.queryByText('No artists match this view')).toBeNull();
  });
});
