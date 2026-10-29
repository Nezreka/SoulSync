import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it } from 'vitest';

import { ReviewPage } from './review-page';
import { fetchMock, reply, finding, auxiliary } from './support/page';
import { unexpectedRequest } from './support/requests';

it('offers one album completeness choice with individually wanted tracks', async () => {
  const intent = {
    ...finding,
    finding_type: 'reconciliation_intent',
    title: 'Is Album meant to be complete?',
    details: {
      album_id: 10,
      album_title: 'Album',
      missing_tracks: [
        {
          title: 'First',
          source_track_id: 'first',
          disc_number: 1,
          track_number: 2,
          wanted_key: 'id:first:1:2',
        },
        {
          title: 'Second',
          source_track_id: 'second',
          disc_number: 1,
          track_number: 3,
          wanted_key: 'id:second:1:3',
        },
      ],
    },
    review_tier: 'manual',
  };
  const decision = {
    id: 'album_completeness',
    category: 'album_completeness',
    label: 'Album completeness',
    risk: 'manual',
    summary: 'Choose album scope',
    finding_ids: [7],
    action_count: 1,
  };
  const plan = {
    ready: 0,
    manual: 1,
    destructive: 0,
    blocked: 0,
    total: 1,
    status: 'needs_review',
    can_apply_ready: false,
    can_organize: false,
    blocker_reasons: [],
    decisions: [decision],
  };
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: 'album:1:10',
              album: 'Album',
              artist: 'Artist',
              pending: 1,
              priority: 1,
              finding_types: ['reconciliation_intent'],
              error_count: 0,
              plan,
            },
          ],
          total: 1,
          page: 0,
          limit: 30,
        }),
      );
    if (url.startsWith('/api/repair/review-queue/album%3A1%3A10?'))
      return Promise.resolve(
        reply({
          case_id: 'album:1:10',
          album: 'Album',
          artist: 'Artist',
          folder: '/music/Album',
          count: 1,
          status: 'pending',
          items: [intent],
          plan,
          decisions: [decision],
          album_plan: {
            state: 'review',
            counts: { confirmed: 0, changed: 0, uncertain: 0, missing: 2, extra: 0 },
            assignments: [
              { status: 'missing', slot_title: 'First', finding_id: 7 },
              { status: 'missing', slot_title: 'Second', finding_id: 7 },
            ],
          },
        }),
      );
    if (url.includes('/findings/7/apply') && init?.method === 'POST')
      return Promise.resolve(reply({ success: true, message: 'Partial album saved' }));
    return unexpectedRequest(url);
  });
  render(<ReviewPage />);
  await screen.findByText('Choose whether this folder represents the complete album');
  fireEvent.click(screen.getByRole('tab', { name: /^Tracks/ }));
  await screen.findByRole('heading', { name: 'Choose which missing tracks you want' });
  fireEvent.click(screen.getByRole('checkbox', { name: 'First' }));
  fireEvent.click(screen.getByRole('button', { name: 'Keep as partial · 1 wanted' }));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) =>
          String(url).includes('/findings/7/apply') &&
          JSON.parse(String((init as RequestInit).body)).fix_action === 'partial:["id:first:1:2"]',
      ),
    ).toBe(true),
  );
});

