import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewFinding, AlbumAssignment, ReviewCase } from '../-review.api';
import type { ReleaseEvidence } from './reconciliation-evidence';

import {
  ReconciliationEvidence,
  ReleaseComparison,
  AssignmentEvidence,
} from './reconciliation-evidence';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('explains the actual alternative that requires a release decision', () => {
  render(
    <ReleaseComparison
      candidates={[
        { source: 'musicbrainz', release_id: 'current', release_title: 'Current edition' },
        {
          source: 'musicbrainz',
          release_id: 'alternative',
          release_title: 'Other edition',
          difference_summary: 'Home: disc 1, track 1 → disc 2, track 1.',
        },
      ]}
      selected={1}
      onSelect={vi.fn()}
      decisionReason="Choose between different track positions."
    />,
  );
  expect(screen.getByText('Choose between different track positions.')).toBeVisible();
  expect(screen.getAllByText('Home: disc 1, track 1 → disc 2, track 1.')).toHaveLength(2);
});

const release: ReleaseEvidence = {
  source: 'musicbrainz',
  release_id: 'selected',
  release_title: 'Catalogue album',
  disambiguation: 'Japanese CD',
  clear: 1,
  matched: 1,
  missing: 0,
  extra: 0,
  total_slots: 1,
  continuous_numbering: false,
};

const finding = {
  id: 1,
  file_path: '/music/Local album/Song.flac',
  details: {
    source: release.source,
    release_id: release.release_id,
    album_title: 'Local display album (anniversary edition)',
    current_tags: { title: 'Song', album: 'Local embedded album' },
    expected_title: 'Song',
  },
} as unknown as ReviewFinding;

function releaseSection() {
  return within(screen.getByText('Release track', { exact: true }).closest('section')!);
}

it('retains exact recording support when several IDs share one displayed identity', () => {
  render(
    <ReconciliationEvidence
      finding={{
        ...finding,
        details: {
          ...finding.details,
          expected_title: 'Original song title',
          recording_mbid: 'selected-recording',
          acoustid: {
            status: 'ok',
            recordings: [
              {
                title: 'Alternate catalog title',
                artist: 'Artist',
                mbid: 'selected-recording',
                score: 1,
              },
              {
                title: 'Alternate catalog title',
                artist: 'Artist',
                mbid: 'another-recording',
                score: 1,
              },
            ],
          },
        },
      }}
      prominent
    />,
  );
  expect(screen.queryByText(/No AcoustID candidate names/)).toBeNull();
  expect(screen.getByRole('link', { name: /Selected recording/ })).toBeVisible();
});

it('opens fresh embedded file metadata using the enclosing album case', async () => {
  const fetch = vi.fn().mockResolvedValue({
    json: async () => ({
      success: true,
      record: { 'Tag: ARTIST': ['Artist', 'Guest'], 'Tag: COMMENT': ['Original file note'] },
    }),
  });
  vi.stubGlobal('fetch', fetch);
  render(<ReconciliationEvidence finding={finding} caseId="folder:album" />);
  fireEvent.click(screen.getByRole('button', { name: 'File metadata' }));
  const dialog = screen.getByRole('dialog', { name: 'File metadata' });
  expect(await within(dialog).findByText('Tag: ARTIST')).toBeVisible();
  expect(await within(dialog).findByText('Original file note', { exact: false })).toBeVisible();
  expect(fetch.mock.calls[0][0]).toBe(
    '/api/repair/review-queue/folder%3Aalbum/file-metadata?path=%2Fmusic%2FLocal+album%2FSong.flac',
  );
  expect(within(dialog).queryByText('Track DB Record')).toBeNull();
});

it('keeps selected catalogue identity separate from local display and embedded album names', () => {
  render(<ReconciliationEvidence finding={finding} release={release} />);
  const local = within(screen.getByText('Local evidence').closest('section')!);
  expect(local.getByText('Local embedded album')).toBeVisible();
  expect(releaseSection().getByText('Catalogue album')).toBeVisible();
  expect(releaseSection().getByText('Japanese CD')).toBeVisible();
  expect(screen.queryByText('Local display album (anniversary edition)')).toBeNull();
});

it.each([{ source: 'deezer' }, { release_id: 'other-edition' }])(
  'does not borrow catalogue metadata from a different source or release: %j',
  (mismatch) => {
    render(<ReconciliationEvidence finding={finding} release={{ ...release, ...mismatch }} />);
    expect(releaseSection().queryByText('Catalogue album')).toBeNull();
    expect(releaseSection().queryByText('Japanese CD')).toBeNull();
    expect(releaseSection().queryByText(/Local/)).toBeNull();
  },
);

