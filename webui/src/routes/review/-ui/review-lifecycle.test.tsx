import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { ReviewPage } from './review-page';

const album = {
  case_id: 'album:1:first',
  album: 'First album',
  artist: 'Artist',
  folder: '/music/First',
  count: 1,
  status: 'pending',
  items: [],
  plan: {
    ready: 0,
    manual: 1,
    destructive: 0,
    blocked: 0,
    total: 1,
    status: 'needs_review',
    blocker_reasons: [],
    can_apply_ready: false,
    can_organize: false,
  },
  album_plan: {
    source: 'musicbrainz',
    release_id: 'current',
    counts: { confirmed: 1, changed: 0, uncertain: 0, missing: 0, extra: 0 },
    assignments: [
      {
        status: 'confirmed',
        file_path: '/music/First/01.flac',
        file: '01.flac',
        current_title: 'Confirmed song',
        slot_title: 'Confirmed song',
        slot_track_number: 1,
        file_duration_ms: 120000,
        slot_duration_ms: 120000,
        reasons: ['Recording identity agrees'],
      },
    ],
    candidates: [
      {
        source: 'musicbrainz',
        release_id: 'current',
        release_title: 'Current edition',
        clear: 1,
        matched: 1,
        total_slots: 1,
        missing: 0,
        extra: 0,
      },
    ],
  },
};
const next = { ...album, case_id: 'album:1:next', album: 'Another album' };
const fetchMock = vi.fn();
function reply(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}
function setup(current: unknown = album, status = 200) {
  fetchMock.mockImplementation(async (input: string) => {
    const url = new URL(input, 'http://localhost');
    if (url.pathname === '/api/repair/review-queue/summary')
      return reply({ all: 1, review: 1, ready: 0, files: 0, ready_total: 0 });
    if (url.pathname === '/api/repair/review-queue')
      return reply({ items: [{ ...next, pending: 1 }], total: 1, page: 0, limit: 30 });
    if (url.pathname.endsWith('/album:1:first')) return reply(current, status);
    if (decodeURIComponent(url.pathname).endsWith('/album:1:first')) return reply(current, status);
    if (decodeURIComponent(url.pathname).endsWith('/album:1:next')) return reply(next);
    if (url.pathname === '/api/repair/finding-types') return reply({ types: [] });
    if (url.pathname === '/api/repair/history') return reply({ runs: [] });
    if (url.pathname === '/api/repair/status') return reply({ current_job: null });
    if (url.pathname === '/api/repair/bulk-fix/status') return reply({ running: false });
    throw new Error(`Unexpected request: ${input}`);
  });
}
beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState(null, '', '/review?case=album%3A1%3Afirst');
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