it('shows one album decision above a complete persisted track assignment', async () => {
  const numberingFinding = {
    ...finding,
    finding_type: 'reconciliation_metadata',
    title: 'Retag Hourglass',
    file_path: '/music/Album/02.flac',
    details: {
      album_id: 10,
      album_title: 'Album',
      artist_name: 'Artist',
      confidence: 'clear',
      changes: { track_number: { old: 1, new: 2 } },
    },
    review_tier: 'manual',
  };
  const decision = {
    id: 'numbering',
    category: 'numbering',
    label: 'Use continuous track numbering',
    risk: 'manual',
    summary: 'Renumber 1 track as one sequence instead of restarting at each disc.',
    finding_ids: [7],
    action_count: 1,
  };
  const plan = {
    ready: 0,
    manual: 1,
    destructive: 0,
    blocked: 0,
    total: 1,
    status: 'needs_review',
    blocker_reasons: ['The release uses a different disc or track numbering scheme'],
    can_apply_ready: false,
    can_organize: false,
    decision_count: 1,
    categories: ['numbering'],
    decisions: [decision],
  };
  fetchMock.mockImplementation((url: string) => {
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: 'album:1:10',
              album: 'Album',
              artist: 'Artist',
              pending: 1,
              priority: 1,
              finding_types: ['reconciliation_metadata'],
              error_count: 0,
              plan,
            },
          ],
          total: 1,
          page: 0,
          limit: 30,
        }),
      );
    if (url.startsWith('/api/repair/review-queue/album%3A1%3A10?'))
      return Promise.resolve(
        reply({
          case_id: 'album:1:10',
          album: 'Album',
          artist: 'Artist',
          album_id: 10,
          folder: '/music/Album',
          count: 1,
          status: 'pending',
          plan,
          decisions: [decision],
          items: [numberingFinding],
          album_plan: {
            state: 'review',
            source: 'musicbrainz',
            release_id: 'digital-release',
            numbering_mode: 'digital',
            intent_scope: 'partial',
            counts: {
              confirmed: 1,
              changed: 1,
              uncertain: 0,
              missing: 0,
              not_owned: 1,
              extra: 0,
              settled: 1,
            },
            categories: ['numbering', 'partial_albums'],
            candidates: [
              {
                source: 'musicbrainz',
                release_id: 'digital-release',
                clear: 2,
                matched: 2,
                total_slots: 3,
                missing: 1,
                extra: 0,
                continuous_numbering: true,
              },
            ],
            assignments: [
              {
                status: 'confirmed',
                file_path: '/music/Album/01.flac',
                file: '01.flac',
                current_title: 'One',
                slot_title: 'One',
              },
              {
                status: 'change',
                file_path: '/music/Album/02.flac',
                file: '02.flac',
                current_title: 'Hourglass',
                slot_title: 'Hourglass',
                finding_id: 7,
                changes: { track_number: { old: 1, new: 2 } },
              },
              { status: 'not_owned', slot_title: 'Bonus Track' },
              {
                status: 'settled',
                file_path: '/music/Album/03.flac',
                file: '03.flac',
                current_title: 'Already reviewed',
                slot_title: 'Already reviewed',
              },
            ],
          },
        }),
      );
    return unexpectedRequest(url);
  });

  render(<ReviewPage />);

  expect(await screen.findByText('Renumber tracks to match the selected release')).toBeTruthy();
  expect(screen.queryByRole('heading', { name: 'Track assignments' })).toBeNull();
  expect(screen.queryByLabelText('Album reconciliation plan')).toBeNull();
  expect(screen.getByText(/Renumber 1 track as one sequence/)).toBeTruthy();
  expect(screen.getByRole('heading', { name: 'What this decision affects' })).toBeTruthy();
  expect(screen.getAllByText('2 of 3 release slots assigned').length).toBeGreaterThan(0);
  expect(screen.getByText('2 tracks need no decision')).toBeTruthy();
  expect(screen.queryByText('1 affected action')).toBeNull();
  expect(
    screen.getByRole('button', { name: 'Plan release track numbers for 1 track' }),
  ).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Evidence' })).toBeNull();
  expect(screen.getByText('digital-release')).toBeTruthy();
  fireEvent.click(screen.getByRole('tab', { name: /Tracks/ }));
  expect(screen.getByRole('heading', { name: 'Track assignments' })).toBeTruthy();
  expect(screen.getByRole('button', { name: /Compare editions/ })).toBeTruthy();
  expect(screen.getAllByText('One disc · match release positions').length).toBeGreaterThan(0);
  expect(screen.getByText('1 outside collection')).toBeTruthy();
  expect(screen.getByText(/1 tracks outside your collection/)).toBeTruthy();
  expect(screen.getByText(/2 settled release slots/)).toBeTruthy();
  expect(screen.getByText(/1 proposed track change/)).toBeTruthy();
  const selectedRow = screen.getByRole('button', { name: /Hourglass/ });
  fireEvent.click(selectedRow);
  const selectedDetail = screen.getByLabelText('Selected action detail');
  expect(selectedDetail.closest('[role="table"]')).toBeNull();
  expect(screen.getByRole('heading', { name: 'Proposed changes' })).toBeTruthy();
  expect(screen.queryByText('Evidence and exact changes')).toBeNull();
  fireEvent.click(selectedRow);
  expect(screen.getByLabelText('Selected action detail')).toBeTruthy();
  expect(selectedRow.getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByRole('button', { name: /Hourglass/ })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Show tracks'), { target: { value: 'numbering' } });
  expect((screen.getByLabelText('Show tracks') as HTMLSelectElement).value).toBe('numbering');
  expect(screen.queryByText(/3 settled release slots/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Clear filter' }));
  expect((screen.getByLabelText('Show tracks') as HTMLSelectElement).value).toBe('');
  expect(screen.getByText(/2 settled release slots/)).toBeTruthy();
});

it('offers an exact replacement and recoverable quarantine for verified wrong audio', async () => {
  const wrongAudioFinding = {
    ...finding,
    finding_type: 'reconciliation_extra',
    title: 'Wrong audio: Who Wants to Live Forever',
    description: 'The fingerprint identifies different recordings than the file title.',
    file_path: '/music/Queen/26 - Who Wants to Live Forever.flac',
    review_tier: 'destructive',
    review_reason: 'The fingerprint identifies different audio than the expected release track',
    details: {
      album_id: 10,
      album_title: 'The Platinum Collection',
      artist_name: 'Queen',
      audio_conflict: 'different_recording',
      replacement_track: {
        name: 'Who Wants to Live Forever',
        source_track_id: 'expected-track-id',
      },
    },
  };
  const plan = {
    ready: 0,
    manual: 0,
    destructive: 1,
    blocked: 0,
    total: 1,
    status: 'needs_review',
    blocker_reasons: [wrongAudioFinding.review_reason],
    can_apply_ready: false,
    can_organize: false,
    decision_count: 1,
    categories: ['audio_conflicts'],
    decisions: [
      {
        id: 'audio_conflicts',
        category: 'audio_conflicts',
        label: 'Wrong audio',
        risk: 'destructive',
        summary: '1 local file contains different audio than the expected release track.',
        finding_ids: [7],
        action_count: 1,
      },
    ],
  };
  fetchMock.mockImplementation((url: string) => {
    if (url === '/api/repair/finding-types')
      return Promise.resolve(
        reply({
          types: [
            {
              type: 'reconciliation_extra',
              label: 'Unassigned Audio',
              verb: 'Quarantine File',
              fixable: true,
              destructive: true,
            },
          ],
        }),
      );
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: 'album:10:queen',
              album: 'The Platinum Collection',
              artist: 'Queen',
              pending: 1,
              priority: 1,
              finding_types: ['reconciliation_extra'],
              error_count: 0,
              plan,
            },
          ],
          total: 1,
          page: 0,
          limit: 30,
        }),
      );
    if (url.startsWith('/api/repair/review-queue/album%3A10%3Aqueen?'))
      return Promise.resolve(
        reply({
          case_id: 'album:10:queen',
          album: 'The Platinum Collection',
          artist: 'Queen',
          album_id: 10,
          folder: '/music/Queen/The Platinum Collection',
          count: 1,
          status: 'pending',
          plan,
          decisions: plan.decisions,
          items: [wrongAudioFinding],
        }),
      );
    return unexpectedRequest(url);
  });

  render(<ReviewPage />);
  await screen.findByText('1 local file contains different audio than the expected release track.');
  fireEvent.click(screen.getByRole('button', { name: /Confirm 1 uncertain track assignment/ }));

  expect(screen.getByRole('button', { name: 'Replace file…' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Replace file…' }));
  expect(screen.getByRole('radio', { name: /Find another copy/ })).toBeChecked();
  expect(
    screen.getByText(
      'Request a new copy. Keep the current file playable until the replacement and playlist handoff are verified, then move it to recoverable quarantine.',
    ),
  ).toBeTruthy();
});

it('puts required choices before independent safe changes on a mixed album', async () => {
  const manualFinding = {
    ...finding,
    finding_type: 'reconciliation_intent',
    title: 'Is Album meant to be complete?',
    review_tier: 'manual',
  };
  const readyFinding = {
    ...finding,
    id: 8,
    finding_type: 'reconciliation_metadata',
    title: 'Retag Track One',
    review_tier: 'ready',
    details: {
      album_id: 10,
      album_title: 'Album',
      artist_name: 'Artist',
      changes: { artist: { old: 'artist', new: 'Artist' } },
    },
  };
  const decisions = [
    {
      id: 'intent',
      category: 'album_completeness',
      label: 'Album completeness',
      risk: 'manual',
      summary: 'Choose whether this album should be complete.',
      finding_ids: [7],
      action_count: 1,
    },
    {
      id: 'metadata',
      category: 'metadata',
      label: 'Artist update',
      risk: 'ready',
      summary: '1 track: artist → Artist.',
      finding_ids: [8],
      action_count: 1,
    },
  ];
  const plan = {
    ready: 1,
    manual: 1,
    destructive: 0,
    blocked: 0,
    total: 2,
    status: 'needs_review',
    blocker_reasons: ['Choose whether the album should be complete'],
    can_apply_ready: true,
    can_organize: false,
    decision_count: 2,
    categories: ['album_completeness', 'metadata'],
    decisions,
  };
  fetchMock.mockImplementation((url: string) => {
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: 'album:1:mixed',
              album: 'Album',
              artist: 'Artist',
              pending: 2,
              priority: 1,
              finding_types: ['reconciliation_intent', 'reconciliation_metadata'],
              error_count: 0,
              plan,
            },
          ],
          total: 1,
          page: 0,
          limit: 30,
        }),
      );
    if (url.startsWith('/api/repair/review-queue/album%3A1%3Amixed?'))
      return Promise.resolve(
        reply({
          case_id: 'album:1:mixed',
          album: 'Album',
          artist: 'Artist',
          album_id: 10,
          folder: '/music/Artist/Album',
          count: 2,
          status: 'pending',
          plan,
          decisions,
          items: [manualFinding, readyFinding],
        }),
      );
    return unexpectedRequest(url);
  });

  render(<ReviewPage />);

  const choices = (
    await screen.findByText('Choose whether this folder represents the complete album')
  ).closest('section');
  const safeChanges = screen.getByLabelText('Independent safe changes');
  // Even a contradictory/stale ready flag must not hide outstanding choices.
  expect(screen.queryByRole('button', { name: 'Apply 1 ready change' })).toBeNull();
  expect(choices).not.toBeNull();
  expect(
    choices!.compareDocumentPosition(safeChanges) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(screen.queryByText('1 affected action')).toBeNull();
  expect(screen.getAllByText('Album completeness').length).toBeGreaterThan(0);
  expect(screen.queryByText('2 questions for this album')).toBeNull();
});

