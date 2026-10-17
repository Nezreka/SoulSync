import { expect, it } from 'vitest';

import type { ReviewCase } from '../-review.api';

import { buildAlbumModel } from './review-album-model';
it('sorts paired conflicts using their release position and puts unassigned files last', () => {
  const assignments = [
    {
      status: 'extra',
      file: 'conflict',
      current_disc_number: 1,
      current_track_number: 16,
      counterpart_disc_number: 2,
      counterpart_track_number: 3,
      relationship: 'version_conflict',
    },
    {
      status: 'change',
      file: 'fourteen',
      slot_title: 'Song',
      slot_disc_number: 2,
      slot_track_number: 1,
    },
    { status: 'extra', file: 'unassigned', current_track_number: 1 },
    {
      status: 'change',
      file: 'fifteen',
      slot_title: 'Song',
      slot_disc_number: 2,
      slot_track_number: 2,
    },
  ];
  const model = buildAlbumModel({
    items: [],
    album_plan: { assignments },
  } as unknown as ReviewCase);
  expect(model.releaseOrderedAssignments.map((row) => row.file)).toEqual([
    'fourteen',
    'fifteen',
    'conflict',
    'unassigned',
  ]);
});
