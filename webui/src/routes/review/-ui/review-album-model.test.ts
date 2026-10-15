import { expect, it } from 'vitest';

import type { ReviewCase } from '../-review.api';

import { buildAlbumModel } from './review-album-model';

it('retains first-match selection when one decision owns several assignment rows', () => {
  const rows = [
    { status: 'extra', finding_id: 1, file_path: '/first', current_title: 'First' },
    { status: 'missing', finding_id: 1, slot_title: 'Missing counterpart' },
    { status: 'confirmed', file_path: '/confirmed' },
  ];
  const model = buildAlbumModel({
    items: [],
    album_plan: { assignments: rows },
  } as unknown as ReviewCase);
  expect(model.assignmentsByFinding.get(1)).toBe(rows[0]);
  expect(model.assignmentsByPath.get('/confirmed')).toBe(rows[2]);
  expect(model.missingAssignments).toEqual([rows[1]]);
  expect(model.settledAssignments).toEqual([rows[2]]);
  expect(rows.map((row) => row.status)).toEqual(['extra', 'missing', 'confirmed']);
});

it('does not turn a wanted download into a new review decision', () => {
  const waiting = {
    id: 'waiting',
    category: 'pending_fulfillment',
    risk: 'blocked',
    finding_ids: [9],
  };
  const model = buildAlbumModel({
    items: [],
    decisions: [waiting],
    album_plan: {
      assignments: [{ status: 'missing', finding_id: 9, fulfillment_state: 'wishlisted' }],
    },
  } as unknown as ReviewCase);
  expect(model.pendingDecisions).toEqual([waiting]);
  expect(model.reviewDecisions).toEqual([]);
  expect(model.wishlistedCount).toBe(1);
  expect(model.decisionMembers.get('waiting')?.has(9)).toBe(true);
});
