import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import {
  LibraryV2CanWriteContext,
  MirrorStatusBanner,
  MonitorToggle,
  SectionBulkMonitorButton,
} from './library-v2-page';

function renderWithQueryClient(node: React.ReactNode, canWrite = true) {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <LibraryV2CanWriteContext.Provider value={canWrite}>{node}</LibraryV2CanWriteContext.Provider>
    </QueryClientProvider>,
  );
}

describe('library v2 monitoring mutations', () => {
  it('keeps mirror retries fail-closed for read-only profiles', async () => {
    let writes = 0;
    server.use(
      http.get('/api/library/v2/mirror-status', () =>
        HttpResponse.json({ success: true, pending: 0, failed: 1 }),
      ),
      http.post('/api/library/v2/mirror-retry', () => {
        writes += 1;
        return HttpResponse.json({ success: true });
      }),
    );

    renderWithQueryClient(<MirrorStatusBanner />, false);

    const retry = await screen.findByRole('button', { name: 'Retry' });
    expect(retry).toBeDisabled();
    fireEvent.click(retry);
    expect(writes).toBe(0);
  });

  it('shows pending sync outside the page and removes it when the queue drains', async () => {
    let pending = 83;
    server.use(
      http.get('/api/library/v2/mirror-status', () =>
        HttpResponse.json({ success: true, pending, failed: 0 }),
      ),
    );
    const queryClient = createTestQueryClient();
    const view = render(
      <QueryClientProvider client={queryClient}>
        <MirrorStatusBanner />
      </QueryClientProvider>,
    );
    const message = await screen.findByText('83 wishlist sync operations pending…');
    expect(view.container).not.toContainElement(message);
    expect(screen.getByRole('region', { name: 'Background activity' })).toContainElement(message);
    pending = 0;
    await queryClient.invalidateQueries();
    await waitFor(() =>
      expect(screen.queryByText('83 wishlist sync operations pending…')).not.toBeInTheDocument(),
    );
  });

  it('shows a monitor-toggle failure and lets the same action retry', async () => {
    let attempts = 0;
    server.use(
      http.post('/api/library/v2/tracks/42/monitor', () => {
        attempts += 1;
        return HttpResponse.json(
          attempts === 1
            ? { success: false, error: 'Wishlist mirror is unavailable' }
            : { success: true },
        );
      }),
    );

    renderWithQueryClient(<MonitorToggle entity="tracks" id={42} monitored={false} />);

    const toggle = screen.getByRole('button', { name: 'Add to Wishlist' });
    fireEvent.click(toggle);

    expect(await screen.findByRole('alert')).toHaveTextContent('Update failed. Click to try again');
    expect(toggle).toBeEnabled();
    expect(screen.getByRole('alert')).toHaveAttribute('title', 'Wishlist mirror is unavailable');

    fireEvent.click(toggle);

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(attempts).toBe(2);
    expect(toggle).toBeEnabled();
  });

  it('surfaces a failed release-section monitor job and retries its exact target', async () => {
    let starts = 0;
    const submitted: unknown[] = [];
    server.use(
      http.post('/api/library/v2/artists/7/releases/monitor', async ({ request }) => {
        starts += 1;
        submitted.push(await request.json());
        return HttpResponse.json({
          success: true,
          job_id: `section-job-${starts}`,
        });
      }),
      http.get('/api/library/v2/jobs/status', ({ request }) => {
        const jobId = new URL(request.url).searchParams.get('job_id');
        return HttpResponse.json({
          running: false,
          error: jobId === 'section-job-1' ? 'Album monitoring failed' : null,
        });
      }),
    );

    renderWithQueryClient(
      <SectionBulkMonitorButton
        artistId={7}
        scope="albums"
        title="Albums"
        allMonitored={false}
        albumIds={[11, 12]}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Add all to Wishlist/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Album monitoring failed');
    expect(screen.getByRole('button', { name: /Add all to Wishlist/ })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(starts).toBe(2);
    expect(submitted).toEqual([
      { scope: 'albums', monitored: true, album_ids: [11, 12] },
      { scope: 'albums', monitored: true, album_ids: [11, 12] },
    ]);
  });
});
