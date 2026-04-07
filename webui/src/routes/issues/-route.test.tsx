import { createMemoryHistory } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

import type { ShellBridge, ShellPageId } from '@/platform/shell/bridge';

import { createAppQueryClient } from '@/app/query-client';
import { AppRouterProvider, createAppRouter } from '@/app/router';

function createResponse(body: unknown, ok = true, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function createShellBridge(overrides: Partial<ShellBridge> = {}): ShellBridge {
  return {
    getCurrentPageId: vi.fn<() => ShellPageId>(() => 'issues'),
    getCurrentProfileContext: vi.fn(() => ({ profileId: 2, isAdmin: true })),
    isPageAllowed: vi.fn(() => true),
    getProfileHomePage: vi.fn<() => ShellPageId>(() => 'discover'),
    resolveLegacyPath: vi.fn<(pathname: string) => ShellPageId | null>(() => 'downloads'),
    setActivePageChrome: vi.fn(),
    activateLegacyPath: vi.fn(),
    showReactHost: vi.fn(),
    ...overrides,
  };
}

function renderIssuesRoute() {
  const queryClient = createAppQueryClient();
  const history = createMemoryHistory({ initialEntries: ['/issues'] });
  const router = createAppRouter({ history, queryClient });

  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

const workflowActions = {
  openDownloadMissingAlbum: vi.fn(),
  openAddToWishlistAlbum: vi.fn(),
};

describe('issues route', () => {
  beforeEach(() => {
    workflowActions.openDownloadMissingAlbum.mockReset();
    workflowActions.openAddToWishlistAlbum.mockReset();
    window.SoulSyncWebShellBridge = createShellBridge();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes('/api/issues/counts')) {
          return createResponse({
            success: true,
            counts: { open: 2, in_progress: 1, resolved: 0, dismissed: 0, total: 3 },
          });
        }
        if (url.includes('/api/issues?')) {
          return createResponse({
            success: true,
            total: 1,
            issues: [
              {
                id: 7,
                profile_id: 2,
                entity_type: 'album',
                entity_id: '15',
                category: 'wrong_metadata',
                title: 'Bad tags',
                description: 'Album title is wrong',
                status: 'open',
                priority: 'normal',
                snapshot_data: {
                  title: 'Album Name',
                  artist_name: 'Artist',
                  thumb_url: 'https://example.com/thumb.jpg',
                  spotify_album_id: 'abc123',
                },
                created_at: '2026-04-03 10:30:00',
                reporter_name: 'Ada',
              },
            ],
          });
        }
        if (url.includes('/api/issues/7')) {
          return createResponse({
            success: true,
            issue: {
              id: 7,
              profile_id: 2,
              entity_type: 'album',
              entity_id: '15',
              category: 'wrong_metadata',
              title: 'Bad tags',
              description: 'Album title is wrong',
              status: 'open',
              priority: 'normal',
              snapshot_data: {
                title: 'Album Name',
                artist_name: 'Artist',
                thumb_url: 'https://example.com/thumb.jpg',
                spotify_album_id: 'abc123',
              },
              created_at: '2026-04-03 10:30:00',
              reporter_name: 'Ada',
            },
          });
        }
        if (url.includes('/api/spotify/album/abc123')) {
          return createResponse({
            id: 'abc123',
            name: 'Album Name',
            album_type: 'album',
            images: [{ url: 'https://example.com/thumb.jpg' }],
            total_tracks: 1,
            artists: [{ name: 'Artist' }],
            tracks: [{ id: 'track-1', name: 'Track 1' }],
          });
        }
        return createResponse({ success: true });
      }) as unknown as typeof fetch,
    );
    vi.stubGlobal('SoulSyncWorkflowActions', workflowActions);
    vi.stubGlobal('showToast', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.SoulSyncWebShellBridge = undefined;
  });

  it('renders stats and list items through the app router', async () => {
    renderIssuesRoute();
    await waitFor(() => expect(screen.getByTestId('issue-counts')).toHaveTextContent('2'));
    expect(await screen.findByTestId('issue-card-7')).toHaveTextContent('Bad tags');
  });

  it('stores filters in route search state', async () => {
    renderIssuesRoute();
    const status = await screen.findByRole('combobox', { name: /status/i });
    fireEvent.change(status, { target: { value: 'resolved' } });
    await waitFor(() => expect(status).toHaveValue('resolved'));
  });

  it('opens and closes the detail modal', async () => {
    renderIssuesRoute();
    fireEvent.click(await screen.findByTestId('issue-card-7'));
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('Issue #7'));
    fireEvent.click(screen.getByRole('button', { name: /close issue detail/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('invokes the shared workflow adapter for admin downloads', async () => {
    renderIssuesRoute();
    fireEvent.click(await screen.findByTestId('issue-card-7'));
    fireEvent.click(await screen.findByRole('button', { name: /download album/i }));
    await waitFor(() => expect(workflowActions.openDownloadMissingAlbum).toHaveBeenCalled());
    expect(workflowActions.openDownloadMissingAlbum).toHaveBeenCalledWith(
      expect.objectContaining({
        virtualPlaylistId: 'issue_download_abc123',
        playlistName: '[Artist] Album Name',
      }),
    );
  });

  it('opens the global React issue composer through the domain bridge', async () => {
    const fetchMock = vi.mocked(fetch);
    renderIssuesRoute();
    await waitFor(() => expect(window.SoulSyncIssueDomain).toBeDefined());

    act(() => {
      window.SoulSyncIssueDomain?.openReportIssue({
        entityType: 'album',
        entityId: 15,
        entityName: 'Album Name',
        artistName: 'Artist',
      });
    });

    fireEvent.click(await screen.findByRole('button', { name: /wrong cover art/i }));
    expect(screen.getByLabelText(/title/i)).toHaveValue('Wrong Cover Art: Album Name');
    fireEvent.click(screen.getByRole('button', { name: /submit issue/i }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([request]) => request instanceof Request && request.method === 'POST'),
      ).toBe(true);
    });
  });
});
