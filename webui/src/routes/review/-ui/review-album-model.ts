import type { AlbumAssignment, ReviewCase, ReviewDecision } from '../-review.api';

import { assignmentKey } from './review-presentation';

export const decisionOrder: Record<string, number> = {
  release_conflicts: 0,
  album_completeness: 1,
  pending_fulfillment: 2,
  numbering: 2,
  audio_conflicts: 3,
  title_issues: 4,
  duplicates: 5,
  extra_files: 6,
  title_conventions: 7,
  metadata: 8,
};

export function buildAlbumModel(reviewCase: ReviewCase | null) {
  const decisions = [...(reviewCase?.decisions || reviewCase?.plan?.decisions || [])].sort(
    (left, right) => (decisionOrder[left.category] ?? 99) - (decisionOrder[right.category] ?? 99),
  );
  const assignments = reviewCase?.album_plan?.assignments || [];
  const pendingAssignments = assignments.filter(
    (row) => !['confirmed', 'not_owned', 'settled'].includes(row.status),
  );
  const duplicateExtraFindings = new Set(
    pendingAssignments
      .filter((row) => row.status === 'duplicate' && row.finding_id != null)
      .map((row) => row.finding_id),
  );
  const versionConflictAssignments = pendingAssignments
    .filter(
      (row) =>
        row.relationship === 'version_conflict' &&
        row.status !== 'change' &&
        row.status !== 'missing',
    )
    .sort((left, right) => {
      const relationship = (left.relationship_key || '').localeCompare(
        right.relationship_key || '',
      );
      if (relationship) return relationship;
      return left.status === 'extra' ? -1 : right.status === 'extra' ? 1 : 0;
    });
  const attentionAssignments = [
    ...versionConflictAssignments,
    ...pendingAssignments
      .filter(
        (row) =>
          row.relationship !== 'version_conflict' &&
          row.status !== 'change' &&
          row.status !== 'missing' &&
          !(row.status === 'uncertain' && duplicateExtraFindings.has(row.finding_id)),
      )
      .sort((left, right) => {
        const order: Record<string, number> = {
          uncertain: 0,
          extra: 1,
          duplicate: 1,
          missing: 2,
          change: 3,
          not_owned: 4,
        };
        return (order[left.status] ?? 9) - (order[right.status] ?? 9);
      }),
  ];
  const changeAssignments = pendingAssignments.filter((row) => row.status === 'change');
  const missingAssignments = pendingAssignments.filter(
    (row) => row.status === 'missing' && row.relationship !== 'version_conflict',
  );
  const pairedConflictCount = versionConflictAssignments.filter(
    (row) => row.status === 'extra',
  ).length;
  const settledAssignments = assignments.filter((row) =>
    ['confirmed', 'not_owned', 'settled'].includes(row.status),
  );
  const reviewDecisions = decisions.filter(
    (decision) => decision.risk !== 'ready' && decision.category !== 'pending_fulfillment',
  );
  const pendingDecisions = decisions.filter(
    (decision) => decision.category === 'pending_fulfillment',
  );
  const wishlistedCount = assignments.filter(
    (row) => row.fulfillment_state === 'wishlisted',
  ).length;
  const releaseChoicePending = reviewDecisions.some(
    (decision) => decision.category === 'release_conflicts',
  );
  const releaseHasMultipleDiscs = assignments.some((row) => Number(row.slot_disc_number || 1) > 1);
  const hasIntentDecision = reviewDecisions.some(
    (decision) => decision.category === 'album_completeness',
  );
  const readyDecisions = decisions.filter((decision) => decision.risk === 'ready');
  const reviewMetadataCount =
    reviewCase?.items.filter(
      (item) => item.finding_type === 'reconciliation_metadata' && item.review_tier !== 'ready',
    ).length || 0;
  const readyFindingIds = new Set(
    (reviewCase?.items || []).filter((item) => item.review_tier === 'ready').map((item) => item.id),
  );
  const readyIncludedInGroup = reviewDecisions.some(
    (decision) =>
      ['metadata', 'numbering', 'title_conventions'].includes(decision.category) &&
      decision.finding_ids.some((id) => readyFindingIds.has(id)),
  );
  const releaseOrderedAssignments = assignments
    .filter((row) => !(row.status === 'missing' && row.relationship === 'version_conflict'))
    .sort(
      (left, right) =>
        Number(left.slot_disc_number || left.current_disc_number || 1) -
          Number(right.slot_disc_number || right.current_disc_number || 1) ||
        Number(left.slot_track_number || left.current_track_number || 0) -
          Number(right.slot_track_number || right.current_track_number || 0) ||
        (left.file || '').localeCompare(right.file || ''),
    );

  const assignmentsByFinding = new Map<AlbumAssignment['finding_id'] | null, AlbumAssignment>();
  const assignmentsByKey = new Map<string, AlbumAssignment>();
  const assignmentsByPath = new Map<string, AlbumAssignment>();
  for (const row of assignments) {
    if (!assignmentsByFinding.has(row.finding_id)) assignmentsByFinding.set(row.finding_id, row);
    if (!assignmentsByKey.has(assignmentKey(row))) assignmentsByKey.set(assignmentKey(row), row);
    if (row.file_path && !assignmentsByPath.has(row.file_path))
      assignmentsByPath.set(row.file_path, row);
  }
  const decisionsByFinding = new Map<number, ReviewDecision>();
  const decisionMembers = new Map<string, Set<number>>();
  for (const decision of decisions) {
    decisionMembers.set(decision.id, new Set(decision.finding_ids));
    for (const id of decision.finding_ids) {
      if (!decisionsByFinding.has(id)) decisionsByFinding.set(id, decision);
    }
  }
  return {
    decisions,
    assignments,
    attentionAssignments,
    changeAssignments,
    missingAssignments,
    pairedConflictCount,
    settledAssignments,
    reviewDecisions,
    pendingDecisions,
    wishlistedCount,
    releaseChoicePending,
    releaseHasMultipleDiscs,
    hasIntentDecision,
    readyDecisions,
    reviewMetadataCount,
    readyIncludedInGroup,
    assignmentsByFinding,
    assignmentsByKey,
    assignmentsByPath,
    decisionsByFinding,
    decisionMembers,
    releaseOrderedAssignments,
  };
}
