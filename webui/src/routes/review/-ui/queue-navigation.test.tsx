import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it } from 'vitest';

import { ReviewPage } from './review-page';
import { fetchMock, reply, finding, auxiliary } from './support/page';
import { unexpectedRequest } from './support/requests';

it('navigates across pages and retains neighbors when reanalysis leaves the current filter', async () => {
  let ids = ['first', 'middle', 'last'];
  fetchMock.mockImplementation((url: string, options?: RequestInit) => {
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    const parsed = new URL(url, 'http://localhost');
    if (parsed.pathname === '/api/repair/review-queue') {
      const page = Math.min(Number(parsed.searchParams.get('page')), ids.length - 1);
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: ids[page],
              album: ids[page],
              pending: 1,
              finding_types: [],
              priority: 1,
              error_count: 0,
            },
          ],
          case_ids: ids,
          total: ids.length,
          page,
          limit: 1,
        }),
      );
    }
    if (parsed.pathname === '/api/repair/review-queue/middle/reanalyze') {
      expect(options?.method).toBe('POST');
      ids = ['first', 'last'];
      return Promise.resolve(reply({ queued: true }));
    }
    if (parsed.pathname === '/api/repair/review-queue/last/reanalyze') {
      expect(options?.method).toBe('POST');
      ids = ['first'];
      return Promise.resolve(reply({ queued: true }));
    }
    const id = parsed.pathname.split('/').at(-1);
    if (['first', 'middle', 'last'].includes(id || ''))
      return Promise.resolve(
        reply({
          case_id: id,
          album: id,
          count: 1,
          status: 'pending',
          items: [],
          refresh: { state: 'complete' },
        }),
      );
    return unexpectedRequest(url);
  });

  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'first' });
  expect(screen.getByRole('button', { name: /Previous album/ })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: /Next album/ }));
  await screen.findByRole('heading', { name: 'middle' });
  await screen.findByText('Page 2 of 3');
  fireEvent.click(screen.getByRole('button', { name: /Next album/ }));
  await screen.findByRole('heading', { name: 'last' });
  await screen.findByText('Page 3 of 3');
  expect(screen.getByRole('button', { name: /Next album/ })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: /Previous album/ }));
  await screen.findByRole('heading', { name: 'middle' });
  fireEvent.click(screen.getByRole('button', { name: 'Reanalyze album' }));
  await screen.findByText('Page 2 of 2');
  expect(screen.getByRole('button', { name: /Previous album/ })).not.toBeDisabled();
  expect(screen.getByRole('button', { name: /Next album/ })).not.toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: /Next album/ }));
  await screen.findByRole('heading', { name: 'last' });
  fireEvent.click(screen.getByRole('button', { name: /Previous album/ }));
  await screen.findByRole('heading', { name: 'first' });
  await screen.findByText('Page 1 of 2');
  fireEvent.click(screen.getByRole('button', { name: /Next album/ }));
  await screen.findByRole('heading', { name: 'last' });
  fireEvent.click(screen.getByRole('button', { name: 'Reanalyze album' }));
  await screen.findByText('Page 1 of 1');
  expect(screen.getByRole('button', { name: /Next album/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: /Previous album/ })).not.toBeDisabled();
});

it('advances to the adjacent album when reanalysis completes the only item on the last page', async () => {
  window.history.replaceState(null, '', '/review?mode=all&case=album30');
  let complete = false;
  fetchMock.mockImplementation((url: string, options?: RequestInit) => {
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    const parsed = new URL(url, 'http://localhost');
    const ids = Array.from({ length: complete ? 30 : 31 }, (_, i) => `album${i}`);
    if (parsed.pathname === '/api/repair/review-queue') {
      const page = Math.min(Number(parsed.searchParams.get('page')), complete ? 0 : 1);
      return Promise.resolve(
        reply({
          items: ids.slice(page * 30, page * 30 + 30).map((id) => ({
            case_id: id,
            album: id,
            pending: 1,
            finding_types: [],
            priority: 1,
            error_count: 0,
          })),
          case_ids: ids,
          total: ids.length,
          page,
          limit: 30,
        }),
      );
    }
    if (parsed.pathname.endsWith('/reanalyze')) {
      expect(options?.method).toBe('POST');
      complete = true;
      return Promise.resolve(reply({ queued: true }));
    }
    const id = parsed.pathname.split('/').at(-1);
    if (id?.startsWith('album'))
      return Promise.resolve(
        reply({
          case_id: id,
          album: id,
          count: 1,
          status: complete && id === 'album30' ? 'completed' : 'pending',
          album_plan:
            complete && id === 'album30'
              ? {
                  assignments: [
                    {
                      status: 'confirmed',
                      fulfillment_state: 'downloaded',
                      current_title: 'Reflections',
                    },
                  ],
                  counts: { confirmed: 1 },
                }
              : undefined,
          items: [],
          refresh: { state: 'complete' },
        }),
      );
    return unexpectedRequest(url);
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'album30' });
  await screen.findByText('Page 2 of 2');
  fireEvent.click(screen.getByRole('button', { name: 'Reanalyze album' }));
  await screen.findByRole('heading', { name: 'album29' });
  await screen.findByText('Page 1 of 1');
  expect(screen.getByRole('button', { name: /Next album/ })).toBeDisabled();
});

