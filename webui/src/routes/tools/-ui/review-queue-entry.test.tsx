import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { ReviewQueueEntry } from './review-queue-entry';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('shows album counts from the queue and links each kind of work to its view', async () => {
  const fetchMock = vi.fn((url: string) => {
    const risk = new URL(url, 'http://localhost').searchParams.get('risk');
    const total = risk === 'manual' ? 3 : risk === 'ready' ? 2 : risk === 'destructive' ? 1 : 5;
    return Promise.resolve({ ok: true, json: async () => ({ total, items: [] }) });
  });
  vi.stubGlobal('fetch', fetchMock);

  render(<ReviewQueueEntry onScanStarted={vi.fn()} />);

  expect(await screen.findByText('3 albums need decisions')).not.toBeNull();
  expect(screen.getByRole('link', { name: 'Needs input (3)' }).getAttribute('href')).toBe(
    '/review?mode=review',
  );
  expect(screen.getByRole('link', { name: 'Ready to apply (2)' }).getAttribute('href')).toBe(
    '/review?mode=ready',
  );
  expect(screen.getByRole('link', { name: 'File actions (1)' }).getAttribute('href')).toBe(
    '/review?mode=files',
  );
  expect(screen.getByText('No completed album scan yet')).not.toBeNull();
});

it('starts an album scan without launching the generic maintenance playbook', async () => {
  const fetchMock = vi.fn((url: string) =>
    Promise.resolve({
      ok: true,
      json: async () =>
        url.includes('review-queue') ? { total: 0, items: [] } : { success: true },
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
