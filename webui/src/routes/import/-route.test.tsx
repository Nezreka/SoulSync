import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ShellBridge, ShellPageId } from '@/platform/shell/bridge';

import { createAppQueryClient } from '@/app/query-client';
import { AppRouterProvider, createAppRouter } from '@/app/router';

import type { ImportStagingFile } from './-import.types';
import { resetImportWorkflowStore } from './-import.store';

function createResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function createShellBridge(overrides: Partial<ShellBridge> = {}): ShellBridge {
  return {
    getCurrentProfileContext: vi.fn(() => ({ profileId: 2, isAdmin: true })),
    isPageAllowed: vi.fn(() => true),
    getProfileHomePage: vi.fn<() => ShellPageId>(() => 'discover'),
    resolveLegacyPath: vi.fn<(pathname: string) => ShellPageId | null>(() => 'search'),
    setActivePageChrome: vi.fn(),
    activateLegacyPath: vi.fn(),
    showReactHost: vi.fn(),
    navigateToArtistDetail: vi.fn(),
    playLibraryTrack: vi.fn(),
    startStream: vi.fn(),
    showLoadingOverlay: vi.fn(),
    hideLoadingOverlay: vi.fn(),
    ...overrides,
  };
}

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

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);

        if (url.includes('/api/import/staging/files')) {
          return createResponse({
            success: true,
            staging_path: '/music/Staging',
            files: stagingFilesPayload,
          });
        }

        if (url.includes('/api/import/staging/groups')) {
          return createResponse({
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
        }

        if (url.includes('/api/import/staging/suggestions')) {
          return createResponse({
            success: true,
            ready: true,
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
        }

        if (url.includes('/api/import/search/albums')) {
          return createResponse({
            success: true,
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
        }

        if (url.includes('/api/import/album/match')) {
          const body =
            input instanceof Request
              ? ((await input.clone().json()) as Record<string, unknown>)
              : (JSON.parse(typeof init?.body === 'string' ? init.body : '{}') as Record<
                  string,
                  unknown
                >);
          albumMatchBodies.push(body);
          return createResponse({
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
        }

        if (url.includes('/api/auto-import/status')) {
          return createResponse({
            success: true,
            running: true,
            current_status: 'idle',
            active_imports: [],
          });
        }

        if (url.includes('/api/auto-import/settings')) {
          return createResponse({
            success: true,
            scan_interval: 60,
            confidence_threshold: 0.9,
          });
        }

        if (url.includes('/api/auto-import/results')) {
          return createResponse({
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
        }

        return createResponse({ success: true });
      }) as unknown as typeof fetch,
    );
  });

  it('renders the import page through the app router', async () => {
    const { history } = renderImportRoute();

    await waitFor(() => expect(screen.getByTestId('import-page')).toBeInTheDocument());
    expect(await screen.findByText('Import Music')).toBeInTheDocument();
    expect(screen.getByText('Import: /music/Staging')).toBeInTheDocument();
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
    expect(screen.getByText('Process Selected (0)')).toBeInTheDocument();
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
    expect(screen.getByText('Process Selected (1)')).toBeInTheDocument();
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

  it('renders auto-import results from route search state', async () => {
    renderImportRoute(['/import/auto?autoFilter=pending']);

    expect(await screen.findByText('1 review')).toBeInTheDocument();
    expect(screen.getAllByText('Album A').length).toBeGreaterThan(0);
    expect(screen.getByText('Needs Review')).toBeInTheDocument();
    expect(getFetchUrls().some((url) => url.includes('/api/import/staging/groups'))).toBe(false);
    expect(getFetchUrls().some((url) => url.includes('/api/import/staging/suggestions'))).toBe(
      false,
    );
  });
});
