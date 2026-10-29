import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it } from 'vitest';

import { ReviewPage } from './review-page';
import { fetchMock, reply, auxiliary } from './support/page';
import { unexpectedRequest } from './support/requests';

it('shows why the last scan stopped and can queue a rerun', async () => {
  fetchMock.mockImplementation((url: string, options?: RequestInit) => {
    if (url === '/api/repair/history?limit=50')
      return Promise.resolve(
        reply({
          runs: [
            {
              job_id: 'library_reconciliation',
              status: 'stopped',
              items_scanned: 2539,
              error_text: 'AcoustID API error: status: error',
            },
          ],
        }),
      );
    if (url === '/api/repair/jobs/library_reconciliation/run') {
      expect(options?.method).toBe('POST');
      return Promise.resolve(reply({ success: true }));
    }
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(reply({ items: [], total: 0, page: 0, limit: 30 }));
    return unexpectedRequest(url);
  });
  render(<ReviewPage />);
  await screen.findByText(/Previous library scan: stopped/);
  expect(screen.getByText(/2539 files checked/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Reanalyze library' }));
  await waitFor(() => expect(screen.getByText(/Library reconciliation queued/)).toBeTruthy());
});
