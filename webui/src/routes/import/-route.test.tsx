import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createAppQueryClient } from '@/app/query-client';
import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createShellBridge } from '@/test/shell-bridge';

import type { ImportStagingFile } from './-import.types';

import { resetImportWorkflowStore } from './-import.store';

function renderImportRoute(initialEntries = ['/import']) {
  const queryClient = createAppQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });

  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

function getFetchUrls() {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => (input instanceof Request ? input.url : String(input)));
}

describe('import route', () => {
  let albumMatchBodies: Record<string, unknown>[];
  let stagingFilesPayload: ImportStagingFile[];

  beforeEach(() => {
    albumMatchBodies = [];
    stagingFilesPayload = [
      {
        filename: '01-track.flac',
        rel_path: 'Album/01-track.flac',
        full_path: '/music/Staging/Album/01-track.flac',
        title: 'Track One',
        artist: 'Artist A',
        album: 'Album A',
        extension: '.flac',
      },
      {
        filename: '02-track.flac',
        rel_path: 'Album/02-track.flac',
        full_path: '/music/Staging/Album/02-track.flac',
        title: 'Track Two',
        artist: 'Artist A',
        album: 'Album A',
        extension: '.flac',
      },
    ];
    resetImportWorkflowStore();
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
    window.showConfirmDialog = vi.fn(async () => true);
    vi.spyOn(globalThis, 'fetch');

    server.use(
      http.get('/api/import/staging/files', () => {
        return HttpResponse.json({
          success: true,
          staging_path: '/music/Staging',
          files: stagingFilesPayload,
        });
      }),
      http.get('/api/import/staging/groups', () => {
        return HttpResponse.json({
          success: true,
          groups: [
            {
              album: 'Album A',
              artist: 'Artist A',
              file_count: 2,
              file_paths: ['/music/Staging/Album/01-track.flac'],
            },
          ],
        });
      }),
      http.get('/api/import/staging/suggestions', () => {
        return HttpResponse.json({
          success: true,
          ready: true,
          primary_source: 'spotify',
          suggestions: [
            {
              id: 'album-1',
              name: 'Album A',
              artist: 'Artist A',
              source: 'deezer',
              total_tracks: 1,
              release_date: '2026-01-01',
            },
          ],
        });
      }),
      http.get('/api/import/search/albums', () => {
        return HttpResponse.json({
          success: true,
          primary_source: 'spotify',
          albums: [
            {
              id: 'album-1',
              name: 'Album A',
              artist: 'Artist A',
              source: 'deezer',
              total_tracks: 1,
              release_date: '2026-01-01',
            },
          ],
        });
      }),
      http.post('/api/import/album/match', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        albumMatchBodies.push(body);
        return HttpResponse.json({
          success: true,
          received: body,
          album: {
            id: 'album-1',
            name: 'Album A',
            artist: 'Artist A',
            source: 'deezer',
            total_tracks: 1,
            release_date: '2026-01-01',
          },
          matches: [
            {
              track: { name: 'Track One', track_number: 1 },
              staging_file: {
                filename: '01-track.flac',
                full_path: '/music/Staging/Album/01-track.flac',
              },
              confidence: 0.95,
            },
          ],
        });
      }),
      http.get('/api/auto-import/status', () => {
        return HttpResponse.json({
          success: true,
          running: true,
          current_status: 'idle',
          active_imports: [],
        });
      }),
      http.get('/api/auto-import/settings', () => {
        return HttpResponse.json({
          success: true,
          scan_interval: 60,
          confidence_threshold: 0.9,
        });
      }),
      http.get('/api/auto-import/results', () => {
        return HttpResponse.json({
          success: true,
          results: [
            {
              id: 4,
              status: 'pending_review',
              folder_hash: 'hash-1',
              folder_name: 'Album A',
              album_name: 'Album A',
              artist_name: 'Artist A',
              confidence: 0.82,
              total_files: 2,
            },
          ],
        });
      }),
      http.get('/api/issues/counts', () => {
        return HttpResponse.json({
          success: true,
          counts: {
            open: 0,
            in_progress: 0,
            resolved: 0,
            dismissed: 0,
            total: 0,
          },
        });
      }),
    );
  });

  it('renders the import page through the app router', async () => {
    const { history } = renderImportRoute();

    await waitFor(() => expect(screen.getByTestId('import-page')).toBeInTheDocument());
    expect(await screen.findByText('Import Music')).toBeInTheDocument();
    expect(screen.getByText('Import: /music/Staging')).toBeInTheDocument();
    expect(
      await screen.findByText('Showing Deezer results - not from your primary source (Spotify).'),
    ).toBeInTheDocument();
    expect(screen.getByText('via Deezer')).toBeInTheDocument();
    await waitFor(() => expect(history.location.pathname).toBe('/import/album'));
    await waitFor(() =>
      expect(getFetchUrls().some((url) => url.includes('/api/import/staging/groups'))).toBe(true),
    );
    expect(getFetchUrls().some((url) => url.includes('/api/import/staging/suggestions'))).toBe(
      true,
    );
    expect(window.SoulSyncWebShellBridge?.showReactHost).toHaveBeenCalledWith('import');
    expect(window.SoulSyncWebShellBridge?.setActivePageChrome).toHaveBeenCalledWith('import');
  });

  it('stores the active tab in nested route paths', async () => {
    const { history } = renderImportRoute();

    fireEvent.click(await screen.findByRole('link', { name: 'Singles' }));

    await waitFor(() => expect(history.location.pathname).toBe('/import/singles'));
    expect(screen.getByRole('button', { name: /Process Selected\s*0/ })).toBeInTheDocument();
  });

  it('keeps client workflow drafts across page remounts', async () => {
    const view = renderImportRoute();

    const searchInput = await screen.findByPlaceholderText('Search for an album...');
    fireEvent.change(searchInput, { target: { value: 'half matched album' } });
    view.unmount();

    renderImportRoute();

    expect(await screen.findByDisplayValue('half matched album')).toBeInTheDocument();
  });

  it('keeps singles selection tied to file identity across refreshes', async () => {
    renderImportRoute(['/import/singles']);

    const secondTrack = await screen.findByLabelText('Select 02-track.flac');
    fireEvent.click(secondTrack);

    stagingFilesPayload = [
      {
        filename: '00-intro.flac',
        rel_path: 'Album/00-intro.flac',
        full_path: '/music/Staging/Album/00-intro.flac',
        title: 'Intro',
        artist: 'Artist A',
        album: 'Album A',
        extension: '.flac',
      },
      ...stagingFilesPayload,
    ];

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: 'Select 02-track.flac' })).toBeChecked(),
    );
    expect(screen.getByRole('checkbox', { name: 'Select 01-track.flac' })).not.toBeChecked();
    expect(screen.getByRole('button', { name: /Process Selected\s*1/ })).toBeInTheDocument();
  });

  it('preserves album source details when matching an album', async () => {
    renderImportRoute();

    const albumButtons = await screen.findAllByRole('button', { name: /Album A/ });
    fireEvent.click(albumButtons[albumButtons.length - 1]);

    await waitFor(() => expect(screen.getByText('Track Matching')).toBeInTheDocument());

    expect(albumMatchBodies.at(-1)).toMatchObject({
      source: 'deezer',
      album_name: 'Album A',
      album_artist: 'Artist A',
    });
  });

  it('surfaces the served source when album search falls back', async () => {
    server.use(
      http.get('/api/import/search/albums', () => {
        return HttpResponse.json({
          success: true,
          primary_source: 'spotify',
          albums: [
            {
              id: 'album-2',
              name: 'Album A',
              artist: 'Artist A',
              source: 'musicbrainz',
              total_tracks: 1,
              release_date: '2026-01-01',
            },
          ],
        });
      }),
    );

    renderImportRoute();

    const searchInput = await screen.findByPlaceholderText('Search for an album...');
    fireEvent.change(searchInput, { target: { value: 'Album A' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));

    expect(
      await screen.findByText(
        'Showing MusicBrainz results - not from your primary source (Spotify).',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('via MusicBrainz')).toBeInTheDocument();
  });

  it('renders auto-import results from route search state', async () => {
    renderImportRoute(['/import/auto?autoFilter=pending']);

    expect(await screen.findByRole('button', { name: /^Needs Review\s*1$/ })).toBeInTheDocument();
    expect(screen.getAllByText('Album A').length).toBeGreaterThan(0);
    expect(screen.getByText('Watching')).toHaveAttribute('data-tone', 'success');
    expect(getFetchUrls().some((url) => url.includes('/api/import/staging/groups'))).toBe(false);
    expect(getFetchUrls().some((url) => url.includes('/api/import/staging/suggestions'))).toBe(
      false,
    );
  });
});
