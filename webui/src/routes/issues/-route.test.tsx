import { createMemoryHistory } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

function createResponse(body: unknown, ok = true, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderIssuesRoute(initialEntries = ['/issues']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });

  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
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
            counts: {
              open: 2,
              in_progress: 1,
              resolved: 0,
              dismissed: 0,
              total: 3,
            },
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
                  track_number: 1,
                  duration: 245,
                  format: 'FLAC',
                  bitrate: 1411,
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
              fix_action: { id: 'edit_metadata', label: 'Edit details' },
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
                track_number: 1,
                duration: 245,
                format: 'FLAC',
                bitrate: 1411,
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
    const issueCard = await screen.findByRole('link', { name: /Bad tags/i });
    expect(issueCard).toHaveAttribute('href', expect.stringContaining('/issues?'));
    expect(issueCard).toHaveAttribute('href', expect.stringContaining('status=open'));
    expect(issueCard).toHaveAttribute('href', expect.stringContaining('category=all'));
    expect(issueCard).toHaveAttribute('href', expect.stringContaining('issueId=7'));
  });

  it('loads the detail modal from the route search state', async () => {
    renderIssuesRoute(['/issues?issueId=7']);
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('Issue #7'));
    expect(await screen.findByTitle('Spotify Album')).toHaveAttribute(
      'href',
      'https://open.spotify.com/album/abc123',
    );
  });

  it('stores filters in route search state', async () => {
    renderIssuesRoute();
    const status = await screen.findByRole('combobox', { name: /status/i });
    fireEvent.change(status, { target: { value: 'resolved' } });
    await waitFor(() => expect(status).toHaveValue('resolved'));
  });

  it('opens and closes the detail modal', async () => {
    const { history } = renderIssuesRoute();
    fireEvent.click(await screen.findByRole('link', { name: /Bad tags/i }));
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('Issue #7'));
    await waitFor(() => expect(history.location.search).toContain('issueId=7'));
    fireEvent.click(screen.getByRole('button', { name: /close issue detail/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(history.location.search).toBe('?status=open&category=all'));
  });

  it('closes the detail modal with Escape', async () => {
    const { history } = renderIssuesRoute();
    fireEvent.click(await screen.findByRole('link', { name: /Bad tags/i }));
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('Issue #7'));
    await waitFor(() => expect(history.location.search).toContain('issueId=7'));

    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(history.location.search).toBe('?status=open&category=all'));
  });

  it('focuses the detail modal close button on open', async () => {
    renderIssuesRoute();
    fireEvent.click(await screen.findByRole('link', { name: /Bad tags/i }));

    const closeButton = await screen.findByRole('button', {
      name: /close issue detail/i,
    });

    await waitFor(() => expect(closeButton).toHaveFocus());
  });

  it('invokes the shared workflow adapter for admin downloads', async () => {
    renderIssuesRoute();
    fireEvent.click(await screen.findByRole('link', { name: /Bad tags/i }));
    fireEvent.click(await screen.findByRole('button', { name: /more actions/i }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /download album/i }));
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

    // everything below happens inside the composer, and its buttons are found
    // by their exact text. role queries compute every button's accessible name
    // and took this test past its time budget under a full run
    const composer = await screen.findByRole('dialog');
    await waitFor(() => expect(composer).toHaveTextContent(/wrong cover art/i));
    const inComposer = within(composer);
    const button = (text: RegExp) => inComposer.getByText(text).closest('button')!;

    fireEvent.click(button(/^wrong cover art$/i));
    const titleInput = inComposer.getByLabelText(/title/i);
    const descriptionInput = inComposer.getByLabelText(/details/i);
    const submitButton = button(/^submit issue$/i);
    const form = submitButton.closest('form');

    expect(titleInput).toHaveValue('Wrong Cover Art: Album Name');
    fireEvent.change(titleInput, { target: { value: '' } });
    expect(submitButton).toBeDisabled();
    fireEvent.submit(form!);
    await waitFor(() => {
      expect(inComposer.getByRole('alert')).toHaveTextContent(
        'Please provide a title for the issue',
      );
    });

    fireEvent.change(titleInput, { target: { value: 'Custom report title' } });
    fireEvent.blur(titleInput);
    fireEvent.change(descriptionInput, {
      target: { value: 'Detailed reproduction notes' },
    });
    fireEvent.click(button(/^high$/i));
    fireEvent.click(button(/^wrong metadata$/i));
    expect(titleInput).toHaveValue('Custom report title');
    expect(descriptionInput).toHaveValue('Detailed reproduction notes');
    expect(button(/^high$/i)).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(button(/^submit issue$/i));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([request]) => request instanceof Request && request.method === 'POST',
        ),
      ).toBe(true);
    });
  });

  it('replies without changing the status', async () => {
    const fetchMock = vi.mocked(fetch);
    renderIssuesRoute(['/issues?issueId=7']);
    const reply = await screen.findByRole('textbox', { name: /reply/i });
    fireEvent.change(reply, { target: { value: 'looking at it' } });
    fireEvent.click(screen.getByRole('button', { name: /send reply/i }));

    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(
        ([request]) =>
          request instanceof Request &&
          request.method === 'POST' &&
          request.url.includes('/api/issues/7/comments'),
      );
      expect(posted).toBeDefined();
    });
    expect(
      fetchMock.mock.calls.some(
        ([request]) => request instanceof Request && request.method === 'PUT',
      ),
    ).toBe(false);
  });

  it('leads with the category fix action and resolves from the footer', async () => {
    const fetchMock = vi.mocked(fetch);
    renderIssuesRoute(['/issues?issueId=7']);
    expect(await screen.findByRole('button', { name: 'Edit details' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^resolve$/i }));
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([request]) => request instanceof Request && request.method === 'PUT',
      );
      expect(put).toBeDefined();
    });
  });

  it('asks the app confirm dialog before deleting, never window.confirm', async () => {
    const confirmSpy = vi.fn(async () => false);
    vi.stubGlobal('showConfirmDialog', confirmSpy);
    const nativeConfirm = vi.spyOn(window, 'confirm');
    renderIssuesRoute(['/issues?issueId=7']);
    fireEvent.click(await screen.findByRole('button', { name: /more actions/i }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /delete issue/i }));
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
    expect(nativeConfirm).not.toHaveBeenCalled();
    nativeConfirm.mockRestore();
  });

  it('renders the thread with comments, events and followers', async () => {
    const baseFetch = vi.mocked(fetch).getMockImplementation()!;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        if (/\/api\/issues\/7(\?|$)/.test(url)) {
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
              status: 'in_progress',
              priority: 'high',
              snapshot_data: { title: 'Album Name', artist_name: 'Artist' },
              created_at: '2026-04-03 10:30:00',
              reporter_name: 'Ada',
              fix_action: { id: 'edit_metadata', label: 'Edit details' },
              comments: [
                { id: 1, author_name: 'Boss', kind: 'event', body: 'is working on this' },
                { id: 2, author_name: 'Boss', kind: 'comment', body: 'retagging now' },
              ],
              followers: [
                { follower_id: 3, follower_name: 'Kim' },
                { follower_id: 4, follower_name: 'Lee' },
              ],
            },
          });
        }
        return baseFetch(input, init);
      }) as unknown as typeof fetch,
    );
    renderIssuesRoute(['/issues?issueId=7']);
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog).toHaveTextContent('retagging now'));
    expect(dialog).toHaveTextContent('Boss is working on this');
    expect(dialog).toHaveTextContent('Kim and 1 other hit this too');
  });

  it('does not render track details for album issues', async () => {
    renderIssuesRoute(['/issues?issueId=7']);

    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('Issue #7'));
    expect(screen.queryByText('Track Details')).not.toBeInTheDocument();
  });
});