it('does not fill an empty catalogue edition with stale finding or local wording', () => {
  render(
    <ReconciliationEvidence
      finding={{
        ...finding,
        details: { ...finding.details, release_disambiguation: 'Old edition' },
      }}
      release={{ ...release, disambiguation: '' }}
    />,
  );
  expect(releaseSection().queryByText('Edition')).toBeNull();
});

it('uses catalogue identity for settled assignments too', () => {
  render(
    <AssignmentEvidence
      row={
        {
          status: 'confirmed',
          current_title: 'Song',
          current_tags: { album: 'Local embedded album' },
          ambiguity_reasons: ['Previous uncertainty'],
        } as AlbumAssignment
      }
      album={
        {
          album: 'Local display album',
          album_plan: {
            source: release.source,
            release_id: release.release_id,
            candidates: [release],
          },
        } as ReviewCase
      }
    />,
  );
  expect(releaseSection().getByText('Catalogue album')).toBeVisible();
  expect(releaseSection().queryByText('Local display album')).toBeNull();
  expect(screen.queryByText('Why this needs review')).toBeNull();
});

it('puts unresolved assignment explanations before controls without reviving a saved decision', () => {
  const row = {
    status: 'uncertain',
    current_title: 'Song',
    ambiguity_reasons: ['Two assignments fit'],
  } as AlbumAssignment;
  const album = { album: 'Album' } as ReviewCase;
  const { rerender } = render(
    <AssignmentEvidence row={row} album={album} controls={<button>Review assignment</button>} />,
  );
  expect(
    screen
      .getByText('Two assignments fit')
      .compareDocumentPosition(screen.getByText('Review assignment')) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  rerender(
    <AssignmentEvidence
      row={{ ...row, planned_choice: { category: 'audio_conflicts', action: 'fix' } }}
      album={album}
    />,
  );
  expect(screen.queryByText('Why this needs review')).toBeNull();
});

it('explains uncertainty and competing identities before proposed edits, decisions and playback', () => {
  render(
    <ReconciliationEvidence
      finding={{
        ...finding,
        details: {
          ...finding.details,
          ambiguity_reasons: ['Two recording identities have equal fingerprint scores'],
          acoustid: {
            status: 'ok',
            best_score: 1,
            recordings: [{ title: 'Other song', artist: 'Artist', score: 1 }],
          },
          changes: { title: { old: 'Old', new: 'Song' } },
        },
      }}
      controls={<button>Accept assignment</button>}
      audio={<button>Play local audio</button>}
      prominent
    />,
  );
  const reason = screen.getByText('Two recording identities have equal fingerprint scores');
  for (const target of [
    screen.getByText('Proposed changes'),
    screen.getByText('Accept assignment'),
    screen.getByText('Play local audio'),
  ]) {
    expect(reason.compareDocumentPosition(target) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  }
  expect(
    screen
      .getByText(/No AcoustID candidate names this release track:/)
      .compareDocumentPosition(screen.getByText('Accept assignment')) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

it('shows duplicate copy evidence and keeper choices before the generic fallback action', () => {
  const keep = vi.fn();
  render(
    <ReconciliationEvidence
      finding={{
        ...finding,
        details: {
          duplicate_copies: [
            { track_id: 'copy-2', file_path: '/music/copy.flac', quality: { format: 'FLAC' } },
            { track_id: 'copy-3', file_path: '/music/other.flac', quality: { format: 'FLAC' } },
          ],
        },
      }}
      onKeepCopy={keep}
      controls={<button>Keep current values</button>}
    />,
  );
  const choice = screen.getAllByRole('button', { name: 'Plan to keep this copy' })[0];
  expect(
    choice.compareDocumentPosition(screen.getByText('Keep current values')) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  fireEvent.click(choice);
  expect(keep).toHaveBeenCalledWith('copy-2');
});

it('keeps the selected recording within five groups and expands the remaining evidence', async () => {
  const recordings = Array.from({ length: 8 }, (_, index) => ({
    title: `Recording ${index}`,
    artist: 'Artist',
    score: 0.99,
    mbid: `id-${index}`,
  }));
  render(
    <ReconciliationEvidence
      finding={
        {
          id: 1,
          file_path: '/music/song.flac',
          details: {
            expected_title: 'Recording 7',
            recording_mbid: 'id-7',
            acoustid: { status: 'ok', recordings },
            ambiguity_reasons: ['Another local file fits this release track'],
            competitors: [
              {
                kind: 'local_file',
                file_path: '/music/15 - Song [DLX].flac',
                assigned_title: 'Song [DLX]',
              },
            ],
          },
        } as unknown as ReviewFinding
      }
      prominent
    />,
  );
  const identity = screen.getByRole('region', { name: 'Audio identity' });
  expect(within(identity).getAllByRole('link', { name: /recording/i })).toHaveLength(5);
  expect(within(identity).getByRole('link', { name: /Selected recording/ })).toHaveAttribute(
    'href',
    expect.stringContaining('id-7'),
  );
  expect(within(identity).getByText('15 - Song [DLX].flac')).toBeVisible();
  fireEvent.click(within(identity).getByRole('button', { name: 'Show 3 more recordings' }));
  expect(within(identity).getAllByRole('link', { name: /recording/i })).toHaveLength(8);
  fireEvent.click(within(identity).getByRole('button', { name: 'Show fewer recordings' }));
  expect(within(identity).getAllByRole('link', { name: /recording/i })).toHaveLength(5);
  fireEvent.click(screen.getByRole('button', { name: 'About AcoustID audio evidence' }));
  expect(
    await screen.findByRole('dialog', { name: 'About AcoustID audio evidence' }),
  ).toHaveTextContent('equal scores do not rank them');
});

it('names release artists and distinguishes the current edition from the planned one', () => {
  render(
    <ReleaseComparison
      candidates={[
        {
          source: 'musicbrainz',
          release_id: 'cd',
          release_title: 'Album',
          release_artist: 'Artist & Guest',
          format: 'CD',
        },
        {
          source: 'musicbrainz',
          release_id: 'digital',
          release_title: 'Album',
          release_artist: 'Artist & Guest',
          format: 'Digital Media',
        },
      ]}
      selected={1}
      onSelect={vi.fn()}
      currentRelease={{ source: 'musicbrainz', release_id: 'digital' }}
      libraryRelease={{ source: 'musicbrainz', release_id: 'cd' }}
    />,
  );
  expect(screen.getAllByText('Artist & Guest')).toHaveLength(3);
  expect(screen.getByLabelText('Current and planned editions')).toHaveTextContent(
    'Current library edition',
  );
  expect(screen.getByLabelText('Current and planned editions')).toHaveTextContent(
    'Selected for this plan',
  );
});

it('marks a large length mismatch red even when the fingerprint identifies the selected recording', () => {
  render(
    <ReconciliationEvidence
      prominent
      finding={
        {
          id: 1,
          finding_type: 'reconciliation_unresolved',
          file_path: '/music/Lotus.flac',
          details: {
            expected_title: 'Lotus',
            file_duration_ms: 300910,
            expected_duration_ms: 104850,
            recording_mbid: 'lotus',
            acoustid: {
              status: 'ok',
              recordings: [{ mbid: 'lotus', title: 'Lotus', artist: 'YIN YIN', score: 0.97 }],
            },
          },
        } as unknown as ReviewFinding
      }
    />,
  );
  expect(screen.getByRole('region', { name: 'Audio identity' })).toHaveAttribute(
    'data-tone',
    'danger',
  );
  expect(screen.getByText('Length differs by 196.1 seconds')).toBeVisible();
  expect(screen.getByText(/incorrect catalogue length/)).toBeVisible();
});

it('requires an explicit audio-copy choice for a video-only MusicBrainz release', () => {
  const onPin = vi.fn();
  render(
    <ReleaseComparison
      candidates={[
        {
          source: 'musicbrainz',
          release_id: 'video',
          release_title: 'Present Tense',
          audio_copy_of_video: true,
        },
      ]}
      selected={0}
      onSelect={vi.fn()}
      onPin={onPin}
    />,
  );
  expect(screen.getByText(/MusicBrainz lists this as a video recording/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Use for this audio copy' }));
  expect(onPin).toHaveBeenCalledWith(0);
});

it('shows the exact library-only change instead of an empty approval table', () => {
  render(
    <ReconciliationEvidence
      finding={
        {
          id: 1,
          details: { changes: { library_album_title: { old: 'Album (pressing)', new: 'Album' } } },
        } as unknown as ReviewFinding
      }
    />,
  );
  const table = screen.getByRole('table');
  expect(within(table).getByText('Album (pressing)')).toBeVisible();
  expect(within(table).getByText('Album')).toBeVisible();
  expect(within(table).getByText(/Album title.*library/)).toBeVisible();
});
