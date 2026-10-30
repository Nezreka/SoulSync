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

    expect(await screen.findByText('3 tracks need a match')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review' })).toBeInTheDocument();
  });

  it('opens a review of the files, with the evidence and the fix for each', async () => {
    // it used to open a made-up "Unknown Artist" page and leave the user to
    // work out what each file was
    server.use(
      ...baseHandlers(),
      http.get('/api/library/unmatched-summary', () =>
        HttpResponse.json({ success: true, count: 2, artist_id: 42 }),
      ),
      http.get('/api/library/v2/unmatched', () =>
        HttpResponse.json({
          success: true,
          tracks: [
            {
              track_id: 1,
              file_id: 11,
              album_id: 5,
              artist_id: 42,
              file_path: '/m/Kanye West/Donda 2/15 - Suzy (1).flac',
              file_name: '15 - Suzy (1).flac',
              folder: '/m/Kanye West/Donda 2',
              format: 'FLAC',
              bitrate: 900,
              duration: 200,
              title: 'Suzy (1)',
              guess: {
                title: 'Suzy',
                track_number: 15,
                album: 'Donda 2',
                artist: 'Kanye West',
                query: 'Suzy Kanye West Donda 2',
                copy: true,
              },
              duplicate_of: {
                track_id: 9,
                title: 'Suzy',
                album_id: 3,
                album_title: 'Donda 2',
                artist_id: 8,
                artist_name: 'Kanye West',
              },
            },
            {
              track_id: 2,
              file_id: 12,
              album_id: 6,
              artist_id: 42,
              file_path: '/m/06 - Tongue.flac',
              file_name: '06 - Tongue.flac',
              folder: '/m',
              format: 'FLAC',
              bitrate: 900,
              duration: null,
              title: 'Tongue',
              guess: {
                title: 'Tongue',
                track_number: 6,
                album: null,
                artist: null,
                query: 'Tongue',
                copy: false,
              },
              duplicate_of: null,
            },
          ],
        }),
      ),
    );
    const { history } = renderLibrary();

    (await screen.findByRole('button', { name: 'Review' })).click();

    expect(
      await screen.findByRole('dialog', { name: 'Tracks without a match' }),
    ).toBeInTheDocument();
    expect(await screen.findByText('15 - Suzy (1).flac')).toBeInTheDocument();
    expect(screen.getByText(/second copy of “Suzy” on Donda 2 by Kanye West/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove duplicate' })).toBeInTheDocument();
    expect(screen.getByText(/nothing to go on but the name/)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Find match' })).toHaveLength(2);
    // no detour through a fake artist page
    expect(String(history.location.search)).not.toContain('artist=42');
  });

  it('uses the singular for one track', async () => {
    server.use(
      ...baseHandlers(),
      http.get('/api/library/unmatched-summary', () =>
        HttpResponse.json({ success: true, count: 1, artist_id: 7 }),
      ),
    );
    renderLibrary();
    expect(await screen.findByText('1 track needs a match')).toBeInTheDocument();
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