it('advances only for an explicit completed album outcome', async () => {
  setup({ ...album, status: 'completed', count: 0, refresh: { state: 'complete' } });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'Another album' });
  expect(screen.queryByText('Review case not found')).toBeNull();
});
it('keeps a still-pending album open even when it leaves the current queue filter', async () => {
  setup();
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'First album' });
  expect(screen.queryByRole('heading', { name: 'Another album' })).toBeNull();
});
it('does not report completion or advance on a failed album request', async () => {
  setup({ error: 'Temporary connection failure' }, 503);
  render(<ReviewPage />);
  await screen.findByText('Temporary connection failure');
  expect(screen.queryByRole('heading', { name: 'Another album' })).toBeNull();
  expect(screen.queryByText('Album review completed.')).toBeNull();
});
it('opens evidence for confirmed rows without manufacturing a pending action', async () => {
  setup();
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'First album' });
  fireEvent.click(
    screen.getByRole('tablist', { name: 'Album review views' }).querySelectorAll('button')[2],
  );
  fireEvent.click(screen.getByRole('button', { name: 'Release order' }));
  fireEvent.click(await screen.findByRole('button', { name: /Confirmed song/ }));
  await screen.findByText('Confirmed match · no metadata edit is pending.');
  expect(screen.getByText('Recording identity agrees')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Apply this change' })).toBeNull();
});
it('puts a loaded manual release into the same candidate comparison before pinning', async () => {
  setup();
  const read = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (input: string, options?: RequestInit) => {
    if (input.endsWith('/release')) {
      expect(JSON.parse(options?.body as string).preview).toBe(true);
      return reply({
        success: true,
        candidate: {
          source: 'musicbrainz',
          release_id: '11111111-1111-1111-1111-111111111111',
          release_title: 'Manual edition',
          clear: 1,
          matched: 1,
          total_slots: 2,
          missing: 1,
          extra: 0,
          missing_tracks: ['Bonus song'],
          pairs: [
            { slot_title: 'Confirmed song', file: '01.flac', track_number: 1, confidence: 'clear' },
          ],
        },
      });
    }
    return read(input, options);
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'First album' });
  fireEvent.click(
    screen.getByRole('tablist', { name: 'Album review views' }).querySelectorAll('button')[1],
  );
  fireEvent.change(screen.getByLabelText('MusicBrainz release MBID or URL'), {
    target: { value: '11111111-1111-1111-1111-111111111111' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Load release' }));
  await screen.findByRole('button', { name: /Manual edition/ });
  expect(within(screen.getByLabelText('release context')).getByText('Bonus song')).toBeTruthy();
  expect(screen.getAllByRole('button', { name: 'Use this release' })).toHaveLength(1);
});
it('restores an operation after returning and reports its terminal result', async () => {
  setup();
  const read = fetchMock.getMockImplementation()!;
  window.sessionStorage.setItem('review-operation', 'operation-one');
  fetchMock.mockImplementation(async (input: string) =>
    new URL(input, 'http://localhost').pathname.endsWith('/bulk-fix/status')
      ? reply({ operation_id: 'operation-one', running: false, fixed: 2, total: 2 })
      : read(input),
  );
  render(<ReviewPage />);
  await screen.findByText('2 album actions completed.');
  await waitFor(() => expect(window.sessionStorage.getItem('review-operation')).toBeNull());
});

it('keeps navigation on the new album when an earlier reanalysis request finishes late', async () => {
  setup();
  const read = fetchMock.getMockImplementation()!;
  let finishReanalysis!: () => void;
  fetchMock.mockImplementation(async (input: string, options?: RequestInit) => {
    if (input.endsWith('/reanalyze')) {
      await new Promise<void>((resolve) => {
        finishReanalysis = resolve;
      });
      return reply({ queued: true });
    }
    return read(input, options);
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'First album' });
  fireEvent.click(screen.getByRole('button', { name: 'Reanalyze album' }));
  expect(screen.getAllByText(/Reanalyzing this album\./)).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: /Another album/ }));
  await screen.findByRole('heading', { name: 'Another album' });
  expect(screen.queryByText(/Reanalyzing this album\./)).toBeNull();
  await act(async () => {
    finishReanalysis();
  });
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Reanalyze album' })).not.toBeDisabled(),
  );
  expect(screen.queryByRole('heading', { name: 'First album' })).toBeNull();
});

it('describes a single available release without implying competing editions', async () => {
  setup();
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'First album' });
  fireEvent.click(screen.getByRole('tab', { name: 'Release' }));
  expect(screen.getByRole('heading', { name: 'Available release' })).toBeTruthy();
  expect(screen.queryByText(/These editions/)).toBeNull();
});

it('returns to Plan when another queue album is selected from Tracks', async () => {
  setup();
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'First album' });
  const nav = screen.getByRole('tablist', { name: 'Album review views' });
  fireEvent.click(within(nav).getByRole('tab', { name: /^Tracks/ }));
  fireEvent.click(screen.getByRole('button', { name: /Another album/ }));
  await screen.findByRole('heading', { name: 'Another album' });
  expect(
    within(screen.getByRole('tablist', { name: 'Album review views' }))
      .getByRole('tab', { name: /^Plan/ })
      .getAttribute('aria-current'),
  ).toBe('page');
});

it('keeps a failed global operation at queue scope while an album is open', async () => {
  setup();
  const read = fetchMock.getMockImplementation()!;
  window.sessionStorage.setItem('review-operation', 'global-op');
  fetchMock.mockImplementation(async (input: string) =>
    new URL(input, 'http://localhost').pathname.endsWith('/bulk-fix/status')
      ? reply({
          operation_id: 'global-op',
          review_scope: 'global',
          running: false,
          failed: 1,
          error: 'Batch verification failed',
        })
      : read(input),
  );
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'First album' });
  expect(await screen.findByRole('alert', { name: 'Queue error' })).toHaveTextContent(
    'Batch verification failed',
  );
});
