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
              filing: null,
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
              filing: null,
            },
          ],
        }),
      ),
      http.post('/api/library/v2/unmatched/12/identify', () => {
        identified.push(12);
        return HttpResponse.json({
          success: true,
          status: 'matched',
          method: 'fingerprint',
          heard: { title: 'Tongue', artist: 'MØ', score: 0.97 },
          already_have: null,
          message: '',
          candidates: [
            {
              source: 'deezer',
              track_id: 'dz-1',
              track_title: 'Tongue',
              artist_name: 'MØ',
              album_name: 'No Mythologies to Follow',
              album_type: 'album',
              year: 2014,
              total_tracks: 12,
              image_url: '',
              isrc: null,
              confidence: 0.94,
              reasons: ['Fingerprint 97%', 'Same length'],
            },
            {
              source: 'deezer',
              track_id: 'dz-2',
              track_title: 'Tongue',
              artist_name: 'MØ',
              album_name: 'Tongue',
              album_type: 'single',
              year: 2014,
              total_tracks: 1,
              image_url: '',
              isrc: null,
              confidence: 0.7,
              reasons: ['Fingerprint 97%'],
            },
          ],
        });
      }),
      http.post('/api/library/v2/unmatched/11/identify', () => {
        identified.push(11);
        return HttpResponse.json({
          success: true,
          status: 'duplicate',
          method: 'fingerprint',
          heard: { title: 'Suzy', artist: 'Kanye West', score: 0.95 },
          already_have: {
            track_id: 9,
            title: 'Suzy',
            album_id: 3,
            album_title: 'Donda 2',
            artist_name: 'Kanye West',
          },
          candidates: [],
          message: '',
        });
      }),
      http.post('/api/reidentify/apply', async ({ request }) => {
        applied.push(await request.json());
        return HttpResponse.json({ success: true, album_name: 'No Mythologies to Follow' });
      }),
    );
    const identified: number[] = [];
    const applied: unknown[] = [];
    const { history } = renderLibrary();

    (await screen.findByRole('button', { name: 'Review' })).click();

    expect(
      await screen.findByRole('dialog', { name: 'Tracks without a match' }),
    ).toBeInTheDocument();
    expect(await screen.findByText('15 - Suzy (1).flac')).toBeInTheDocument();
    // a copy the folder already gives away needs no listening to say so
    expect(screen.getByText(/You already have this/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove duplicate' })).toBeInTheDocument();
    // the untagged one is fingerprinted and its likely release offered
    expect(
      await screen.findByText('No Mythologies to Follow · MØ · Album · 2014'),
    ).toBeInTheDocument();
    expect(screen.getByText('Strong match')).toBeInTheDocument();
    expect(screen.getByText('Fingerprint 97%')).toBeInTheDocument();
    // one lookup at a time, in list order
    await waitFor(() => expect(identified).toEqual([11, 12]));
    // the other release is one click away
    screen.getByRole('button', { name: '1 other match' }).click();
    expect(await screen.findByText('Tongue · MØ · Single · 2014')).toBeInTheDocument();
    screen.getByRole('button', { name: 'Accept' }).click();
    expect(await screen.findByText(/Filing under No Mythologies to Follow/)).toBeInTheDocument();
    expect(applied).toEqual([
      { library_track_id: 2, source: 'deezer', track_id: 'dz-1', replace: true },
    ]);
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
