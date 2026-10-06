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
    );
    renderBanner();
    expect(await screen.findByText(/library upgrade failed/)).toBeInTheDocument();
    expect(screen.getByText('disk full')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry now' }));
    await waitFor(() => expect(started).toBe(true));
  });

  it('shows progress while running and nothing once done', async () => {
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(status({ status: 'running', current: 1, total: 4 })),
      ),
    );
    const { unmount } = renderBanner();
    expect(await screen.findByText(/Upgrading your library — 25%/)).toBeInTheDocument();
    unmount();

    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(status({ status: 'done' })),
      ),
    );
    const { container } = renderBanner();
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
