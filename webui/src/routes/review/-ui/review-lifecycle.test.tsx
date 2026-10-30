import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { reviewCaseFixture } from '../-test/fixtures';
import { ReviewPage } from './review-page';
import { unexpectedRequest } from './support/requests';

const album = reviewCaseFixture(false, {
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
    state: 'review',
    categories: [],
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
        continuous_numbering: false,
        clear: 1,
        matched: 1,
        total_slots: 1,
        missing: 0,
        extra: 0,
      },
    ],
  },
});
const next = { ...album, case_id: 'album:1:next', album: 'Another album' };

const fetchMock = vi.fn();

function reply(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

function setup(current: unknown = album, status = 200) {
  fetchMock.mockImplementation(async (input: string) => {
    const url = new URL(input, 'http://localhost');
    if (url.pathname.endsWith('/playlist-references')) return reply({ playlists: [] });
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
    return unexpectedRequest(input);
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
  await screen.findByText('Assignment settled · no metadata edit is pending.');
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

it.each(['success', 'failure'] as const)(
  'keeps the next album request alive when a previous decision returns %s',
  async (outcome) => {
    setup(reviewCaseFixture(false, { case_id: album.case_id, album: album.album }));
    const read = fetchMock.getMockImplementation()!;
    let finishSave!: () => void;
    let finishNext!: () => void;
    let nextSignal: AbortSignal | null | undefined;
    fetchMock.mockImplementation(async (input: string, options?: RequestInit) => {
      if (input.includes('/decisions/metadata/apply')) {
        await new Promise<void>((resolve) => {
          finishSave = resolve;
        });
        return reply({ success: outcome === 'success', error: 'Previous album failed' });
      }
      if (
        decodeURIComponent(new URL(input, 'http://localhost').pathname).endsWith('/album:1:next')
      ) {
        nextSignal = options?.signal;
        await new Promise<void>((resolve) => {
          finishNext = resolve;
        });
        return reply(next);
      }
      return read(input, options);
    });
    render(<ReviewPage />);
    await screen.findByRole('heading', { name: 'First album' });
    fireEvent.click(screen.getByRole('button', { name: /Use 1 proposed change/ }));
    await waitFor(() => expect(finishSave).toBeTypeOf('function'));
    fireEvent.click(screen.getByRole('button', { name: /Another album/ }));
    await waitFor(() => expect(finishNext).toBeTypeOf('function'));
    await act(async () => {
      finishSave();
    });
    expect(nextSignal?.aborted).toBe(false);
    await act(async () => {
      finishNext();
    });
    await screen.findByRole('heading', { name: 'Another album' });
    expect(screen.queryByText(/Previous album failed/)).toBeNull();
    expect(screen.queryByText('Album plan saved. Apply it when you are ready.')).toBeNull();
  },
);

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

it.each(['edit', 'navigate'] as const)(
  'discards a late manual release lookup after %s',
  async (action) => {
    setup();
    const read = fetchMock.getMockImplementation()!;
    let finish!: () => void;
    fetchMock.mockImplementation(async (input: string, options?: RequestInit) => {
      if (input.endsWith('/release')) {
        await new Promise<void>((resolve) => {
          finish = resolve;
        });
        return reply({
          success: true,
          candidate: {
            source: 'musicbrainz',
            release_id: '11111111-1111-1111-1111-111111111111',
            release_title: 'Obsolete lookup',
          },
        });
      }
      return read(input, options);
    });
    render(<ReviewPage />);
    await screen.findByRole('heading', { name: 'First album' });
    fireEvent.click(screen.getByRole('tab', { name: 'Release' }));
    fireEvent.change(screen.getByLabelText('MusicBrainz release MBID or URL'), {
      target: { value: '11111111-1111-1111-1111-111111111111' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Load release' }));
    await waitFor(() => expect(finish).toBeTypeOf('function'));
    if (action === 'edit') {
      fireEvent.change(screen.getByLabelText('MusicBrainz release MBID or URL'), {
        target: { value: '22222222-2222-2222-2222-222222222222' },
      });
    } else {
      fireEvent.click(screen.getByRole('button', { name: /Another album/ }));
      await screen.findByRole('heading', { name: 'Another album' });
      fireEvent.click(screen.getByRole('tab', { name: 'Release' }));
    }
    await act(async () => finish());
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Load release' })).not.toBeDisabled(),
    );
    expect(screen.queryByText('Obsolete lookup')).toBeNull();
    expect(screen.getByLabelText('MusicBrainz release MBID or URL')).toHaveValue(
      action === 'edit' ? '22222222-2222-2222-2222-222222222222' : '',
    );
  },
);

it('opens a requested decision, then returns to a filtered list with matching decision navigation', async () => {
  const items = [7, 8, 9].map((id) => ({
    id,
    title: `Track ${id}`,
    description: `Evidence for track ${id}`,
    file_path: `/music/First/${id}.flac`,
    finding_type: id === 7 ? 'reconciliation_unresolved' : 'reconciliation_metadata',
    review_tier: id === 7 ? 'manual' : 'ready',
    details: { changes: id === 7 ? {} : { title: { old: `track ${id}`, new: `Track ${id}` } } },
  }));
  const decisions = [
    {
      id: 'audio',
      category: 'audio_conflicts',
      label: 'Audio assignment',
      risk: 'manual',
      finding_ids: [7],
      action_count: 1,
    },
  ];
  setup({
    ...album,
    count: 3,
    items,
    decisions,
    album_plan: {
      ...album.album_plan,
      assignments: items.map((item) => ({
        status: item.id === 7 ? 'uncertain' : 'change',
        finding_id: item.id,
        file_path: item.file_path,
        file: `${item.id}.flac`,
        current_title: `Local ${item.id}`,
        slot_title: `Slot ${item.id}`,
        changes: item.details.changes,
      })),
    },
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'First album' });
  fireEvent.click(screen.getByRole('button', { name: /Confirm 1 uncertain track assignment/ }));
  const inspector = screen.getByLabelText('Selected track details');
  expect(inspector).toHaveAttribute('data-open', 'true');
  expect(within(inspector).getByText('Evidence for track 7')).toBeTruthy();
  expect(within(inspector).getByRole('button', { name: /Next decision/ })).not.toBeDisabled();
  fireEvent.click(screen.getByRole('tab', { name: /^Plan/ }));
  fireEvent.click(within(screen.getByLabelText('Proposed metadata by field')).getByRole('button'));
  expect(screen.getByLabelText('Selected track details')).not.toHaveAttribute('data-open');
  const selected = screen.getByLabelText('Selected action detail');
  expect(within(selected).getByText('Evidence for track 8')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /Next decision/ }));
  expect(screen.getByText('Evidence for track 9')).toBeTruthy();
  expect(screen.getByRole('button', { name: /Next decision/ })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: /Previous decision/ }));
  expect(screen.getByText('Evidence for track 8')).toBeTruthy();
});

it('does not replace a newly selected album when release pinning finishes late', async () => {
  setup();
  const read = fetchMock.getMockImplementation()!;
  let finish!: () => void;
  fetchMock.mockImplementation(async (input: string, options?: RequestInit) => {
    if (input.endsWith('/release')) {
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
      return reply({ success: true });
    }
    return read(input, options);
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'First album' });
  fireEvent.click(screen.getByRole('tab', { name: 'Release' }));
  fireEvent.click(screen.getByRole('button', { name: 'Use this release' }));
  await waitFor(() => expect(finish).toBeTypeOf('function'));
  fireEvent.click(screen.getByRole('button', { name: /Another album/ }));
  await screen.findByRole('heading', { name: 'Another album' });
  await act(async () => finish());
  expect(screen.queryByRole('heading', { name: 'First album' })).toBeNull();
  expect(screen.getByRole('heading', { name: 'Another album' })).toBeTruthy();
});

it('advances after saving a track choice and keeps navigation when returning to the decided track', async () => {
  const items = [7, 8].map((id) => ({
    id,
    title: `Track ${id}`,
    file_path: `/music/First/${id}.flac`,
    finding_type: 'reconciliation_metadata',
    review_tier: 'ready',
    evidence_token: `token-${id}`,
    details: { changes: { title: { old: `track ${id}`, new: `Track ${id}` } } },
  }));
  const current = {
    ...album,
    count: 2,
    items,
    album_plan: {
      ...album.album_plan,
      assignments: items.map((item) => ({
        status: 'change',
        finding_id: item.id,
        file_path: item.file_path,
        file: `${item.id}.flac`,
        current_title: `Track ${item.id}`,
        slot_title: `Track ${item.id}`,
        changes: item.details.changes,
      })),
    },
  };
  setup(current);
  const read = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (input: string, options?: RequestInit) => {
    if (input === '/api/repair/finding-types')
      return reply({ types: [{ finding_type: 'reconciliation_metadata', fixable: true }] });
    if (input.endsWith('/findings/7/apply')) {
      Object.assign(current.items[0].details, {
        planned_choice: { action: 'dismiss', fix_action: '' },
      });
      return reply({ success: true, message: 'Choice saved.', refresh_queued: true });
    }
    if (decodeURIComponent(input.split('?')[0]).endsWith('/album:1:first'))
      return reply(structuredClone(current));
    return read(input, options);
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'First album' });
  fireEvent.click(screen.getByRole('tab', { name: /^Tracks/ }));
  fireEvent.click(screen.getAllByRole('button', { name: /Track 7/ })[0]);
  fireEvent.click(screen.getByRole('button', { name: 'Leave as-is' }));
  await waitFor(() =>
    expect(
      within(screen.getByLabelText('Selected action detail')).getByRole('heading', { level: 3 }),
    ).toHaveTextContent('Track 8'),
  );
  expect(screen.queryByText(/Updating this album plan/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /Previous decision/ }));
  expect(screen.getByRole('status')).toHaveTextContent('Leave as-is planned');
  expect(screen.getByRole('button', { name: /Next decision/ })).toBeEnabled();
});

it('presents an unidentified release with actual local files and a path to identify it', async () => {
  const current = {
    ...album,
    items: [
      {
        id: 7,
        finding_type: 'reconciliation_unresolved',
        title: 'Identify release',
        file_path: null,
        details: {
          attempted_releases: [],
          checked_files: [
            { file: '01 - Études.mp3', file_path: '/music/First/01.mp3', audio_status: 'no_match' },
          ],
        },
      },
    ],
    album_plan: {
      ...album.album_plan,
      state: 'unidentified',
      source: '',
      release_id: '',
      candidates: [],
      assignments: [
        {
          status: 'uncertain',
          file_path: '/music/First/01.mp3',
          file: '01 - Études.mp3',
          current_title: 'Études',
          file_duration_ms: 190000,
          acoustid: { status: 'no_match' },
        },
      ],
    },
  };
  setup(current);
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'Release not identified' });
  expect(screen.getByText(/1 local file found/)).toBeVisible();
  expect(screen.queryByText(/No track assignment data/)).toBeNull();
  expect(screen.queryByText(/Choose the correct action after/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Identify release…' }));
  expect(screen.getByRole('textbox', { name: 'MusicBrainz release MBID or URL' })).toBeVisible();
});
