import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { MonitoringModal, MonitorToggle } from './library-v2-page';

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

  it('keeps a failed future-release choice visible and retries it explicitly', async () => {
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
    expect(select).toHaveValue('new');
    expect(select).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('Future-release monitoring saved.')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(attempts).toBe(2);
    expect(submitted).toEqual([{ monitor_new_items: 'new' }, { monitor_new_items: 'new' }]);
    expect(select).toBeEnabled();
  });
});
