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

it('keeps readable changes visible and exposes exact identifier edits on demand', () => {
  render(<ReconciliationEvidence finding={finding} />);
  expect(screen.getAllByText('Song').length).toBeGreaterThan(0);
  const disclosure = screen.getByText('1 identifier change').closest('details')!;
  expect(disclosure.open).toBe(false);
  fireEvent.click(screen.getByText('1 identifier change'));
  expect(disclosure.open).toBe(true);
  expect(screen.getByText('old-id')).toBeTruthy();
  expect(screen.getByText('new-id')).toBeTruthy();
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