describe('issue composer for members', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge({
      getCurrentProfileContext: vi.fn(() => ({ profileId: 5, isAdmin: false })),
    });
    vi.stubGlobal('showToast', vi.fn());
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        const method = input instanceof Request ? input.method : 'GET';
        if (url.includes('/api/issues/counts')) {
          return createResponse({
            success: true,
            counts: { open: 0, in_progress: 0, resolved: 0, dismissed: 0, total: 0, updates: 0 },
          });
        }
        if (method === 'POST' && url.split('?')[0].endsWith('/api/issues')) {
          return createResponse({ success: true, id: 7, merged: true });
        }
        return createResponse({ success: true, issues: [], total: 0 });
      }) as unknown as typeof fetch,
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.SoulSyncWebShellBridge = undefined;
  });

  it('hides priority and says so when someone already reported it', async () => {
    renderIssuesRoute();
    await waitFor(() => expect(window.SoulSyncIssueDomain).toBeDefined());
    act(() => {
      window.SoulSyncIssueDomain?.openReportIssue({
        entityType: 'track',
        entityId: 3,
        entityName: 'Song',
      });
    });
    const composer = await screen.findByRole('dialog');
    const inComposer = within(composer);
    const button = (text: RegExp) => inComposer.getByText(text).closest('button')!;
    fireEvent.click(button(/^wrong track$/i));
    expect(inComposer.queryByText(/^high$/i)).toBeNull();
    fireEvent.click(button(/^submit issue$/i));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        "Someone already reported this. You'll hear when it's fixed.",
        'info',
      ),
    );
  });
});

describe('issues route survives a backend outage', () => {
  /**
   * The loader WARMS the cache; it must not gate the route. With Promise.all a
   * single failing request rejected the loader and TanStack replaced the whole
   * page with defaultErrorComponent — where the vanilla page stayed usable.
   *
   * Asserting the absence of that fallback also catches the other half: a page
   * that crashes on missing data throws into the same boundary.
   */
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
  });

  it('renders the page instead of "Something went wrong"', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: 'backend down' }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    );

    const { router } = renderIssuesRoute(['/issues']);

    // Positive proof the PAGE component mounted — useReactPageShell calls this
    // on mount, so it cannot pass while the error component is showing instead.
    await waitFor(() =>
      expect(window.SoulSyncWebShellBridge!.showReactHost).toHaveBeenCalledWith('issues'),
    );
    expect(screen.queryByText('Something went wrong')).toBeNull();
    expect(router.state.location.pathname).toBe('/issues');
  });
});
