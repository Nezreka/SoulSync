import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { HistoryModal } from './library-v2-page';

describe('Library history', () => {
  it('loads older events without losing the active filter or inventing an earlier verdict', async () => {
    const requested: number[] = [];
    server.use(
      http.get('/api/library/v2/artists/7/history', ({ request }) => {
        const limit = Number(new URL(request.url).searchParams.get('limit'));
        requested.push(limit);
        return HttpResponse.json({
          success: true,
          history: [
            ...Array.from({ length: 50 }, (_, i) => ({
              category: 'maintenance',
              event_type: 'acoustid_check',
              title: 'Acoustic ID status updated',
              date: '2026-08-16T20:02:00Z',
              source: 'scanner',
              status: 'Verified',
              status_basis: 'current_file',
              track_id: i,
              track_title: `Track ${i}`,
              album_title: 'Example release',
            })),
            ...(limit > 50
              ? [
                  {
                    category: 'metadata',
                    event_type: 'manual_override',
                    title: 'Metadata edited',
                    date: '2026-08-15T12:00:00Z',
                    detail: 'Title changed by hand',
                    track_title: 'Earlier track',
                  },
                ]
              : []),
          ],
        });
      }),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <HistoryModal scope="artist" entityId={7} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Current file status')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search history' }), {
      target: { value: 'Earlier track' },
    });
    expect(screen.getByText('No events match these filters.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load older events' }));
    expect(await screen.findByText('Metadata edited')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Search history' })).toHaveValue('Earlier track');
    expect(requested).toEqual([50, 100]);
    expect(screen.queryByRole('button', { name: 'Load older events' })).not.toBeInTheDocument();
  });
});
