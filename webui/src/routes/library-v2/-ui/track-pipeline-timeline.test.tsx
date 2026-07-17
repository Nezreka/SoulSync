import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { TrackPipelineTimeline } from './library-v2-page';

function renderWithClient(node: React.ReactElement) {
  const queryClient = createTestQueryClient();
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

describe('library v2 track pipeline timeline (§52.9)', () => {
  it('renders nothing once loaded when the track has no pipeline history', async () => {
    server.use(
      http.get('/api/library/v2/tracks/9/history', () =>
        HttpResponse.json({ success: true, history: [] }),
      ),
    );

    renderWithClient(<TrackPipelineTimeline trackId={9} />);

    await waitFor(() =>
      expect(screen.queryByText(/Loading pipeline history/)).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(/Pipeline —/)).not.toBeInTheDocument();
  });

  it('shows a failed attempt that never reached a file row, oldest first', async () => {
    server.use(
      http.get('/api/library/v2/tracks/9/history', () =>
        HttpResponse.json({
          success: true,
          history: [
            {
              date: '2026-07-17 10:05:00',
              event_type: 'import_file_quarantined',
              category: 'quarantined',
              title: 'Quarantined',
              detail: 'AcoustID mismatch',
              source: 'acquisition',
            },
            {
              date: '2026-07-17 10:00:00',
              event_type: 'grab_submitted',
              category: 'grabbed',
              title: 'Grabbed',
              detail: null,
              source: 'acquisition',
            },
          ],
        }),
      ),
    );

    renderWithClient(<TrackPipelineTimeline trackId={9} />);

    await waitFor(() => expect(screen.getByText('Pipeline — 2 events')).toBeInTheDocument());
    const items = screen.getAllByText(/Grabbed|Quarantined/);
    expect(items.map((el) => el.textContent)).toEqual(['Grabbed', 'Quarantined']);
    expect(screen.getByText('AcoustID mismatch')).toBeInTheDocument();
  });
});
