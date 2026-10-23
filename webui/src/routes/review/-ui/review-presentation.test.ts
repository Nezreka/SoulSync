import { expect, it } from 'vitest';

import {
  changeFieldLabel,
  displayChanges,
  evidenceLabel,
  statusLabel,
} from './review-presentation';

it('distinguishes audio, metadata, user decisions and unknown older evidence', () => {
  expect(evidenceLabel('audio')).toBe('Audio-supported match');
  expect(evidenceLabel('metadata')).toBe('Matched from metadata');
  expect(evidenceLabel('user')).toBe('User-confirmed assignment');
  expect(evidenceLabel(undefined)).toBe('Assignment settled');
  expect(statusLabel('confirmed', { status: 'confirmed', companion_of: '/source.flac' })).toBe(
    'Intentional companion',
  );
});

it('merges identical file and library edits without losing their scope', () => {
  const changes = displayChanges({
    title: { old: 'Old', new: 'New' },
    library_title: { old: 'Old', new: 'New' },
    library_recording_mbid: { old: '', new: 'recording-id' },
  });
  expect(changes).toHaveLength(2);
  expect(changeFieldLabel(changes[0].field, changes[0].locations)).toBe(
    'Track title · file and library',
  );
  expect(changeFieldLabel(changes[1].field, changes[1].locations)).toBe('Recording ID · library');
});

it('keeps differing file and library values visible and formats collection values', () => {
  const changes = displayChanges({
    title: { old: 'Embedded title', new: 'New' },
    library_title: { old: 'Library title', new: 'New' },
    genres: { old: ['Rock', 'Pop'], new: ['Rock'] },
  });
  expect(changes).toHaveLength(3);
  expect(changes.map((change) => change.old)).toEqual([
    'Embedded title',
    'Library title',
    'Rock, Pop',
  ]);
});
