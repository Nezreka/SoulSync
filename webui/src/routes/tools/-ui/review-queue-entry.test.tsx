import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { ReviewQueueEntry } from './review-queue-entry';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('shows album counts from the queue and links each kind of work to its view', async () => {
  const fetchMock = vi.fn((url: string) => {
    return Promise.resolve({
      ok: true,
      json: async () =>
        url.includes('/review-queue/summary')
          ? { all: 5, review: 3, ready: 2, files: 1 }
          : { runs: [] },
    });
  });
  vi.stubGlobal('fetch', fetchMock);

  render(<ReviewQueueEntry onScanStarted={vi.fn()} />);

  expect(await screen.findByText(/3 albums need input · 2 ready to apply/)).not.toBeNull();
  expect(screen.getByRole('link', { name: 'Open Album Review' })).toHaveAttribute(
    'href',
    '/review?mode=review',
  );
  expect(screen.getByText('Preview')).toBeVisible();
  expect(screen.getByText('No completed album scan yet')).not.toBeNull();
});

it('starts an album scan without launching the generic maintenance playbook', async () => {
  const fetchMock = vi.fn((url: string) =>
    Promise.resolve({
      ok: true,
      json: async () =>
        url.includes('review-queue')
          ? { all: 0, review: 0, ready: 0, files: 0 }
          : { success: true },
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  const onScanStarted = vi.fn();

  render(<ReviewQueueEntry onScanStarted={onScanStarted} />);
  fireEvent.click(screen.getByRole('button', { name: 'Scan albums' }));
  await waitFor(() => expect(onScanStarted).toHaveBeenCalledOnce());
  expect(
    fetchMock.mock.calls.some(([url]) => url === '/api/repair/jobs/library_reconciliation/run'),
  ).toBe(true);
});