it('renders findings that do not have release candidates', async () => {
  const noCandidateFinding = {
    ...finding,
    title: 'Mixed albums: loose files',
    details: {
      album_title: 'Loose files',
      album_folder: '/music/Loose files',
      file_count: 2,
      checked_files: [
        {
          file: '01 - One.flac',
          tag_title: 'One',
          audio_status: 'no_match',
        },
      ],
    },
  };
  fetchMock.mockImplementation((url: string, options?: RequestInit) => {
    if (url === '/api/repair/review-queue/folder%3A1%3Aloose/reanalyze') {
      expect(options?.method).toBe('POST');
      return Promise.resolve(reply({ queued: true }));
    }
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: 'folder:1:loose',
              album: 'Loose files',
              artist: null,
              pending: 1,
              priority: 1,
              finding_types: ['reconciliation_unresolved'],
              error_count: 0,
            },
          ],
          total: 1,
          page: 0,
          limit: 30,
        }),
      );
    if (url.startsWith('/api/repair/review-queue/folder%3A1%3Aloose?'))
      return Promise.resolve(
        reply({
          case_id: 'folder:1:loose',
          album: 'Loose files',
          artist: null,
          folder: '/music/Loose files',
          count: 1,
          status: 'pending',
          items: [noCandidateFinding],
        }),
      );
    return unexpectedRequest(url);
  });

  render(<ReviewPage />);

  await screen.findByRole('heading', { name: 'Loose files' });
  expect(screen.queryByText('Technical evidence')).toBeNull();
  expect(screen.getByText('Local evidence')).toBeTruthy();
  expect(screen.queryByLabelText('Evidence summary')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Reanalyze album' }));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).endsWith('/folder%3A1%3Aloose/reanalyze')),
    ).toBe(true),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Album maintenance options' }));
  await waitFor(() =>
    expect(screen.getByRole('menuitem', { name: 'Refresh catalog evidence' })).not.toBeDisabled(),
  );
  fireEvent.click(screen.getByRole('menuitem', { name: 'Refresh catalog evidence' }));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([url, options]) =>
          String(url).endsWith('/folder%3A1%3Aloose/reanalyze') &&
          options?.body === JSON.stringify({ refresh_catalogue: true }),
      ),
    ).toBe(true),
  );
});

