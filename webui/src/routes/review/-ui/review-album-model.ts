import type { AlbumAssignment, ReviewCase, ReviewDecision, ReviewFinding } from '../-review.api';

import { assignmentKey, changeFieldLabel } from './review-presentation';

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
        !row.planned_choice &&
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
          !row.planned_choice &&
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
  const changeAssignments = pendingAssignments.filter(
    (row) => row.status === 'change' || Boolean(row.planned_choice),
  );
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
  const wishlistedCount = assignments.filter((row) =>
    ['wishlisted', 'downloading', 'downloaded'].includes(row.fulfillment_state || ''),
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
        Number(
          left.counterpart_disc_number || left.slot_disc_number || (left.slot_title ? 1 : 9999),
        ) -
          Number(
            right.counterpart_disc_number ||
              right.slot_disc_number ||
              (right.slot_title ? 1 : 9999),
          ) ||
        Number(
          left.counterpart_track_number || left.slot_track_number || left.current_track_number || 0,
        ) -
          Number(
            right.counterpart_track_number ||
              right.slot_track_number ||
              right.current_track_number ||
              0,
          ) ||
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

interface ReadyChangeGroup {
  key: string;
  label: string;
  count: number;
  valueSummary?: string;
}

export function readyFieldGroup(field: string): { key: string; label: string } {
  if (['artist', 'album_artist'].includes(field))
    return { key: 'album_artist', label: 'Album artist' };
  if (['library_track_artist', 'track_artist'].includes(field))
    return { key: 'track_artist', label: 'Track artist credits' };
  if (field === 'filename') return { key: 'filename', label: 'Filenames' };
  if (['library_recording_mbid', 'musicbrainz_recording_id'].includes(field)) {
    return { key: 'recording_ids', label: 'Recording IDs' };
  }
  if (['title', 'library_title'].includes(field)) return { key: 'title', label: 'Track titles' };
  if (['album', 'library_album_title'].includes(field)) {
    return { key: 'album', label: 'Album title' };
  }
  if (
    ['track_number', 'disc_number', 'library_track_number', 'library_disc_number'].includes(field)
  ) {
    return { key: 'numbering', label: 'Numbering' };
  }
  return { key: field, label: changeFieldLabel(field) };
}

export function groupReadyChanges(items: ReviewFinding[], readyOnly = true): ReadyChangeGroup[] {
  const groups = new Map<
    string,
    {
      key: string;
      label: string;
      values: Array<{ old?: unknown; new?: unknown }>;
      findingIdSet: Set<number>;
    }
  >();
  for (const item of items.filter(
    (finding) =>
      finding.finding_type === 'reconciliation_metadata' &&
      (!readyOnly || finding.review_tier === 'ready'),
  )) {
    const details = item.details && typeof item.details === 'object' ? item.details : {};
    const changes =
      details.changes && typeof details.changes === 'object' && !Array.isArray(details.changes)
        ? (details.changes as Record<string, { old?: unknown; new?: unknown }>)
        : {};
    const fields = Object.keys(changes);
    if (!fields.length) continue;
    for (const field of fields) {
      const semantic = readyFieldGroup(field);
      const group = groups.get(semantic.key) || {
        key: semantic.key,
        label: semantic.label,
        values: [],
        findingIdSet: new Set<number>(),
      };
      group.findingIdSet.add(item.id);
      group.values.push(changes[field] || {});
      groups.set(semantic.key, group);
    }
  }
  return [...groups.values()].map(({ values, findingIdSet, ...group }) => {
    const count = findingIdSet.size;
    if (group.key === 'numbering') {
      const fields = items
        .filter((item) => findingIdSet.has(item.id))
        .flatMap((item) => Object.keys((item.details?.changes || {}) as Record<string, unknown>));
      const hasDisc = fields.some((field) => field.includes('disc_number'));
      const hasTrack = fields.some((field) => field.includes('track_number'));
      group.label =
        hasDisc && hasTrack
          ? 'Disc and track numbering'
          : hasDisc
            ? 'Disc numbering'
            : 'Track numbering';
    }
    let valueSummary: string;
    if (group.key === 'track_artist' && values.every((value) => !String(value.old ?? '').trim())) {
      valueSummary = 'Adding missing featured artists to metadata';
    } else if (group.key === 'recording_ids') {
      valueSummary = `Add MusicBrainz recording IDs to ${count} track${count === 1 ? '' : 's'}`;
    } else if (group.key === 'filename') {
      valueSummary = `Rename ${count} file${count === 1 ? '' : 's'} to clean release titles`;
    } else {
      const first = values[0];
      const same = values.every(
        (value) =>
          String(value.old ?? '') === String(first.old ?? '') &&
          String(value.new ?? '') === String(first.new ?? ''),
      );
      valueSummary = same
        ? `${String(first.old ?? 'empty')} → ${String(first.new ?? 'empty')}`
        : `Update ${count} track${count === 1 ? '' : 's'}`;
    }
    return { ...group, count, valueSummary };
  });
}
