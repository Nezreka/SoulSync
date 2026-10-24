import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { AlbumAssignment, ReviewFinding } from '../-review.api';

import { ApplyPlanPreview } from './apply-plan-preview';
import { FileDecisions } from './file-decisions';
import { NumberingChoice } from './numbering-choice';
import { PairingEvidence } from './pairing-evidence';
import { ReconciliationEvidence, ReleaseComparison } from './reconciliation-evidence';

afterEach(cleanup);

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

it('opens numbering choices before changing the plan', () => {
  const onChoose = vi.fn();
  render(
    <NumberingChoice
      scheme="Numbered by release disc"
      busy={false}
      onChoose={onChoose}
      assignments={[
        {
          file_path: '/song.flac',
          current_title: 'Song',
          current_disc_number: 1,
          current_track_number: 12,
          slot_disc_number: 2,
          slot_track_number: 1,
        } as AlbumAssignment,
      ]}
    />,
  );
  expect(screen.queryByRole('button', { name: 'Keep current numbering' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Change…' }));
  expect(onChoose).not.toHaveBeenCalled();
  expect(screen.getByRole('dialog')).toHaveTextContent('2.1');
  fireEvent.click(screen.getByRole('button', { name: 'Keep current numbering' }));
  expect(onChoose).toHaveBeenCalledWith('dismiss');
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

it('groups overlapping changes while retaining one action total and exact values', () => {
  render(
    <ApplyPlanPreview
      expanded
      destinations={['Artist/Album/Disc 1', 'Artist/Album/Disc 2']}
      preview={{
        signature: 'snapshot',
        action_count: 1,
        file_actions: 0,
        albums: [
          {
            album: 'Album',
            case_id: 'case',
            actions: [
              {
                id: 1,
                title: 'Song',
                file_path: '/music/song.flac',
                description: 'Update Song',
                changes: [
                  { field: 'track_number', old: 12, new: 1, kind: 'numbering' },
                  { field: 'title', old: 'Old title', new: 'Song', kind: 'corrections' },
                ],
              },
            ],
          },
        ],
      }}
    />,
  );
  expect(screen.getByText('1 planned action')).toBeVisible();
  expect(screen.getByText('Disc and track numbering')).toBeVisible();
  fireEvent.click(screen.getByText('Metadata corrections'));
  expect(screen.getByText('Old title')).toBeVisible();
  expect(screen.getByLabelText('Destination folders')).toHaveTextContent('Artist/Album');
});

it('keeps long file-decision lists compact with track review owning the full paths', () => {
  const items = Array.from({ length: 7 }, (_, id) => ({
    id,
    file_decision: {
      title: `Song ${id}`,
      keep: [`/music/album/${id}.flac`],
      quarantine: [`/music/album/${id}.mp3`],
      request: '',
    },
  }));
  render(<FileDecisions items={items} />);
  expect(screen.getAllByText('FLAC')).toHaveLength(5);
  fireEvent.click(screen.getByRole('button', { name: 'Show 2 more file decisions' }));
  expect(screen.getAllByText('FLAC')).toHaveLength(7);
  expect(screen.queryByText('File paths')).toBeNull();
  expect(screen.queryByText('/music/album/0.mp3')).toBeNull();
});

it('shows matcher checks with their actual decision explanation', () => {
  render(
    <PairingEvidence
      checks={[
        {
          check: 'Artist credit',
          local: 'Noah Kahan',
          release: 'Noah Kahan & Hozier',
          result: 'conflicts',
        },
        {
          check: 'Album assignment',
          local: '3 files',
          release: '3 slots',
          result: 'resolved',
          detail: 'Other files fit their own slots',
        },
        { check: 'Decision', result: 'conflicts', detail: 'Listen for a track boundary error' },
      ]}
    />,
  );
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.getAllByText('Differs')).toHaveLength(2);
  fireEvent.click(screen.getByText('Artist credit'));
  expect(screen.getByText('Noah Kahan & Hozier')).toBeVisible();
  fireEvent.click(screen.getByText('Album assignment'));
  fireEvent.click(screen.getByText('Decision'));
  expect(screen.getByText('Other files fit their own slots')).toBeVisible();
  expect(screen.getByText('Listen for a track boundary error')).toBeVisible();
});

it('separates exact track requests from quarantine and labels both ends of a folder move', () => {
  render(
    <ApplyPlanPreview
      expanded
      destinations={[{ current: 'Artist/Old pressing', planned: 'Artist/Approved album' }]}
      preview={{
        signature: 'preview',
        file_actions: 1,
        action_count: 1,
        albums: [
          {
            album: 'Approved album',
            case_id: 'a',
            actions: [
              {
                id: 1,
                description: 'Replace Song',
                title: 'Song',
                requests: [{ title: 'Song (live)', release: 'Approved edition', state: 'planned' }],
                file_decision: {
                  title: 'Song',
                  keep: [],
                  quarantine: ['/library/song.mp3'],
                  request: 'Song (live)',
                },
              },
            ],
          },
        ],
      }}
    />,
  );
  const requests = screen.getByLabelText('Request tracks');
  expect(requests).toHaveTextContent('Song (live)');
  expect(requests).toHaveTextContent('Approved edition');
  expect(requests).toHaveTextContent('Add to wishlist');
  expect(screen.getByLabelText('File decisions included')).not.toHaveTextContent('Song (live)');
  expect(screen.getByLabelText('Destination folders')).toHaveTextContent('Current folder');
  expect(screen.getByLabelText('Destination folders')).toHaveTextContent('Planned folder');
  expect(screen.getByLabelText('Destination folders')).toHaveTextContent('Artist/Approved album');
});