it('presents competing copies as one explicit keeper decision', async () => {
  const duplicateFinding = {
    ...finding,
    finding_type: 'reconciliation_duplicate',
    title: 'Duplicate copies: Cold',
    description: 'These files identify as the same release track; choose a keeper.',
    details: {
      album_title: 'The Dark',
      artist_name: 'Chelsea Wolfe',
      album_folder: '/music/Chelsea Wolfe/The Dark',
      resolution_kind: 'duplicate_copies',
      duplicate_copies: [
        {
          track_id: 'album-copy',
          title: 'Cold',
          file_path: '/music/Chelsea Wolfe/The Dark/01 - Cold.flac',
          playlists: [],
        },
        {
          track_id: 'single-copy',
          title: 'Cold',
          file_path: '/music/Chelsea Wolfe/The Dark/CHELSEA WOLFE - The Dark - 01 Cold.flac',
          playlists: ['Release Radar'],
        },
      ],
      acoustid: {
        status: 'ok',
        recordings: [
          {
            mbid: 'cold-one',
            title: 'Cold',
            artist: 'Chelsea Wolfe',
            score: 0.99,
          },
          {
            mbid: 'cold-two',
            title: 'Cold',
            artist: 'Chelsea Wolfe',
            score: 0.99,
          },
        ],
      },
    },
  };
  fetchMock.mockImplementation((url: string) => {
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: 'album:1:dark',
              album: 'The Dark',
              artist: 'Chelsea Wolfe',
              pending: 1,
              priority: 1,
              finding_types: ['reconciliation_duplicate'],
              error_count: 0,
            },
          ],
          total: 1,
          page: 0,
          limit: 30,
        }),
      );
    if (url.startsWith('/api/repair/review-queue/album%3A1%3Adark?'))
      return Promise.resolve(
        reply({
          case_id: 'album:1:dark',
          album: 'The Dark',
          artist: 'Chelsea Wolfe',
          folder: '/music/Chelsea Wolfe/The Dark',
          count: 1,
          status: 'pending',
          items: [duplicateFinding],
        }),
      );
    return unexpectedRequest(url);
  });

  render(<ReviewPage />);

  await screen.findByRole('heading', { name: 'Duplicate local copies' });
  expect(screen.getByText('Choose the copy to keep')).toBeTruthy();
  expect(screen.getByText('Playlist: Release Radar')).toBeTruthy();
  expect(screen.getAllByRole('button', { name: 'Plan to keep this copy' })).toHaveLength(2);
  expect(screen.getAllByRole('link', { name: /Recording · cold-/ })).toHaveLength(2);
});

