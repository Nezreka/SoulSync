import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReviewPage } from './review-page';

const fetchMock = vi.fn();

function reply(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

const finding = {
  id: 7,
  job_id: 'library_reconciliation',
  finding_type: 'reconciliation_unresolved',
  severity: 'warning',
  status: 'pending',
  title: 'Choose release: Ashes of the Wake',
  description: 'Multiple releases fit equally well.',
  file_path: null,
  details: {
    album_title: 'Ashes of the Wake',
    artist_name: 'Lamb of God',
    release_disambiguation: 'US deluxe edition',
    album_folder: '/music/Lamb of God/Ashes of the Wake',
    candidates: [
      {
        source: 'musicbrainz',
        release_id: 'release-one',
        image_url: '/api/image-cache/release-one',
        matched: 1,
        clear: 1,
        total_slots: 1,
        missing: [],
        extra: [],
        pairs: [
          {
            file: '01 - Laid to Rest.flac',
            tag_title: 'Laid to Rest',
            file_duration_ms: 240000,
            audio_title: 'Laid to Rest',
            audio_artist: 'Lamb of God',
            audio_score: 0.98,
            audio_alternatives: [
              {
                title: 'Hourglass',
                artist: 'Lamb of God',
                score: 0.98,
                mbid: 'hourglass-id',
              },
            ],
            slot_title: 'Laid to Rest',
            local_recording_disambiguation: 'acoustic',
            slot_recording_disambiguation: 'acoustic',
            slot_number: 1,
            disc_number: 1,
            slot_duration_ms: 240010,
            duration_delta_ms: 10,
            confidence: 'clear',
            reasons: ['AcoustID recording ID matches release slot'],
            ambiguity_reasons: [],
          },
        ],
      },
      {
        source: 'musicbrainz',
        release_id: 'release-two',
        matched: 1,
        clear: 1,
      },
    ],
  },
  evidence_token: 'current-evidence',
};

function auxiliary(url: string) {
  if (url.startsWith('/api/repair/review-queue/summary?'))
    return reply({ all: 1, review: 1, ready: 0, files: 0, ready_total: 0 });
  if (url === '/api/repair/finding-types') return reply({ types: [] });
  if (url === '/api/repair/bulk-fix/status') return reply({ running: false });
  if (url === '/api/repair/status') return reply({ current_job: null });
  if (url === '/api/repair/history?limit=50') return reply({ runs: [] });
  return null;
}

beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState(null, '', '/review');
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe('library reconciliation review queue', () => {
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
      throw new Error(`Unexpected request: ${url}`);
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
            JSON.parse(String((init as RequestInit).body)).fix_action ===
              'partial:["id:first:1:2"]',
        ),
      ).toBe(true),
    );
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
      throw new Error(`Unexpected request: ${url}`);
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

  it('explains release comparisons without dumping JSON', async () => {
    const ambiguousFinding = {
      ...finding,
      details: {
        ...finding.details,
        expected_title: 'Laid to Rest',
        artist_name: 'Lamb of God',
        acoustid: {
          status: 'ok',
          best_score: 0.98,
          recordings: [
            { title: 'Laid to Rest', artist: 'Lamb of God', score: 0.98 },
            { title: 'Hourglass', artist: 'Lamb of God', score: 0.98 },
          ],
        },
        ambiguity_reasons: [
          'AcoustID returned competing identities not resolved by the other evidence',
        ],
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
                case_id: 'album:1:ashes',
                album: 'Ashes of the Wake',
                artist: 'Lamb of God',
                album_thumb_url: '/api/image-cache/local-ashes',
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
      if (url.startsWith('/api/repair/review-queue/album%3A1%3Aashes?'))
        return Promise.resolve(
          reply({
            case_id: 'album:1:ashes',
            album: 'Ashes of the Wake',
            artist: 'Lamb of God',
            album_thumb_url: '/api/image-cache/local-ashes',
            folder: '/music/Lamb of God/Ashes of the Wake',
            count: 1,
            status: 'pending',
            items: [ambiguousFinding],
          }),
        );
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<ReviewPage />);
    await screen.findByRole('heading', { name: 'Ashes of the Wake' });
    fireEvent.click(screen.getByRole('tab', { name: 'Release' }));
    await screen.findByRole('heading', { name: 'Compare 2 release candidates' });
    const images = [...document.querySelectorAll('img')];
    expect(images.some((image) => image.src.includes('local-ashes'))).toBe(true);
    expect(images.some((image) => image.src.includes('release-one'))).toBe(true);
    expect(screen.getByText('Assignment settled')).toBeTruthy();
    expect(screen.getByText(/US deluxe edition/)).toBeTruthy();
    expect(screen.queryByText(/"release_id"/)).toBeNull();
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
      throw new Error(`Unexpected request: ${url}`);
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
    fireEvent.click(screen.getByRole('tab', { name: /Tracks/ }));
    expect(screen.getByRole('heading', { name: 'Track assignments' })).toBeTruthy();
    expect(screen.getByText('digital-release')).toBeTruthy();
    expect(screen.getAllByText('One disc · match release positions').length).toBeGreaterThan(0);
    expect(screen.getByText('1 outside collection')).toBeTruthy();
    expect(screen.getByText(/3 settled release slots/)).toBeTruthy();
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
    expect(screen.getByText(/3 settled release slots/)).toBeTruthy();
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
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<ReviewPage />);
    await screen.findByText(
      '1 local file contains different audio than the expected release track.',
    );
    fireEvent.click(screen.getByRole('button', { name: /Confirm 1 uncertain track assignment/ }));

    expect(
      screen.getByRole('button', { name: 'Plan replacement with correct track' }),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Plan quarantine of wrong file' })).toBeTruthy();
    expect(
      screen.getByText(
        'Requests the exact track from the selected release, then moves this file to recoverable quarantine.',
      ),
    ).toBeTruthy();
  });

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
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<ReviewPage />);
    await screen.findByText(/Last reconciliation stopped/);
    expect(screen.getByText(/2539 files checked/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Scan library' }));
    await waitFor(() => expect(screen.getByText(/Library reconciliation queued/)).toBeTruthy());
  });

  it.each(['metadata', 'user', undefined])('applies a ready plan (%s)', async (evidence) => {
    const readyFinding = {
      ...finding,
      finding_type: 'reconciliation_metadata',
      title: 'Retag Laid to Rest',
      details: {
        album_id: 10,
        album_title: 'Ashes of the Wake',
        artist_name: 'Lamb of God',
        assignment_evidence: evidence,
        changes: {
          album_artist: { old: 'Various Artists', new: 'Lamb of God' },
          ...(evidence !== 'metadata'
            ? { musicbrainz_recording_id: { old: 'old-id', new: 'new-id' } }
            : {}),
        },
      },
      review_tier: 'ready',
      review_reason:
        evidence === 'user'
          ? 'The assignment was confirmed during review'
          : 'Metadata supports this repair; audio identity is not independently confirmed',
    };
    const plan = {
      ready: 1,
      manual: 0,
      destructive: 0,
      blocked: 0,
      total: 1,
      status: 'ready',
      blocker_reasons: [],
      can_apply_ready: true,
      can_organize: true,
      decision_count: 1,
      categories: ['metadata'],
      decisions: [
        {
          id: 'metadata',
          category: 'metadata',
          label: 'Album artist update',
          risk: 'ready',
          summary: '1 track: Various Artists → Lamb of God.',
          finding_ids: [7],
          action_count: 1,
        },
      ],
    };
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/repair/finding-types')
        return Promise.resolve(
          reply({
            types: [
              {
                type: 'reconciliation_metadata',
                label: 'Metadata repair',
                verb: 'Apply repair',
                fixable: true,
                destructive: false,
              },
            ],
          }),
        );
      if (url.includes('/reorganize/preview'))
        return Promise.resolve(reply({ success: true, tracks: [] }));
      if (url.startsWith('/api/repair/review-queue/apply-ready?'))
        return Promise.resolve(
          reply({
            signature: 'reviewed',
            action_count: 1,
            file_actions: 0,
            albums: [
              {
                album: 'Ashes of the Wake',
                actions: [{ description: 'Update artist metadata' }],
              },
            ],
          }),
        );
      if (url === '/api/repair/review-queue/apply-ready') {
        expect(JSON.parse(String(options?.body))).toEqual({
          case_id: 'album:1:ashes',
          organize: false,
          signature: 'reviewed',
        });
        return Promise.resolve(reply({ started: true, total: 1 }));
      }
      if (url === '/api/repair/bulk-fix/status')
        return Promise.resolve(reply({ running: false, fixed: 1, failed: 0, total: 1 }));
      const common = auxiliary(url);
      if (common) return Promise.resolve(common);
      if (url.startsWith('/api/repair/review-queue?'))
        return Promise.resolve(
          reply({
            items: [
              {
                case_id: 'album:1:ashes',
                album: 'Ashes of the Wake',
                artist: 'Lamb of God',
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
      if (url.startsWith('/api/repair/review-queue/album%3A1%3Aashes?'))
        return Promise.resolve(
          reply({
            case_id: 'album:1:ashes',
            album: 'Ashes of the Wake',
            artist: 'Lamb of God',
            album_id: 10,
            folder: '/music/Lamb of God/Ashes of the Wake',
            count: 1,
            status: 'pending',
            plan,
            items: [readyFinding],
          }),
        );
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<ReviewPage />);

    expect((await screen.findAllByText('Various Artists → Lamb of God')).length).toBeGreaterThan(0);
    expect(screen.getByText('Plan ready to apply · 1 action')).toBeTruthy();
    expect(screen.getByText(/All required decisions are planned/)).toBeVisible();
    expect(
      screen.queryByText(
        /passed the album and audio checks|supported by the album and audio evidence/,
      ),
    ).toBeNull();
    expect(screen.queryByRole('heading', { name: '1 question for this album' })).toBeNull();
    expect(screen.queryByText('Review ready changes')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Apply 1 ready change' }));
    await screen.findByRole('dialog', { name: 'Apply ready album plans' });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(
      fetchMock.mock.calls.filter(
        ([url, options]) =>
          url === '/api/repair/review-queue/apply-ready' && options?.method === 'POST',
      ),
    ).toHaveLength(0);
    expect(screen.queryByText(/Applying album changes/)).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Apply 1 ready change' })).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Apply 1 ready change' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Apply plans' }));
    await waitFor(() => expect(screen.getByText('1 album action completed.')).toBeTruthy(), {
      timeout: 3000,
    });
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
      throw new Error(`Unexpected request: ${url}`);
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
      throw new Error(`Unexpected request: ${url}`);
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
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Album actions' })).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Album actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Refresh catalog evidence' }));
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
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<ReviewPage />);

    await screen.findByRole('heading', { name: 'Duplicate local copies' });
    expect(screen.getByText('Choose the copy to keep')).toBeTruthy();
    expect(screen.getByText('Playlist: Release Radar')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Plan to keep this copy' })).toHaveLength(2);
    expect(screen.getByText('2 linked recording IDs')).toBeTruthy();
  });

  it('pins the chosen release with its evidence token', async () => {
    let applied = false;
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/repair/finding-types')
        return Promise.resolve(
          reply({
            types: [
              {
                type: 'reconciliation_release',
                label: 'Release Ambiguity',
                verb: 'Choose Release',
                fixable: true,
                destructive: false,
              },
            ],
          }),
        );
      const common = auxiliary(url);
      if (common) return Promise.resolve(common);
      if (url.startsWith('/api/repair/review-queue?'))
        return Promise.resolve(
          reply({
            items: applied
              ? []
              : [
                  {
                    case_id: 'album:1:ashes',
                    album: 'Ashes of the Wake',
                    artist: 'Lamb of God',
                    pending: 1,
                    priority: 1,
                    finding_types: ['reconciliation_release'],
                    error_count: 0,
                  },
                ],
            total: applied ? 0 : 1,
            page: 0,
            limit: 30,
          }),
        );
      if (url.includes('/findings/7/apply')) {
        const body = JSON.parse(options?.body as string) as Record<string, unknown>;
        expect(body).toMatchObject({
          action: 'fix',
          fix_action: '0',
          evidence_token: 'current-evidence',
        });
        applied = true;
        return Promise.resolve(reply({ success: true, message: 'Release pinned' }));
      }
      if (url.startsWith('/api/repair/review-queue/album%3A1%3Aashes?'))
        return Promise.resolve(
          applied
            ? reply({ error: 'Review case not found' }, 404)
            : reply({
                case_id: 'album:1:ashes',
                album: 'Ashes of the Wake',
                artist: 'Lamb of God',
                folder: '/music/Lamb of God/Ashes of the Wake',
                count: 1,
                status: 'pending',
                items: [{ ...finding, finding_type: 'reconciliation_release' }],
              }),
        );
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<ReviewPage />);
    await screen.findByRole('heading', { name: 'Ashes of the Wake' });
    fireEvent.click(screen.getByRole('tab', { name: 'Release' }));
    const button = await screen.findByRole('button', { name: 'Use this release' });
    fireEvent.click(button);
    await waitFor(() => expect(applied).toBe(true));
  });

  it('shows edition artwork and colored fit in the album plan', async () => {
    const releaseFinding = {
      ...finding,
      finding_type: 'reconciliation_release',
      details: {
        ...finding.details,
        candidates: [
          {
            source: 'musicbrainz',
            release_id: 'first-release',
            release_title: 'Album',
            image_url: '/api/image-cache/first-release',
            matched: 12,
            total_slots: 12,
            missing: [],
            extra: [],
          },
          {
            source: 'musicbrainz',
            release_id: 'second-release',
            release_title: 'Album (deluxe)',
            matched: 10,
            total_slots: 12,
            missing: ['Bonus 1', 'Bonus 2'],
            extra: ['local.flac'],
          },
        ],
      },
    };
    const decision = {
      id: 'release_conflicts',
      category: 'release_conflicts',
      label: 'Release or edition',
      risk: 'manual',
      summary: 'Multiple releases fit equally well.',
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
      blocker_reasons: [],
      can_apply_ready: false,
      can_organize: false,
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
                case_id: 'album:1:ashes',
                album: 'Album',
                artist: 'Artist',
                album_thumb_url: '/api/image-cache/local-album',
                pending: 1,
                priority: 1,
                finding_types: ['reconciliation_release'],
                error_count: 0,
                plan,
              },
            ],
            total: 1,
            page: 0,
            limit: 30,
          }),
        );
      if (url.startsWith('/api/repair/review-queue/album%3A1%3Aashes?'))
        return Promise.resolve(
          reply({
            case_id: 'album:1:ashes',
            album: 'Album',
            artist: 'Artist',
            album_thumb_url: '/api/image-cache/local-album',
            folder: '/music/Album',
            count: 1,
            status: 'pending',
            plan,
            decisions: [decision],
            items: [releaseFinding],
          }),
        );
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<ReviewPage />);
    await screen.findByRole('heading', { name: 'Album' });
    fireEvent.click(screen.getByRole('tab', { name: 'Release' }));
    expect(
      await screen.findByRole('heading', { name: 'Choose the edition for this album' }),
    ).toBeTruthy();
    const images = [...document.querySelectorAll('img')];
    expect(images.filter((image) => image.src.includes('local-album'))).toHaveLength(2);
    expect(images.some((image) => image.src.includes('first-release'))).toBe(true);
    expect(
      screen.getAllByText('12/12 assigned')[0].closest('[data-tone]')?.getAttribute('data-tone'),
    ).toBe('success');
    expect(screen.getByText('2 missing').closest('[data-tone]')?.getAttribute('data-tone')).toBe(
      'danger',
    );
    expect(screen.getByText('1 extra').closest('[data-tone]')?.getAttribute('data-tone')).toBe(
      'warning',
    );
  });
});