it('filters both APIs and lets the reader jump between pages', async () => {
  fetchMock.mockImplementation((url: string) => {
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?')) {
      const params = new URL(url, 'http://localhost').searchParams;
      expect(params.get('job_id')).toBe('library_reconciliation');
      const page = Number(params.get('page'));
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: `album:1:${page}`,
              album: `Album ${page + 1}`,
              artist: 'Artist',
              pending: 1,
              priority: 1,
              finding_types: ['reconciliation_unresolved'],
              error_count: 0,
            },
          ],
          total: 3,
          page,
          limit: 1,
        }),
      );
    }
    if (url.startsWith('/api/repair/review-queue/album%3A1%3A')) {
      expect(url).toContain('?job_id=library_reconciliation');
      const page = Number(url.match(/album%3A1%3A(\d+)/)?.[1] || 0);
      return Promise.resolve(
        reply({
          case_id: `album:1:${page}`,
          album: `Album ${page + 1}`,
          artist: 'Artist',
          folder: '/music/Album',
          count: 1,
          status: 'pending',
          items: [finding],
        }),
      );
    }
    return unexpectedRequest(url);
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'Album 1' });
  expect(screen.getByText('Page 1 of 3')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Jump to page'), {
    target: { value: '2' },
  });
  await screen.findByRole('heading', { name: 'Album 3' });
  expect(screen.getByText('Page 3 of 3')).toBeTruthy();
  expect(screen.getByText(/Album 3 of 3/)).toBeTruthy();
});

it('discards a delayed automatic refresh and totals after changing filters', async () => {
  const pending: Array<(value: unknown) => void> = [];
  let delay = false;
  fetchMock.mockImplementation((url: string) => {
    if (
      url.startsWith('/api/repair/review-queue?') ||
      url.startsWith('/api/repair/review-queue/summary?')
    ) {
      const filtered = new URL(url, 'http://localhost').searchParams.get('q') === 'new';
      if (delay && !filtered) return new Promise((resolve) => pending.push(resolve));
      return Promise.resolve(
        reply(
          url.includes('/summary?')
            ? { all: filtered ? 2 : 1, review: 0, ready: 0, files: 0, ready_total: 0 }
            : { items: [], total: filtered ? 2 : 1, page: 0, limit: 30 },
        ),
      );
    }
    return Promise.resolve(auxiliary(url) ?? unexpectedRequest(url));
  });
  render(<ReviewPage />);
  await screen.findByText('1 album');
  delay = true;
  expect(screen.queryByRole('button', { name: 'Refresh view' })).toBeNull();
  fireEvent(window, new Event('focus'));
  await waitFor(() => expect(pending).toHaveLength(2));
  fireEvent.change(screen.getByPlaceholderText('Search findings'), { target: { value: 'new' } });
  await screen.findByText('2 albums in this view');
  await act(async () => {
    pending[0](reply({ all: 99, review: 99, ready: 0, files: 0, ready_total: 0 }));
    pending[1](reply({ items: [], total: 99, page: 0, limit: 30 }));
  });
  expect(screen.getByText('2 albums in this view')).toBeTruthy();
  expect(screen.queryByText(/99 albums/)).toBeNull();
});

it('preserves search and category when filtering ready albums', async () => {
  fetchMock.mockImplementation((url: string) => {
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(reply({ items: [], total: 0, page: 0, limit: 30 }));
    return Promise.resolve(auxiliary(url) ?? unexpectedRequest(url));
  });
  render(<ReviewPage />);
  await screen.findByText('1 album');
  fireEvent.change(screen.getByPlaceholderText('Search findings'), {
    target: { value: 'old search' },
  });
  fireEvent.change(screen.getByLabelText('Filter by type'), { target: { value: 'numbering' } });
  fireEvent.click(screen.getByRole('button', { name: /^Ready to apply/ }));
  await waitFor(() => {
    const calls = fetchMock.mock.calls.filter(([url]) =>
      String(url).startsWith('/api/repair/review-queue?'),
    );
    const params = new URL(calls.at(-1)![0], 'http://localhost').searchParams;
    expect(params.get('risk')).toBe('ready');
    expect(params.get('q')).toBe('old search');
    expect(params.get('category')).toBe('numbering');
  });
  expect((screen.getByPlaceholderText('Search findings') as HTMLInputElement).value).toBe(
    'old search',
  );
});

it('replaces the old queue with a retryable search error when the server is busy', async () => {
  let fail = true;
  fetchMock.mockImplementation((url: string) => {
    if (url.startsWith('/api/repair/review-queue?')) {
      const query = new URL(url, 'http://localhost').searchParams.get('q');
      if (query && fail) return Promise.resolve(reply({ error: 'Reconciliation is busy' }, 503));
      return Promise.resolve(
        reply({
          items: query
            ? []
            : [{ case_id: 'old', album: 'Old visible album', pending: 1, finding_types: [] }],
          total: query ? 0 : 1,
          page: 0,
          limit: 30,
        }),
      );
    }
    if (url.startsWith('/api/repair/review-queue/old?'))
      return Promise.resolve(
        reply({
          case_id: 'old',
          album: 'Old selected album',
          folder: '/music/Old',
          count: 1,
          status: 'pending',
          items: [],
        }),
      );
    return Promise.resolve(auxiliary(url) ?? unexpectedRequest(url));
  });
  render(<ReviewPage />);
  await screen.findByText('Old visible album');
  fireEvent.change(screen.getByPlaceholderText('Search findings'), { target: { value: 'Tron' } });
  await screen.findByRole('button', { name: 'Retry search' });
  expect(screen.queryByText('Old visible album')).toBeNull();
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry search' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Retry search' })).toBeNull());
});
