import { expect, it } from 'vitest';

import type { ReviewCase, ReviewFinding } from '../-review.api';

import { buildAlbumModel, groupReadyChanges } from './review-album-model';

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

it('keeps a cancelled request as a missing-track decision without counting it as waiting', () => {
  const decision = {
    id: 'missing',
    category: 'album_completeness',
    risk: 'manual',
    finding_ids: [9],
  };
  const row = { status: 'missing', finding_id: 9, fulfillment_state: 'cancelled' };
  const model = buildAlbumModel({
    items: [],
    decisions: [decision],
    album_plan: { assignments: [row] },
  } as unknown as ReviewCase);
  expect(model.wishlistedCount).toBe(0);
  expect(model.reviewDecisions).toEqual([decision]);
  expect(model.missingAssignments).toEqual([row]);
});

it('counts a file and library edit once and keeps undecided changes out of ready summaries', () => {
  const findings = [
    {
      id: 1,
      finding_type: 'reconciliation_metadata',
      review_tier: 'ready',
      details: {
        changes: {
          title: { old: 'Song', new: 'Song (live)' },
          library_title: { old: 'Song', new: 'Song (live)' },
        },
      },
    },
    {
      id: 2,
      finding_type: 'reconciliation_metadata',
      review_tier: 'manual',
      details: { changes: { title: { old: 'Other', new: 'Other (demo)' } } },
    },
  ] as unknown as ReviewFinding[];
  expect(groupReadyChanges(findings)).toEqual([
    { key: 'title', label: 'Track titles', count: 1, valueSummary: 'Song → Song (live)' },
  ]);
  expect(groupReadyChanges(findings, false)).toEqual([
    { key: 'title', label: 'Track titles', count: 2, valueSummary: 'Update 2 tracks' },
  ]);
});
