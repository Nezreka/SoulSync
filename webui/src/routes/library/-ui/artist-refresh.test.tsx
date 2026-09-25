import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { ArtistRefreshButton, LibraryV2CanWriteContext } from './library-v2-page';

describe('library v2 artist refresh mutation', () => {
  afterEach(() => {
    window.showToast = undefined;
  });

  it('shows a rejected refresh and turns the same control into a retry', async () => {
    let attempts = 0;
    server.use(
      http.post('/api/library/v2/artists/7/refresh', () => {
        attempts += 1;
        return HttpResponse.json({
          success: true,
          job_id: `refresh-${attempts}`,
        });
      }),
      http.get('/api/library/v2/jobs/status', ({ request }) => {
        const jobId = new URL(request.url).searchParams.get('job_id');
        return HttpResponse.json({
          job_id: jobId,
          running: false,
          error: jobId === 'refresh-1' ? 'Music root is temporarily unavailable' : null,
          result: jobId === 'refresh-1' ? null : { refreshed_albums: 3, scanned: 8 },
        });
      }),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <LibraryV2CanWriteContext.Provider value>
          <ArtistRefreshButton artistId={7} />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );

    const refresh = screen.getByRole('button', { name: 'Refresh & Scan' });
    expect(refresh).toHaveAttribute(
      'title',
      'Re-read files on disk: existence, audio quality and embedded tags. Provider metadata is unchanged.',
    );
    fireEvent.click(refresh);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Music root is temporarily unavailable',
    );
    const retry = screen.getByRole('button', { name: 'Retry Refresh & Scan' });
    expect(retry).toBeEnabled();

    fireEvent.click(retry);

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(attempts).toBe(2);
    expect(screen.getByRole('button', { name: 'Refresh & Scan' })).toBeEnabled();
  });

  it('reports what the scan actually did instead of settling silently', async () => {
    // The bug this covers is the user-visible half of the missing-file fix:
    // pressing the button used to look identical whether it retired a dead
    // row, relinked a renamed file, or did nothing at all.
    server.use(
      http.post('/api/library/v2/artists/9/refresh', () =>
        HttpResponse.json({ success: true, job_id: 'refresh-9' }),
      ),
      http.get('/api/library/v2/jobs/status', () =>
        HttpResponse.json({
          job_id: 'refresh-9',
          running: false,
          error: null,
          result: {
            refreshed_albums: 4,
            scanned: 9,
            updated: 9,
            missing: 2,
            path_drift: 2,
            path_repointed: 1,
            missing_confirmed: 1,
            missing_suspected: 0,
            recovered: 0,
          },
        }),
      ),
    );
    const toast = vi.fn();
    window.showToast = toast;

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <LibraryV2CanWriteContext.Provider value>
          <ArtistRefreshButton artistId={9} />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Refresh & Scan' }));

    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(toast).toHaveBeenCalledWith(
      'Refresh & Scan: 9 files scanned, 1 renamed file relinked, 1 now missing, ' +
        '1 needing review in Stale Index Paths.',
      'success',
    );
  });
});