it('describes a saved removal blocker without asking for another choice', async () => {
  const decision = {
    id: 'apply_blockers',
    category: 'apply_blockers',
    label: 'Resolve blocked actions',
    risk: 'blocked',
    action_count: 1,
    finding_ids: [7],
  };
  const plan = {
    ready: 0,
    blocked: 1,
    manual: 0,
    destructive: 0,
    total: 1,
    status: 'needs_review',
    can_apply_ready: false,
    can_organize: false,
    decisions: [decision],
  };
  const album = {
    case_id: 'blocked',
    album: 'Chopin',
    artist: 'Chopin',
    status: 'pending',
    count: 1,
    pending: 1,
    plan,
    decisions: [decision],
    items: [
      {
        ...finding,
        last_error: 'This copy is in a playlist',
        details: {
          plan_approved: true,
          planned_choice: { action: 'fix', fix_action: 'quarantine', category: 'audio_conflicts' },
        },
      },
    ],
  };
  fetchMock.mockImplementation((url: string) => {
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(reply({ items: [album], total: 1, page: 0, limit: 30 }));
    if (url.includes('/api/repair/review-queue/blocked?')) return Promise.resolve(reply(album));
    return unexpectedRequest(url);
  });
  render(<ReviewPage />);
  expect(
    await screen.findByRole('heading', { name: '1 saved action needs attention' }),
  ).toBeVisible();
  expect(screen.queryByText('1 choice needs your input')).toBeNull();
  expect(
    screen.getByText(
      'Your choices are saved. Resolve the apply issues below before retrying this album plan.',
    ),
  ).toBeVisible();
});