it('discards a delayed manual refresh and totals after changing filters', async () => {
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
    return Promise.resolve(auxiliary(url));
  });
  render(<ReviewPage />);
  await screen.findByText('1 album');
  delay = true;
  fireEvent.click(screen.getByRole('button', { name: 'Reload queue' }));
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

it('clears search and category when opening the global ready batch', async () => {
  fetchMock.mockImplementation((url: string) => {
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(reply({ items: [], total: 0, page: 0, limit: 30 }));
    return Promise.resolve(auxiliary(url));
  });
  render(<ReviewPage />);
  await screen.findByText('1 album');
  fireEvent.change(screen.getByPlaceholderText('Search findings'), {
    target: { value: 'old search' },
  });
  fireEvent.change(screen.getByLabelText('Filter by type'), { target: { value: 'numbering' } });
  fireEvent.click(screen.getByRole('button', { name: 'Review ready batch' }));
  await waitFor(() => {
    const calls = fetchMock.mock.calls.filter(([url]) =>
      String(url).startsWith('/api/repair/review-queue?'),
    );
    const params = new URL(calls.at(-1)![0], 'http://localhost').searchParams;
    expect(params.get('risk')).toBe('ready');
    expect(params.get('q') || '').toBe('');
    expect(params.get('category') || '').toBe('');
  });
  expect((screen.getByPlaceholderText('Search findings') as HTMLInputElement).value).toBe('');
});

it('keeps an album selected when reopening the already visible ready batch', async () => {
  window.history.replaceState(null, '', '/review?mode=ready');
  fetchMock.mockImplementation((url: string) => {
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: 'album:1:10',
              album: 'Ready Album',
              artist: 'Artist',
              pending: 1,
              finding_types: [],
            },
          ],
          total: 1,
          page: 0,
          limit: 30,
        }),
      );
    if (url.startsWith('/api/repair/review-queue/album%3A1%3A10'))
      return Promise.resolve(
        reply({
          case_id: 'album:1:10',
          album: 'Ready Album',
          artist: 'Artist',
          folder: '/music/Album',
          count: 1,
          status: 'pending',
          items: [finding],
        }),
      );
    throw new Error(`Unexpected request: ${url}`);
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'Ready Album' });
  fireEvent.click(screen.getByRole('button', { name: 'Review ready batch' }));
  expect(screen.getByRole('heading', { name: 'Ready Album' })).toBeTruthy();
});
