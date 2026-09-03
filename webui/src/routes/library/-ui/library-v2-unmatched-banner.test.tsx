import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * The #1202 banner, on the Library-v2 page.
 *
 * Upstream put it on the legacy library page this branch deleted, so the
 * endpoint has been answering into nothing since the sync. These pin the two
 * things that make the banner worth having: it appears when there IS something
 * to fix, and it stays completely silent otherwise — a strip reading "0 tracks"
 * or "could not load" where a warning would go is worse than no strip at all.
 */

function renderLibrary() {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: ['/library'] });
  const router = createAppRouter({ history, queryClient });
  return {
    history,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

/** Everything the artist index asks for on mount, minus the summary itself. */
function baseHandlers() {
  return [
    http.get('/api/library/v2/enabled', () =>
      HttpResponse.json({ success: true, enabled: true, can_write: true }),
    ),
    http.get('/api/library/v2/mirror-status', () =>
      HttpResponse.json({ success: true, pending: 0, failed: 0 }),
    ),
    http.get('/api/library/v2/artists', () =>
      HttpResponse.json({
        success: true,
        artists: [],
        pagination: { page: 1, limit: 50, total_count: 0, total_pages: 0 },
      }),
    ),
  ];
}

describe('the unmatched-imports banner', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });

  it('says how many, and offers a way to go fix them', async () => {
    server.use(
      ...baseHandlers(),
      http.get('/api/library/unmatched-summary', () =>
        HttpResponse.json({ success: true, count: 3, artist_id: 42 }),
      ),
    );
    renderLibrary();

    expect(await screen.findByText('3 tracks imported without a match')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show them' })).toBeInTheDocument();
  });

  it('opens the Library-v2 artist holding them, not a legacy detail page', async () => {
    // Upstream links at /artist-detail/library/<id>. That route does not exist
    // here, and the id is a v2 id either way — the link has to stay in V2.
    server.use(
      ...baseHandlers(),
      http.get('/api/library/unmatched-summary', () =>
        HttpResponse.json({ success: true, count: 2, artist_id: 42 }),
      ),
      http.get('/api/library/v2/artists/42', () =>
        HttpResponse.json({ success: false, error: 'not needed for this assertion' }),
      ),
    );
    const { history } = renderLibrary();

    const button = await screen.findByRole('button', { name: 'Show them' });
    button.click();

    // The router serialises search into the URL; assert on that rather than a
    // parsed object, so a change in how the param is encoded is visible here.
    await waitFor(() => expect(String(history.location.search)).toContain('artist=42'));
  });

  it('uses the singular for one track', async () => {
    server.use(
      ...baseHandlers(),
      http.get('/api/library/unmatched-summary', () =>
        HttpResponse.json({ success: true, count: 1, artist_id: 7 }),
      ),
    );
    renderLibrary();
    expect(await screen.findByText('1 track imported without a match')).toBeInTheDocument();
  });

  it('renders nothing for a clean library', async () => {
    server.use(
      ...baseHandlers(),
      http.get('/api/library/unmatched-summary', () =>
        HttpResponse.json({ success: true, count: 0, artist_id: null }),
      ),
    );
    const { container } = renderLibrary();
    await screen.findByPlaceholderText('Filter artists…');
    expect(container.querySelector('.library-unmatched-banner')).toBeNull();
  });

  it('stays silent when the endpoint fails', async () => {
    server.use(
      ...baseHandlers(),
      http.get('/api/library/unmatched-summary', () =>
        HttpResponse.json({ success: false, error: 'boom' }, { status: 500 }),
      ),
    );
    const { container } = renderLibrary();
    await screen.findByPlaceholderText('Filter artists…');
    expect(container.querySelector('.library-unmatched-banner')).toBeNull();
  });

  it('says nothing when there is a count but nowhere to send you', async () => {
    // Without an artist to open, "3 tracks need fixing" is a dead end.
    server.use(
      ...baseHandlers(),
      http.get('/api/library/unmatched-summary', () =>
        HttpResponse.json({ success: true, count: 3, artist_id: null }),
      ),
    );
    const { container } = renderLibrary();
    await screen.findByPlaceholderText('Filter artists…');
    expect(container.querySelector('.library-unmatched-banner')).toBeNull();
  });
});
