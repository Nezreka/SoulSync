import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { MigrationBanner } from './migration-banner';

function status(bootstrap: Record<string, unknown>) {
  return {
    success: true,
    running: false,
    artwork_cache: { running: false },
    bootstrap: { attempts: 1, stage: null, current: 0, total: 0, last_error: null, ...bootstrap },
  };
}

function renderBanner() {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MigrationBanner />
    </QueryClientProvider>,
  );
}

describe('MigrationBanner', () => {
  it('shows a failed upgrade on every page and retries it', async () => {
    let started = false;
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(status({ status: 'failed', last_error: 'disk full' })),
      ),
      http.post('/api/library/v2/import', () => {
        started = true;
        return HttpResponse.json({ success: true });
      }),
      http.get('/api/library/v2/enabled', () =>
        HttpResponse.json({ success: true, enabled: true, can_write: true }),
      ),
    );
    renderBanner();
    expect(await screen.findByText(/library upgrade failed/)).toBeInTheDocument();
    expect(screen.getByText('disk full')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry now' }));
    await waitFor(() => expect(started).toBe(true));
  });

  it('tells a household member to ask an admin instead of a retry that can only fail', async () => {
    // third #1062 review G15-7: the import route refuses a non-admin
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(status({ status: 'failed', last_error: 'disk full' })),
      ),
      http.get('/api/library/v2/enabled', () =>
        HttpResponse.json({ success: true, enabled: true, can_write: false }),
      ),
    );
    renderBanner();
    expect(await screen.findByText('Ask an admin to retry it.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry now' })).toBeNull();
  });

  it('shows progress while running and nothing once done', async () => {
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(
          status({ status: 'running', stage: 'tracks', current: 1200, total: 4800 }),
        ),
      ),
    );
    const { unmount } = renderBanner();
    expect(
      await screen.findByText(/Upgrading your library — tracks 1,200 of 4,800 \(25%\)/),
    ).toBeInTheDocument();
    unmount();

    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(status({ status: 'running', stage: 'finalize:links' })),
      ),
    );
    const second = renderBanner();
    expect(await screen.findByText(/Upgrading your library — finishing\./)).toBeInTheDocument();
    second.unmount();

    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(status({ status: 'done' })),
      ),
    );
    const { container } = renderBanner();
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('says what the finishing work is doing after the upgrade, and goes when it ends', async () => {
    let active = true;
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json({
          ...status({ status: 'done' }),
          preparation: { active, stage: 'artists', current: 1200, total: 2400 },
        }),
      ),
    );
    const { unmount } = renderBanner();
    expect(
      await screen.findByText(
        /Finishing your library upgrade: matching artist credits 1,200 of 2,400 \(50%\)/,
      ),
    ).toBeInTheDocument();
    unmount();
    active = false;
    renderBanner();
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
  });

  it('calls the credit pass a restart repeats what it is, not an upgrade', async () => {
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json({
          ...status({ status: 'done' }),
          preparation: { active: true, full: false, stage: 'artists', current: 30, total: 120 },
        }),
      ),
    );
    renderBanner();
    expect(
      await screen.findByText(
        'Matching new artist credits in the background: 30 of 120 (25%). Everything works meanwhile.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Finishing your library upgrade/)).not.toBeInTheDocument();
  });

  it('shows nothing for the restart pass until it has something to count', async () => {
    let calls = 0;
    server.use(
      http.get('/api/library/v2/import/status', () => {
        calls += 1;
        return HttpResponse.json({
          ...status({ status: 'done' }),
          preparation: { active: true, full: false, stage: 'artists', current: 0, total: 0 },
        });
      }),
    );
    renderBanner();
    await waitFor(() => expect(calls).toBeGreaterThan(0));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('still says finishing your upgrade for the full preparation', async () => {
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json({
          ...status({ status: 'done' }),
          preparation: { active: true, full: true, stage: 'tracklists', current: 5, total: 10 },
        }),
      ),
    );
    renderBanner();
    expect(
      await screen.findByText(/Finishing your library upgrade: album tracklists 5 of 10 \(50%\)/),
    ).toBeInTheDocument();
  });
});
