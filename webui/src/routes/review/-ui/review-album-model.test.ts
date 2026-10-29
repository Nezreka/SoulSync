import { expect, it } from 'vitest';

import type { AlbumAssignment, ReviewCase, ReviewFinding } from '../-review.api';

import { buildAlbumModel, groupReadyChanges } from './review-album-model';
import { metadataNeedsReview, statusLabel } from './review-presentation';

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

it('groups metadata decisions by action state while preserving matching confidence and previews', () => {
  const base = {
    status: 'change',
    confidence: 'clear',
    assignment_evidence: 'audio',
    finding_id: 1,
    review_tier: 'manual',
  };
  const rows = [
    base,
    { ...base, action_state: 'manual' },
    { ...base, action_state: 'blocked' },
    { ...base, action_state: 'ready', planned_choice: { action: 'fix', category: 'metadata' } },
    { ...base, action_state: 'ready', planned_choice: { action: 'dismiss', category: 'metadata' } },
    { ...base, action_state: 'preview' },
    { ...base, numbering_preview: true },
    { ...base, action_state: 'ready' },
  ] as AlbumAssignment[];
  const model = buildAlbumModel({
    items: [],
    album_plan: { assignments: rows },
  } as unknown as ReviewCase);
  expect(model.attentionAssignments).toEqual(rows.slice(0, 3));
  expect(model.changeAssignments).toEqual(rows.slice(3));
  expect(model.metadataDecisionCount).toBe(3);
  expect(model.proposedChangeCount).toBe(5);
  expect(model.attentionAssignments.every(metadataNeedsReview)).toBe(true);
  expect(rows[0].confidence).toBe('clear');
  expect(statusLabel(rows[0].status, rows[0])).toBe('Review metadata');
  expect(statusLabel(rows[3].status, rows[3])).toBe('Change planned');
  expect(statusLabel(rows[4].status, rows[4])).toBe('Keep values planned');
  expect(statusLabel(rows[6].status, rows[6])).toBe('Numbering proposal');
});

it('keeps remaining metadata in review after a numbering choice is saved', () => {
  const row = {
    status: 'change',
    confidence: 'clear',
    action_state: 'manual',
    planned_choice: { category: 'numbering', action: 'dismiss' },
  } as AlbumAssignment;
  const model = buildAlbumModel({
    items: [],
    album_plan: { assignments: [row] },
  } as unknown as ReviewCase);
  expect(model.attentionAssignments).toEqual([row]);
  expect(model.changeAssignments).toEqual([]);
  expect(model.metadataDecisionCount).toBe(1);
  expect(statusLabel(row.status, row)).toBe('Review metadata · numbering kept');
  expect(metadataNeedsReview({ ...row, action_state: 'ready' })).toBe(false);
  expect(statusLabel(row.status, { ...row, action_state: 'ready' })).toBe('Keep numbering planned');
});

function idFinding(id: number, old: unknown, next: unknown, libraryOld = old): ReviewFinding {
  return {
    id,
    finding_type: 'reconciliation_metadata',
    review_tier: 'ready',
    details: {
      changes: {
        musicbrainz_recording_id: { old, new: next },
        library_recording_mbid: { old: libraryOld, new: next },
      },
    },
  } as unknown as ReviewFinding;
}

it.each([
  ['', 'recording', 'Add'],
  ['old', 'recording', 'Replace'],
  ['old', '', 'Remove'],
  [null, 'recording', 'Add'],
])('describes ID operations and counts file/library copies once: %s → %s', (old, next, verb) => {
  const [group] = groupReadyChanges([idFinding(1, old, next)]);
  expect(group.count).toBe(1);
  expect(group.valueSummary).toBe(`${verb} MusicBrainz recording IDs on 1 track`);
});

it('counts mixed ID operations per track, including different file/library effects', () => {
  const [group] = groupReadyChanges([
    idFinding(1, '', 'new'),
    idFinding(2, 'old', 'new'),
    idFinding(3, 'old', ''),
    idFinding(4, '', 'new', 'old'),
    idFinding(5, 'unchanged', 'unchanged'),
  ]);
  expect(group.count).toBe(4);
  expect(group.valueSummary).toBe(
    'Add on 1 track; Replace on 1 track; Remove on 1 track; Mixed edits on 1 track',
  );
});

it('retains whitespace cleanup as a real proposed edit', () => {
  const [group] = groupReadyChanges([
    {
      id: 1,
      finding_type: 'reconciliation_metadata',
      review_tier: 'ready',
      details: { changes: { title: { old: ' Home ', new: 'Home' } } },
    } as unknown as ReviewFinding,
  ]);
  expect(group.count).toBe(1);
  expect(group.valueSummary).toBe(' Home  → Home');
});

it('explains album grouping without exposing database row IDs in its summary', () => {
  const [group] = groupReadyChanges([
    {
      id: 1,
      finding_type: 'reconciliation_metadata',
      review_tier: 'ready',
      details: {
        changes: { library_album_assignment: { old: 'separate-row-id', new: 'target-row-id' } },
      },
    } as unknown as ReviewFinding,
  ]);
  expect(group.key).toBe('library_album_assignment');
  expect(group.valueSummary).toContain('reviewed album');
  expect(group.valueSummary).not.toMatch(/separate-row-id|target-row-id/);
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

it('labels disc-total-only edits as numbering without claiming track numbers will change', () => {
  const item = {
    ...idFinding(1, null, 'recording'),
    details: { changes: { total_discs: { old: 1, new: 2 } } },
  };
  const groups = groupReadyChanges([item]);
  expect(groups).toHaveLength(1);
  expect(groups[0]).toMatchObject({ key: 'numbering', label: 'Total discs', count: 1 });
});
