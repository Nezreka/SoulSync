import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { MonitoringModal, MonitorToggle, SectionBulkMonitorButton } from './library-v2-page';

function renderWithQueryClient(node: React.ReactNode) {
  const queryClient = createTestQueryClient();
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

describe('library v2 monitoring mutations', () => {
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

    const toggle = screen.getByRole('button', { name: 'Start monitoring' });
    fireEvent.click(toggle);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Update failed — click bookmark to retry',
    );
    expect(toggle).toBeEnabled();
    expect(screen.getByRole('alert')).toHaveAttribute('title', 'Wishlist mirror is unavailable');

    fireEvent.click(toggle);

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(attempts).toBe(2);
    expect(toggle).toBeEnabled();
  });

  it('rolls back a failed future-release choice and retries the rejected value', async () => {
    let attempts = 0;
    const submitted: unknown[] = [];
    server.use(
      http.post('/api/library/v2/artists/7/edit', async ({ request }) => {
        attempts += 1;
        submitted.push(await request.json());
        return HttpResponse.json(
          attempts === 1
            ? { success: false, error: 'Monitor rule could not be saved' }
            : { success: true },
        );
      }),
    );

    renderWithQueryClient(<MonitoringModal artistId={7} monitorNewItems="all" onClose={vi.fn()} />);

    const select = screen.getByLabelText('Future releases');
    fireEvent.change(select, { target: { value: 'new' } });

    expect(await screen.findByRole('alert')).toHaveTextContent('Monitor rule could not be saved');
    expect(select).toHaveValue('all');
    expect(select).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('Future-release monitoring saved.')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(attempts).toBe(2);
    expect(submitted).toEqual([{ monitor_new_items: 'new' }, { monitor_new_items: 'new' }]);
    expect(select).toBeEnabled();
  });

  it('surfaces a failed bulk-monitor job and retries the same scope', async () => {
    let starts = 0;
    const submitted: unknown[] = [];
    const onClose = vi.fn();
    server.use(
      http.post('/api/library/v2/artists/7/releases/monitor', async ({ request }) => {
        starts += 1;
        submitted.push(await request.json());
        return HttpResponse.json({ success: true, job_id: `job-${starts}` });
      }),
      http.get('/api/library/v2/jobs/status', ({ request }) => {
        const jobId = new URL(request.url).searchParams.get('job_id');
        return HttpResponse.json({
          running: false,
          error: jobId === 'job-1' ? 'Wishlist mirror failed' : null,
        });
      }),
    );

    renderWithQueryClient(<MonitoringModal artistId={7} monitorNewItems="all" onClose={onClose} />);

    fireEvent.click(screen.getByRole('button', { name: /Monitor missing only/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Wishlist mirror failed');
    expect(screen.getByRole('button', { name: /Monitor missing only/ })).toBeEnabled();
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(starts).toBe(2);
    expect(submitted).toEqual([
      { scope: 'missing', monitored: true },
      { scope: 'missing', monitored: true },
    ]);
  });

  it('surfaces a failed release-section monitor job and retries its exact target', async () => {
    let starts = 0;
    const submitted: unknown[] = [];
    server.use(
      http.post('/api/library/v2/artists/7/releases/monitor', async ({ request }) => {
        starts += 1;
        submitted.push(await request.json());
        return HttpResponse.json({ success: true, job_id: `section-job-${starts}` });
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

    fireEvent.click(screen.getByRole('button', { name: /Monitor all/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Album monitoring failed');
    expect(screen.getByRole('button', { name: /Monitor all/ })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(starts).toBe(2);
    expect(submitted).toEqual([
      { scope: 'albums', monitored: true, album_ids: [11, 12] },
      { scope: 'albums', monitored: true, album_ids: [11, 12] },
    ]);
  });
});
