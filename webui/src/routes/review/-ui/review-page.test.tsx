import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
      { source: 'musicbrainz', release_id: 'release-two', matched: 1, clear: 1 },
    ],
  },
  evidence_token: 'current-evidence',
};

function auxiliary(url: string) {
  if (url === '/api/repair/finding-types') return reply({ types: [] });
  if (url === '/api/repair/status') return reply({ current_job: null });
  if (url === '/api/repair/history?limit=50') return reply({ runs: [] });
  return null;
}

beforeEach(() => {
  window.history.replaceState(null, '', '/review');
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe('library reconciliation review queue', () => {
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
    fireEvent.change(screen.getByLabelText('Jump to page'), { target: { value: '2' } });
    await screen.findByRole('heading', { name: 'Album 3' });
    expect(screen.getByText('Page 3 of 3')).toBeTruthy();
    expect(screen.getByText(/Album 3 of 3/)).toBeTruthy();
  });

  it('explains release comparisons without dumping JSON', async () => {
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
            folder: '/music/Lamb of God/Ashes of the Wake',
            count: 1,
            status: 'pending',
            items: [finding],
          }),
        );
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<ReviewPage />);
    await screen.findByRole('heading', { name: 'Compared releases' });
    expect(screen.getByText('Candidate 1 · musicbrainz')).toBeTruthy();
    expect(screen.getByText('Candidate 2 · musicbrainz')).toBeTruthy();
    expect(screen.getByText('AcoustID recording ID matches release slot')).toBeTruthy();
    expect(screen.getByText('Exact')).toBeTruthy();
    expect(screen.getByText(/Δ 0.01s/)).toBeTruthy();
    expect(screen.getByText(/AcoustID alternate ruled out: Hourglass/)).toBeTruthy();
    expect(screen.getAllByText('Recording: acoustic')).toHaveLength(2);
    expect(screen.getByText('US deluxe edition')).toBeTruthy();
    expect(screen.getByText('1 confirmed match')).toBeTruthy();
    expect(screen.getByText(/Track comparisons were not saved/)).toBeTruthy();
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
      label: 'Numbering convention',
      risk: 'manual',
      summary: '1 track or disc position change shares one convention choice.',
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
              intent_scope: 'partial',
              counts: {
                confirmed: 1,
                changed: 1,
                uncertain: 0,
                missing: 0,
                not_owned: 1,
                extra: 0,
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
              ],
            },
          }),
        );
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<ReviewPage />);

    expect(await screen.findByRole('heading', { name: 'Track assignment' })).toBeTruthy();
    expect(screen.getByText('Numbering convention')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Use selected release numbering' })).toBeTruthy();
    expect(screen.getByText('digital-release')).toBeTruthy();
    expect(screen.getByText('Continuous digital numbering')).toBeTruthy();
    expect(screen.getByText('○ 1 outside collection')).toBeTruthy();
    expect(screen.getByText('✓ 2 settled release slots')).toBeTruthy();
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
    await waitFor(() => expect(screen.getByText(/Library reconciliation was queued/)).toBeTruthy());
  });

  it('separates ready album changes and applies only the planned subset', async () => {
    const readyFinding = {
      ...finding,
      finding_type: 'reconciliation_metadata',
      title: 'Retag Laid to Rest',
      details: {
        album_id: 10,
        album_title: 'Ashes of the Wake',
        artist_name: 'Lamb of God',
        changes: { album_artist: { old: 'Various Artists', new: 'Lamb of God' } },
      },
      review_tier: 'ready',
      review_reason: 'Audio and release evidence support this metadata repair',
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
      if (url === '/api/repair/review-queue/apply-ready') {
        expect(JSON.parse(String(options?.body))).toEqual({
          case_id: 'album:1:ashes',
          organize: false,
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
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<ReviewPage />);

    expect(await screen.findByText('Ready to reconcile')).toBeTruthy();
    expect(screen.getAllByText('✓ 1 ready').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: 'Apply 1 ready change' }));
    await waitFor(() => expect(screen.getByText('1 ready change applied.')).toBeTruthy());
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
    fetchMock.mockImplementation((url: string) => {
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
    expect(screen.getByText('Technical evidence')).toBeTruthy();
    expect(screen.queryByLabelText('Evidence summary')).toBeNull();
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
            { mbid: 'cold-one', title: 'Cold', artist: 'Chelsea Wolfe', score: 0.99 },
            { mbid: 'cold-two', title: 'Cold', artist: 'Chelsea Wolfe', score: 0.99 },
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
    expect(screen.getAllByRole('button', { name: 'Keep this copy' })).toHaveLength(2);
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
          fix_action: '1',
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
    const buttons = await screen.findAllByRole('button', { name: 'Pin this release' });
    fireEvent.click(buttons[1]);
    await screen.findByText('Release pinned');
  });
});
