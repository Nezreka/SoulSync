import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { LibraryV2CanWriteContext, MaintenanceModal } from './library-v2-page';

function renderModal() {
  const onClose = vi.fn();
  return {
    onClose,
    ...render(
      <QueryClientProvider client={createTestQueryClient()}>
        <LibraryV2CanWriteContext.Provider value>
          <MaintenanceModal artistId={7} artistName="Massive Attack" onClose={onClose} />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    ),
  };
}

describe('Library v2 maintenance tools', () => {
  it('uses understandable names and makes artist versus library scope explicit', () => {
    renderModal();

    expect(screen.getByRole('dialog', { name: 'Library Health & Repair' })).toBeInTheDocument();
    expect(screen.getByText('Catalog & Wishlist')).toBeInTheDocument();
    expect(screen.getByText('Artist files & tags')).toBeInTheDocument();
    // no library-wide scan job is left, so that section is not drawn empty
    expect(screen.queryByText('Library-wide scans')).toBeNull();
    expect(screen.getAllByText('Entire library')).toHaveLength(1);
    expect(screen.getByText('This artist')).toBeInTheDocument();

    expect(screen.getByRole('button', { name: /Match Unmapped Artists/ })).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Synchronize Wanted & Wishlist/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Find Missing Metadata/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Check Album Tags/ })).toBeInTheDocument();
    // Deliberately absent: queueing a below-cutoff track is not a job you run,
    // it is what the wanted projection does continuously.
    expect(screen.queryByRole('button', { name: /Find Quality Upgrades/ })).toBeNull();
  });

  it('runs the cleanup checks against this artist only (#2004)', async () => {
    const bodies: { job: string; body: unknown }[] = [];
    server.use(
      http.post('/api/repair/jobs/:job/run', async ({ params, request }) => {
        bodies.push({ job: String(params.job), body: await request.json() });
        return HttpResponse.json({ success: true });
      }),
    );
    renderModal();

    for (const [name, job] of [
      [/Find Dead File Paths/, 'dead_file_cleaner'],
      [/Find Renamed Files/, 'path_drift_reconcile'],
      [/Find Duplicates/, 'native_duplicate_detector'],
    ] as const) {
      fireEvent.click(screen.getByRole('button', { name }));
      await vi.waitFor(() => expect(bodies.map((b) => b.job)).toContain(job));
    }
    for (const { body } of bodies) {
      expect(body).toEqual({ artist_id: 7, artist_name: 'Massive Attack' });
    }
  });

  it('shows a terminal job error instead of a successful zero result', async () => {
    server.use(
      http.post('/api/library/v2/maintenance/reconcile-unmapped-artists', () =>
        HttpResponse.json({ success: true, job_id: 'job-1' }),
      ),
      http.get('/api/library/v2/jobs/status', () =>
        HttpResponse.json({
          job_id: 'job-1',
          running: false,
          result: null,
          error: 'database locked',
        }),
      ),
    );

    renderModal();
    fireEvent.click(screen.getByRole('button', { name: /Match Unmapped Artists/ }));

    expect(await screen.findByText(/database locked/)).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
    expect(screen.queryByText('done')).not.toBeInTheDocument();
  });

  it('keeps keyboard handling in the shared dialog and closes on Escape', async () => {
    const { onClose } = renderModal();
    const close = screen.getByRole('button', { name: 'Close' });
    await vi.waitFor(() => expect(close).toHaveFocus());
    fireEvent.keyDown(document, { key: 'Escape' });
    await vi.waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});
