import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewFinding } from '../-review.api';
import type { FindingTypeInfo } from '../../tools/-tools.groups';

import { ReconciliationEvidence } from './reconciliation-evidence';
import { SelectedActionDetail } from './selected-action-detail';

afterEach(cleanup);
const finding = {
  id: 7,
  finding_type: 'reconciliation_metadata',
  title: 'Update metadata: Song',
  file_path: '/music/song.flac',
  review_tier: 'ready',
  details: {
    current_title: 'Song',
    changes: {
      title: { old: 'song', new: 'Song' },
      musicbrainz_release_id: { old: 'old-id', new: 'new-id' },
    },
    assignment_options: [{ key: 'id:other:1:2', title: 'Other', track_number: 2, disc_number: 1 }],
  },
} as unknown as ReviewFinding;

it('keeps accept and keep choices visible while allowing alternative reassignment', () => {
  const onAct = vi.fn();
  render(
    <SelectedActionDetail
      finding={finding}
      itemIndex={0}
      itemCount={1}
      busy={false}
      compact
      onAct={onAct}
      onSelect={vi.fn()}
      typeInfo={{ fixable: true } as FindingTypeInfo}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Use proposed change' }));
  expect(onAct).toHaveBeenCalledWith('fix');
  fireEvent.click(screen.getByRole('button', { name: 'Keep current values' }));
  expect(onAct).toHaveBeenCalledWith('dismiss');
  expect(screen.getByText('Change track assignment').closest('details')!.open).toBe(false);
  fireEvent.click(screen.getByText('Change track assignment'));
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'id:other:1:2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save plan assignment' }));
  expect(onAct).toHaveBeenCalledWith('fix', 'assign:id:other:1:2');
});

it('shows identity replacement alongside readable edits before approval', () => {
  render(<ReconciliationEvidence finding={finding} />);
  expect(screen.getAllByText('Song').length).toBeGreaterThan(0);
  expect(screen.getByText('Replace MusicBrainz release ID · file tag')).toBeVisible();
  expect(screen.getByText('old-id')).toBeVisible();
  expect(screen.getByText('new-id')).toBeVisible();
  expect(screen.getByText('old-id').closest('details')).toBeNull();
});

it('shows short identifier additions and removals beside a filename edit', () => {
  render(
    <ReconciliationEvidence
      finding={{
        ...finding,
        details: {
          changes: {
            filename: { old: 'old.flac', new: 'new.flac' },
            musicbrainz_recording_id: { old: 'remove-id', new: '' },
            library_recording_mbid: { old: 'remove-id', new: '' },
            musicbrainz_release_id: { old: '', new: 'added-id' },
          },
        },
      }}
    />,
  );
  expect(screen.getByText('Remove Recording ID · file and library')).toBeVisible();
  expect(screen.getAllByText('remove-id')).toHaveLength(1);
  expect(screen.getByText('remove-id').closest('details')).toBeNull();
  expect(screen.getByText('added-id')).toBeVisible();
  expect(screen.getByText('added-id').closest('details')).toBeNull();
});

it('shows identifier-only proposals immediately', () => {
  render(
    <ReconciliationEvidence
      finding={{
        ...finding,
        details: { changes: { musicbrainz_recording_id: { old: 'old-id', new: 'new-id' } } },
      }}
    />,
  );
  expect(screen.getByText('new-id')).toBeTruthy();
  expect(screen.queryByText('1 identifier change')).toBeNull();
});

it('explains an unsupported recording ID without implying a release change', () => {
  render(
    <ReconciliationEvidence
      finding={{
        ...finding,
        details: {
          metadata_review_reason: 'Confirm removing the incorrect recording ID',
          recording_id_conflict: { title: 'Different song', artist: 'Different artist' },
          changes: { musicbrainz_recording_id: { old: 'incorrect-id', new: '' } },
        },
      }}
    />,
  );
  expect(screen.getByRole('heading', { name: 'Recording ID needs review' })).toBeTruthy();
  expect(screen.getByText('Different song')).toBeTruthy();
  expect(screen.getByText('incorrect-id')).toBeTruthy();
  expect(screen.queryByText('Album metadata differs between files')).toBeNull();
});

