import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

const shellActions = {
  downloadAlbum: vi.fn(),
  addToWishlist: vi.fn(),
};

describe('issues route', () => {
  beforeEach(() => {
    shellActions.downloadAlbum.mockReset();
    shellActions.addToWishlist.mockReset();
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
        return createResponse({ success: true });
      }) as unknown as typeof fetch,
    );
    vi.stubGlobal('SoulSyncIssueActions', shellActions);
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

  it('invokes the legacy adapter for admin downloads', async () => {
    renderIssuesRoute();
    fireEvent.click(await screen.findByTestId('issue-card-7'));
    fireEvent.click(await screen.findByRole('button', { name: /download album/i }));
    expect(shellActions.downloadAlbum).toHaveBeenCalledWith('abc123', 'Artist', 'Album Name');
  });
});