it('recommends one duplicate keeper while leaving both copies available for a decision', () => {
  const onKeepCopy = vi.fn();
  render(
    <ReconciliationEvidence
      onKeepCopy={onKeepCopy}
      finding={{
        ...finding,
        details: {
          recommended_keeper: 'lossless',
          keeper_reason: 'Highest quality copy',
          duplicate_copies: [
            { track_id: 'lossless', file_path: '/music/song.flac' },
            { track_id: 'lossy', file_path: '/music/song.mp3' },
          ],
        },
      }}
    />,
  );
  expect(screen.getAllByText('Recommended copy')).toHaveLength(1);
  expect(screen.getByText('Recommended copy').closest('article')!.textContent).toContain(
    'song.flac',
  );
  fireEvent.click(screen.getAllByRole('button', { name: 'Plan to keep this copy' })[1]);
  expect(onKeepCopy).toHaveBeenCalledWith('lossy');
});

const repeatedRecording = {
  ...finding,
  finding_type: 'reconciliation_unresolved',
  details: {
    replacement_track: { source_track_id: 'recording', disc_number: 2, track_number: 1 },
    assignment_options: [
      { key: 'id:recording:1:1', title: 'First occurrence', disc_number: 1, track_number: 1 },
      { key: 'id:recording:2:1', title: 'Second occurrence', disc_number: 2, track_number: 1 },
    ],
  },
} as ReviewFinding;

function detail(current: ReviewFinding, onAct = vi.fn(), preview = false) {
  return (
    <SelectedActionDetail
      finding={current}
      itemIndex={0}
      itemCount={1}
      busy={false}
      onAct={onAct}
      onSelect={vi.fn()}
      compact
      numberingPreview={preview}
      typeInfo={{ fixable: true } as FindingTypeInfo}
    />
  );
}

it('uses the exact release position when a recording appears more than once', () => {
  const onAct = vi.fn();
  render(detail(repeatedRecording, onAct));
  fireEvent.click(
    screen.getByRole('button', { name: 'Use assignment in plan: Second occurrence' }),
  );
  expect(onAct).toHaveBeenCalledWith('fix', 'assign:id:recording:2:1');
  expect(screen.queryByRole('button', { name: /Use assignment in plan: First/ })).toBeNull();
});

it.each([
  { audio_conflict: 'different_recording' },
  { version_conflict: true },
  { fulfillment_state: 'wishlisted' },
])('does not recommend assigning conflicting or waiting audio: %j', (details) => {
  render(detail({ ...repeatedRecording, details: { ...repeatedRecording.details, ...details } }));
  expect(screen.queryByRole('button', { name: /Use assignment in plan:/ })).toBeNull();
});

it('exposes no assignment mutation from a numbering preview or a missing file', () => {
  const { rerender } = render(detail(repeatedRecording, vi.fn(), true));
  expect(screen.queryByText('Change track assignment')).toBeNull();
  expect(screen.queryByRole('button', { name: /Use assignment in plan:/ })).toBeNull();
  rerender(detail({ ...repeatedRecording, file_path: null }));
  expect(screen.queryByText('Change track assignment')).toBeNull();
  expect(screen.queryByRole('button', { name: /Use assignment in plan:/ })).toBeNull();
});

it('clears a draft assignment when the same finding receives another release or options', () => {
  const onAct = vi.fn();
  const { rerender } = render(detail(finding, onAct));
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'id:other:1:2' } });
  expect(screen.getByRole('button', { name: 'Save plan assignment' })).not.toBeDisabled();
  rerender(
    detail({ ...finding, details: { ...finding.details, release_id: 'another-release' } }, onAct),
  );
  expect(screen.getByRole('combobox')).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Save plan assignment' })).toBeDisabled();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'id:other:1:2' } });
  rerender(
    detail(
      {
        ...finding,
        details: {
          ...finding.details,
          assignment_options: [{ key: 'id:new:1:1', title: 'New slot' }],
        },
      },
      onAct,
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save plan assignment' }));
  expect(onAct).not.toHaveBeenCalled();
  expect(screen.getByRole('combobox')).toHaveValue('');
});
