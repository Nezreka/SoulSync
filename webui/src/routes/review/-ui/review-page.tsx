import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { FindingTypeInfo } from '../../tools/-tools.groups';

import {
  applyReviewDecision,
  applyReviewDecisionGroup,
  fetchLastReconciliationRun,
  fetchReviewCase,
  fetchReviewApplyStatus,
  fetchReviewQueue,
  fetchReviewQueueModeTotals,
  fetchReconciliationScanStatus,
  runReconciliationScan,
  startReadyChanges,
  type AlbumAssignment,
  type ReconciliationRun,
  type ReconciliationScanStatus,
  type ReviewCase,
  type ReviewCaseSummary,
  type ReviewDecision,
  type ReviewFinding,
  type ReviewPlan,
  type ReviewQueuePage,
  type ReviewQueueModeTotals,
} from '../-review.api';
import { fetchFindingTypes } from '../../tools/-tools.api';
import { findingTypeLabel } from '../../tools/-tools.core';
import { ProviderReleaseId } from './provider-release-id';
import {
  CandidateFit,
  ReconciliationEvidence,
  ReleaseComparison,
  StatusChip,
} from './reconciliation-evidence';
import { ReviewArtwork } from './review-artwork';
import { ReviewAudio } from './review-audio';
import styles from './review-page.module.css';

const requireChoice = new Set([
  'reconciliation_extra',
  'reconciliation_duplicate',
  'reconciliation_intent',
  'reconciliation_release',
]);

const emptyPlan: ReviewPlan = {
  ready: 0,
  manual: 0,
  destructive: 0,
  blocked: 0,
  total: 0,
  status: 'needs_review' as const,
  blocker_reasons: [] as string[],
  can_apply_ready: false,
  can_organize: false,
};

const categoryOptions = [
  ['', 'All categories'],
  ['metadata', 'Safe metadata'],
  ['title_conventions', 'Title conventions'],
  ['title_issues', 'Title identity'],
  ['numbering', 'Numbering schemes'],
  ['release_conflicts', 'Release or edition'],
  ['audio_conflicts', 'Audio conflicts'],
  ['album_completeness', 'Album completeness'],
  ['duplicates', 'Duplicates'],
  ['extra_files', 'Extra files'],
] as const;

type QueueMode = 'review' | 'ready' | 'files' | 'all';
type AlbumView = 'plan' | 'tracks' | 'evidence';
type TrackView = 'issues' | 'release';

function shortDuration(value: number | null | undefined): string {
  if (!value || !Number.isFinite(value)) return '';
  return `${Math.floor(value / 60000)}:${String(Math.floor((value % 60000) / 1000)).padStart(2, '0')}`;
}

function assignmentKey(row: AlbumAssignment): string {
  return `${row.file_path || 'missing'}:${row.slot_disc_number || 1}:${row.slot_track_number || row.current_track_number || 0}`;
}

const queueModes: Array<{ value: QueueMode; label: string; risk: string }> = [
  { value: 'review', label: 'Needs input', risk: 'manual' },
  { value: 'ready', label: 'Ready to apply', risk: 'ready' },
  { value: 'files', label: 'File actions', risk: 'destructive' },
  { value: 'all', label: 'All', risk: '' },
];

const decisionOrder: Record<string, number> = {
  release_conflicts: 0,
  album_completeness: 1,
  numbering: 2,
  audio_conflicts: 3,
  title_issues: 4,
  duplicates: 5,
  extra_files: 6,
  title_conventions: 7,
  metadata: 8,
};

function caseStatusSummary(item: ReviewCaseSummary): string {
  if (!item.plan) {
    return `${item.pending} pending action${item.pending === 1 ? '' : 's'}`;
  }
  const ordered = [...(item.plan.decisions || [])].sort(
    (left, right) => (decisionOrder[left.category] ?? 99) - (decisionOrder[right.category] ?? 99),
  );
  const primary = ordered.find((decision) => decision.risk !== 'ready') || ordered[0];
  const status = item.plan.status === 'ready' ? 'Ready' : 'Needs input';
  const more = ordered.length > 1 ? ` · +${ordered.length - 1} more` : '';
  return `${status}${primary ? ` · ${primary.label}` : ''} · ${item.pending} action${item.pending === 1 ? '' : 's'}${more}`;
}

function findingHeading(finding: ReviewFinding): string {
  const details = finding.details && typeof finding.details === 'object' ? finding.details : {};
  if (details.version_conflict) {
    return `Compare versions: ${String(details.current_title || finding.title.replace(/^Unassigned audio:\s*/i, ''))}`;
  }
  const changes =
    details.changes && typeof details.changes === 'object' && !Array.isArray(details.changes)
      ? Object.keys(details.changes as Record<string, unknown>)
      : [];
  const expected = String(details.expected_title || finding.title.replace(/^Retag\s+/i, ''));
  if (details.audio_conflict === 'different_recording') return finding.title;
  if (changes.length === 1 && changes[0] === 'filename') return `Rename file: ${expected}`;
  if (finding.finding_type === 'reconciliation_unresolved' && changes.length === 0) {
    return `Confirm assignment: ${expected.replace(/^Confirm:\s*/i, '')}`;
  }
  if (/^Retag\s+/i.test(finding.title)) return `Update metadata: ${expected}`;
  return finding.title;
}

function statusLabel(status: string, row?: AlbumAssignment) {
  if (row?.audio_conflict === 'different_recording') return 'Wrong audio';
  if (row?.relationship === 'version_conflict') return 'Compare versions';
  return (
    {
      confirmed: 'Confirmed',
      change: 'Change',
      uncertain: 'Review',
      missing: 'Missing',
      not_owned: 'Not collected',
      extra: 'Extra',
      duplicate: 'Duplicate',
      settled: 'No action',
    }[status] || status
  );
}

function decisionInstruction(decision: ReviewDecision, numberingMode?: string): string {
  switch (decision.category) {
    case 'release_conflicts':
      return 'Choose the release this album should follow';
    case 'album_completeness':
      return 'Choose whether this folder represents the complete album';
    case 'numbering':
      return numberingMode === 'digital'
        ? 'Switch this album to continuous track numbering'
        : "Use the selected release's disc and track numbering";
    case 'audio_conflicts':
      return `Confirm ${decision.action_count} uncertain track assignment${decision.action_count === 1 ? '' : 's'}`;
    case 'title_issues':
      return `Resolve ${decision.action_count} recording title difference${decision.action_count === 1 ? '' : 's'}`;
    case 'duplicates':
      return `Choose which ${decision.action_count === 1 ? 'duplicate file' : 'duplicate files'} to keep`;
    case 'extra_files':
      return `Decide what to do with ${decision.action_count} unmatched file${decision.action_count === 1 ? '' : 's'}`;
    default:
      return decision.label;
  }
}

function decisionCategoryLabel(category: string): string {
  return (
    {
      release_conflicts: 'Release or edition',
      album_completeness: 'Album completeness',
      numbering: 'Numbering',
      audio_conflicts: 'Audio assignment',
      title_issues: 'Recording identity',
      duplicates: 'Duplicate files',
      extra_files: 'Unmatched files',
      title_conventions: 'Title wording',
      metadata: 'Metadata',
    }[category] || category.replaceAll('_', ' ')
  );
}

function AssignmentRow({
  row,
  onSelect,
  selected,
}: {
  row: AlbumAssignment;
  onSelect: (findingId: number, rowKey: string) => void;
  selected: boolean;
}) {
  const pairedVersion = row.relationship === 'version_conflict' && row.status === 'extra';
  const currentPosition = `${row.current_disc_number || 1}.${row.current_track_number ?? '—'}`;
  const releasePosition = `${row.counterpart_disc_number || row.slot_disc_number || 1}.${
    row.counterpart_track_number ?? row.slot_track_number ?? '—'
  }`;
  const position = pairedVersion
    ? `${currentPosition} → ${releasePosition}`
    : `${
        row.current_disc_number || row.slot_disc_number || 1
      }.${row.current_track_number ?? row.slot_track_number ?? '—'}`;
  const evidence = row.audio_title
    ? `${row.audio_title}${row.audio_score ? ` · ${Math.round(row.audio_score * 100)}%` : ''}`
    : row.confidence || '';
  const action =
    row.audio_conflict === 'different_recording'
      ? `Fingerprint identifies ${row.audio_title || 'a different recording'}`
      : row.relationship === 'version_conflict'
        ? 'Compare the local and release versions'
        : row.action_state === 'preview'
          ? row.preview_reason || 'Resolve the album question first'
          : row.status === 'settled'
            ? 'No pending action'
            : row.changes && Object.keys(row.changes).length
              ? Object.keys(row.changes)
                  .map((key) => key.replaceAll('_', ' '))
                  .join(', ')
              : row.status === 'uncertain'
                ? 'Confirm assignment'
                : row.title_difference === 'flavor'
                  ? 'Display wording only'
                  : row.title_difference === 'version_detail'
                    ? 'Version detail preserved'
                    : 'No metadata edit';
  return (
    <button
      type="button"
      role="row"
      className={styles.matrixRow}
      data-status={row.status}
      data-selected={selected || undefined}
      aria-expanded={row.finding_id ? selected : undefined}
      onClick={() => row.finding_id && onSelect(row.finding_id, assignmentKey(row))}
      disabled={!row.finding_id}
    >
      <span className={styles.rowStatus}>
        <i>{statusLabel(row.status, row)}</i>
      </span>
      <span className={styles.rowPosition}>{position}</span>
      <span className={styles.trackComparison}>
        <span>
          <strong>{row.current_title || 'No local file'}</strong>
          <small>
            {row.file || 'Missing from this folder'}
            {row.file_duration_ms ? ` · ${shortDuration(row.file_duration_ms)}` : ''}
          </small>
        </span>
        <b aria-hidden="true">→</b>
        <span>
          <strong>
            {(pairedVersion ? row.counterpart_title : row.slot_title) || 'No release slot'}
          </strong>
          <small>
            Release{' '}
            {pairedVersion
              ? releasePosition
              : `${row.slot_disc_number || 1}.${row.slot_track_number ?? '—'}`}
            {row.slot_duration_ms ? ` · ${shortDuration(row.slot_duration_ms)}` : ''}
          </small>
        </span>
      </span>
      <span className={styles.rowResult}>
        <strong>{action}</strong>
        {evidence ? <small>{evidence}</small> : null}
        {row.playlists?.length ? <small>Playlist: {row.playlists.join(', ')}</small> : null}
        {row.finding_id ? (
          <small>{selected ? '▾ Hide review' : '▸ Review this track'}</small>
        ) : null}
      </span>
    </button>
  );
}

function changeFieldLabel(field: string): string {
  return (
    {
      artist: 'Artist',
      album_artist: 'Album artist',
      album: 'Album title',
      title: 'Track title',
      filename: 'Filename',
      musicbrainz_recording_id: 'Recording ID',
      library_recording_mbid: 'Library recording ID',
    }[field] || field.replaceAll('_', ' ')
  );
}

interface ReadyChangeGroup {
  key: string;
  label: string;
  count: number;
  findingIds: number[];
  valueSummary?: string;
}

function readyFieldGroup(field: string): { key: string; label: string } {
  if (['artist', 'album_artist', 'library_track_artist', 'track_artist'].includes(field)) {
    return { key: 'artist', label: 'Artist credits' };
  }
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
    return { key: 'numbering', label: 'Track numbering' };
  }
  return { key: field, label: changeFieldLabel(field) };
}

function groupReadyChanges(items: ReviewFinding[], readyOnly = true): ReadyChangeGroup[] {
  const groups = new Map<
    string,
    ReadyChangeGroup & {
      values: Array<{ old?: unknown; new?: unknown }>;
      findingIdSet: Set<number>;
      rawFields: Set<string>;
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
        count: 0,
        findingIds: [],
        values: [],
        findingIdSet: new Set<number>(),
        rawFields: new Set<string>(),
      };
      group.findingIdSet.add(item.id);
      group.rawFields.add(field);
      group.values.push(changes[field] || {});
      groups.set(semantic.key, group);
    }
  }
  return [...groups.values()].map(({ values, findingIdSet, rawFields, ...group }) => {
    const findingIds = [...findingIdSet];
    const count = findingIds.length;
    const fields = [...rawFields];
    if (
      group.key === 'artist' &&
      fields.some((field) => ['track_artist', 'library_track_artist'].includes(field)) &&
      fields.every((field) => ['artist', 'library_track_artist', 'track_artist'].includes(field))
    ) {
      return {
        ...group,
        count,
        findingIds,
        label: 'Featured artist credits',
        valueSummary: 'Adding missing featured artists to metadata',
      };
    }
    if (group.key === 'recording_ids') {
      return {
        ...group,
        count,
        findingIds,
        label: 'Recording IDs',
        valueSummary: `Add MusicBrainz recording IDs to ${count} track${count === 1 ? '' : 's'}`,
      };
    }
    if (group.key === 'filename') {
      return {
        ...group,
        count,
        findingIds,
        valueSummary: `Rename ${count} file${count === 1 ? '' : 's'} to clean release titles`,
      };
    }
    if (!values.length) return { ...group, count, findingIds };
    const first = values[0];
    const same = values.every(
      (value) =>
        String(value.old ?? '') === String(first.old ?? '') &&
        String(value.new ?? '') === String(first.new ?? ''),
    );
    return {
      ...group,
      count,
      findingIds,
      valueSummary: same
        ? `${String(first.old ?? 'empty')} → ${String(first.new ?? 'empty')}`
        : `Update ${count} track${count === 1 ? '' : 's'}`,
    };
  });
}

function slotKey(slot: Record<string, unknown>): string {
  if (typeof slot.wanted_key === 'string' && slot.wanted_key) return slot.wanted_key;
  const id = textValue(slot.source_track_id).trim();
  if (id)
    return `id:${id}:${textValue(slot.disc_number || 1)}:${textValue(slot.track_number || 0)}`;
  const title = textValue(slot.title || slot.name)
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return `position:${textValue(slot.disc_number || 1)}:${textValue(slot.track_number || 0)}:${title}`;
}

function CompletenessChoice({
  finding,
  busy,
  onChoose,
}: {
  finding: ReviewFinding;
  busy: boolean;
  onChoose: (choice: string) => void;
}) {
  const missing = records(record(finding.details).missing_tracks);
  const [wanted, setWanted] = useState<string[]>([]);
  return (
    <div className={styles.completenessChoice}>
      <p>
        {missing.length} tracks are absent from this folder. Mark any you want to collect if this is
        a partial album.
      </p>
      <div className={styles.wantedTracks}>
        {missing.map((slot, index) => {
          const key = slotKey(slot);
          return (
            <label key={`${key}-${index}`}>
              <input
                type="checkbox"
                checked={wanted.includes(key)}
                onChange={(event) =>
                  setWanted((current) =>
                    event.target.checked
                      ? [...current, key]
                      : current.filter((value) => value !== key),
                  )
                }
              />
              <span>{textValue(slot.title || slot.name) || `Track ${index + 1}`}</span>
            </label>
          );
        })}
      </div>
      <div className={styles.decisionActions}>
        <button type="button" disabled={busy} onClick={() => onChoose('complete')}>
          I want the complete album
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onChoose(`partial:${JSON.stringify(wanted)}`)}
        >
          Keep as partial{wanted.length ? ` · ${wanted.length} wanted` : ''}
        </button>
      </div>
    </div>
  );
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map(record).filter((item) => Object.keys(item).length) : [];
}

function textValue(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

function candidateContext(candidate: Record<string, unknown>): string {
  return [
    textValue(candidate.country),
    textValue(candidate.format),
    textValue(candidate.date),
    textValue(candidate.disambiguation),
  ]
    .filter(Boolean)
    .join(' · ');
}

function SelectedActionDetail({
  finding,
  itemIndex,
  itemCount,
  typeInfo,
  busy,
  onAct,
  onSelect,
  previousId,
  nextId,
  comparisonPath,
  comparisonDurationMs,
  compact = false,
}: {
  finding: ReviewFinding;
  itemIndex: number;
  itemCount: number;
  typeInfo?: FindingTypeInfo;
  busy: boolean;
  onAct: (action: 'fix' | 'dismiss', choice?: string) => void;
  onSelect: (findingId: number | null) => void;
  previousId?: number;
  nextId?: number;
  comparisonPath?: string;
  comparisonDurationMs?: number;
  compact?: boolean;
}) {
  const canApply = Boolean(typeInfo?.fixable) && finding.finding_type !== 'suspect_album_tag';
  const [assignmentSlot, setAssignmentSlot] = useState('');
  const assignmentOptions = records(record(finding.details).assignment_options);
  const duplicateChoice = finding.finding_type === 'reconciliation_duplicate';
  const wrongAudio = finding.details?.audio_conflict === 'different_recording';
  const versionConflict = finding.details?.version_conflict === true;
  const replacement = record(finding.details?.replacement_track);
  const canReplaceWrongAudio = Boolean(wrongAudio && replacement?.source_track_id);
  const suggested = finding.review_tier === 'ready';
  const compactReason = finding.review_reason || finding.description;
  const details = record(finding.details);
  const localDurationMs = Number(
    details.file_duration_ms || record(details.current_tags).duration_ms || 0,
  );
  const releaseDurationMs = Number(
    details.expected_duration_ms || record(details.replacement_track).duration_ms || 0,
  );
  const durationDifference =
    localDurationMs && releaseDurationMs ? Math.abs(localDurationMs - releaseDurationMs) : 0;
  return (
    <section
      className={styles.findingDetail}
      data-compact={compact || undefined}
      id="selected-review-action"
      aria-label="Selected action detail"
    >
      {!compact && (
        <div className={styles.decisionHeader}>
          <span>
            {suggested ? 'Ready change' : 'Needs review'} · {findingTypeLabel(finding.finding_type)}{' '}
            · Item {itemIndex + 1} of {itemCount}
          </span>
          <span className={styles.severity}>{finding.severity}</span>
        </div>
      )}
      <h3>{findingHeading(finding)}</h3>
      <p className={styles.description}>
        {versionConflict
          ? 'The local audio and release track may be different versions. Listen before choosing how to keep them.'
          : compact
            ? compactReason || 'Review the supporting evidence.'
            : finding.description || 'Review the evidence below.'}
      </p>
      {durationDifference > 3000 && (
        <div className={styles.durationWarning}>
          <strong>Length differs by {(durationDifference / 1000).toFixed(1)} seconds</strong>
          <span>
            Local {shortDuration(localDurationMs)} · Release {shortDuration(releaseDurationMs)}
          </span>
        </div>
      )}
      {finding.file_path &&
        ['reconciliation_unresolved', 'reconciliation_extra'].includes(finding.finding_type) && (
          <ReviewAudio
            localPath={finding.file_path}
            localTitle={
              textValue(details.current_title || record(details.current_tags).title) ||
              finding.title
            }
            localDurationMs={localDurationMs}
            comparisonPath={comparisonPath}
            comparisonTitle={
              textValue(details.expected_title || record(details.replacement_track).name) ||
              'Release recording'
            }
            comparisonDurationMs={comparisonDurationMs || releaseDurationMs}
            previewUrl={textValue(
              details.expected_preview_url || record(details.replacement_track).preview_url,
            )}
          />
        )}
      {!compact && (
        <section
          className={styles.suggestion}
          data-tone={suggested ? 'good' : 'warning'}
          aria-label="Suggested resolution"
        >
          <span className={styles.suggestionIcon} aria-hidden="true">
            {suggested ? '✓' : '!'}
          </span>
          <div>
            <div className={styles.eyebrow}>Suggested resolution</div>
            <strong>
              {wrongAudio
                ? canReplaceWrongAudio
                  ? 'Replace with the expected release track'
                  : 'Move the wrong file to quarantine'
                : duplicateChoice
                  ? 'Choose the copy to keep'
                  : suggested
                    ? typeInfo?.verb || 'Apply repair'
                    : canApply
                      ? 'Choose the correct action after reviewing the evidence'
                      : 'Review manually'}
            </strong>
            <p>
              {wrongAudio
                ? canReplaceWrongAudio
                  ? 'Requests the exact track from the selected release, then moves this file to recoverable quarantine.'
                  : 'The audio is wrong for this release slot, but no exact replacement source is available.'
                : duplicateChoice
                  ? 'Both files identify as the same release track. Playlist membership is shown with each copy.'
                  : suggested
                    ? 'Evidence supports a direct repair.'
                    : finding.review_reason || 'The evidence still requires a decision.'}
            </p>
          </div>
        </section>
      )}
      <ReconciliationEvidence
        finding={finding}
        busy={busy}
        prominent={
          finding.finding_type === 'reconciliation_unresolved' || wrongAudio || versionConflict
        }
        onKeepCopy={duplicateChoice ? (trackId) => onAct('fix', trackId) : undefined}
      />
      {finding.finding_type === 'reconciliation_intent' && (
        <CompletenessChoice
          key={finding.id}
          finding={finding}
          busy={busy}
          onChoose={(choice) => onAct('fix', choice)}
        />
      )}
      {finding.file_path && assignmentOptions.length > 0 && (
        <div className={styles.assignmentCorrection}>
          <label>
            Assign this file to another release track
            <select
              value={assignmentSlot}
              onChange={(event) => setAssignmentSlot(event.target.value)}
            >
              <option value="">Choose a release track…</option>
              {assignmentOptions.map((slot, index) => (
                <option value={textValue(slot.key)} key={`${textValue(slot.key)}-${index}`}>
                  {textValue(slot.disc_number)}.{textValue(slot.track_number)}{' '}
                  {textValue(slot.title)}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy || !assignmentSlot}
            onClick={() => onAct('fix', `assign:${assignmentSlot}`)}
          >
            Save assignment
          </button>
          <small>The album will be rescanned before any metadata edit is offered.</small>
        </div>
      )}
      <div className={styles.actions}>
        {finding.finding_type === 'reconciliation_intent' ? null : versionConflict && canApply ? (
          <>
            {replacement.source_track_id && (
              <>
                <button
                  type="button"
                  disabled={busy}
                  className={styles.primary}
                  onClick={() => onAct('fix', 'request_missing')}
                >
                  Keep local and request release version
                </button>
                <button type="button" disabled={busy} onClick={() => onAct('fix', 'replace')}>
                  Replace local with release version
                </button>
              </>
            )}
          </>
        ) : wrongAudio && canApply ? (
          <>
            {canReplaceWrongAudio && (
              <button
                type="button"
                disabled={busy}
                className={styles.primary}
                onClick={() => onAct('fix', 'replace')}
              >
                {busy ? 'Applying…' : 'Replace with correct track'}
              </button>
            )}
            <button type="button" disabled={busy} onClick={() => onAct('fix', 'quarantine')}>
              Quarantine wrong file
            </button>
          </>
        ) : (
          canApply &&
          finding.finding_type !== 'reconciliation_release' &&
          finding.finding_type !== 'reconciliation_duplicate' &&
          finding.finding_type !== 'reconciliation_unresolved' && (
            <button
              type="button"
              disabled={busy}
              className={styles.primary}
              onClick={() => onAct('fix')}
            >
              {busy
                ? 'Applying…'
                : suggested
                  ? typeInfo?.verb || 'Apply repair'
                  : finding.finding_type === 'reconciliation_extra'
                    ? 'Quarantine file'
                    : 'Apply this change'}
            </button>
          )
        )}
        <button type="button" disabled={busy} onClick={() => onAct('dismiss')}>
          {versionConflict ? 'Keep local version' : 'Accept as-is'}
        </button>
        <button
          type="button"
          disabled={busy || previousId == null}
          onClick={() => onSelect(previousId ?? null)}
        >
          ← Previous decision
        </button>
        <button
          type="button"
          disabled={busy || nextId == null}
          onClick={() => onSelect(nextId ?? null)}
        >
          Next decision →
        </button>
      </div>
    </section>
  );
}

export function ReviewPage() {
  const initialParams = useMemo(() => new URLSearchParams(window.location.search), []);
  const initialMode = initialParams.get('mode');
  const [queue, setQueue] = useState<ReviewQueuePage | null>(null);
  const [queueTotals, setQueueTotals] = useState<ReviewQueueModeTotals | null>(null);
  const [caseId, setCaseId] = useState(() => initialParams.get('case') || '');
  const [reviewCase, setReviewCase] = useState<ReviewCase | null>(null);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [activeRowKey, setActiveRowKey] = useState<string | null>(null);
  const [albumView, setAlbumView] = useState<AlbumView>('plan');
  const [trackFilter, setTrackFilter] = useState('');
  const [trackView, setTrackView] = useState<TrackView>('issues');
  const [openGroups, setOpenGroups] = useState<string[]>([]);
  const [previewCandidate, setPreviewCandidate] = useState(0);
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [queueMode, setQueueMode] = useState<QueueMode>(
    initialMode && queueModes.some((mode) => mode.value === initialMode)
      ? (initialMode as QueueMode)
      : 'review',
  );
  const [searchInput, setSearchInput] = useState('');
  const [catalog, setCatalog] = useState<Record<string, FindingTypeInfo>>({});
  const [busy, setBusy] = useState(false);
  const [scanBusy, setScanBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [applyProgress, setApplyProgress] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [scanStatus, setScanStatus] = useState<ReconciliationScanStatus | null>(null);
  const [lastRun, setLastRun] = useState<ReconciliationRun | null>(null);
  const wasScanning = useRef(false);
  const risk = queueModes.find((mode) => mode.value === queueMode)?.risk || '';

  useEffect(() => {
    let live = true;
    void fetchLastReconciliationRun()
      .then((run) => {
        if (live) setLastRun(run);
      })
      .catch(() => undefined);
    const poll = async () => {
      try {
        const status = await fetchReconciliationScanStatus();
        if (!live) return;
        setScanStatus(status);
        const scanning = status.current_job?.job_id === 'library_reconciliation';
        if (wasScanning.current && !scanning) {
          void fetchLastReconciliationRun()
            .then((run) => {
              if (live) setLastRun(run);
            })
            .catch(() => undefined);
        }
        wasScanning.current = scanning;
      } catch {
        // Status is supporting context; a temporary poll failure should not
        // hide the review queue or block a decision.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 15000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (searchInput !== query) {
        setPage(0);
        setCaseId('');
        setQuery(searchInput);
      }
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput, query]);

  const refreshQueue = useCallback(async () => {
    const result = await fetchReviewQueue(page, query, category, risk);
    setQueue(result);
    void fetchReviewQueueModeTotals(query, category)
      .then(setQueueTotals)
      .catch(() => undefined);
    return result;
  }, [page, query, category, risk]);

  useEffect(() => {
    let live = true;
    void Promise.all([
      fetchReviewQueue(page, query, category, risk),
      fetchReviewQueueModeTotals(query, category),
    ])
      .then(([result, totals]) => {
        if (!live) return;
        setQueue(result);
        setQueueTotals(totals);
        if (!caseId && result.items[0]) setCaseId(result.items[0].case_id);
      })
      .catch((cause: unknown) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
    };
    // caseId is deliberately excluded: changing the selected case does not rescan the queue.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, query, category, risk]);

  useEffect(() => {
    void fetchFindingTypes()
      .then((types) => {
        setCatalog(Object.fromEntries(types.map((type) => [type.type, type])));
      })
      .catch(() => setCatalog({}));
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (caseId) url.searchParams.set('case', caseId);
    else url.searchParams.delete('case');
    url.searchParams.set('mode', queueMode);
    window.history.replaceState(null, '', url);
    if (!caseId) {
      setReviewCase(null);
      return;
    }
    let live = true;
    setReviewCase(null);
    setError('');
    void fetchReviewCase(caseId)
      .then((result) => {
        if (!live) return;
        setReviewCase(result);
        setActiveId((current) => {
          if (result.items.some((f) => f.id === current)) return current;
          const hasStructuredOverview = Boolean(
            result.decisions?.length ||
            result.plan?.decisions?.length ||
            result.album_plan?.assignments?.length,
          );
          if (!hasStructuredOverview) return result.items[0]?.id ?? null;
          return (
            result.items.find((finding) => finding.finding_type === 'reconciliation_release')?.id ??
            null
          );
        });
        setActiveRowKey(null);
        setOpenGroups([]);
      })
      .catch((cause: unknown) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
    };
  }, [caseId, queueMode]);

  useEffect(() => {
    if (!caseId || reviewCase?.refresh?.state !== 'running') return;
    let live = true;
    const timer = window.setInterval(() => {
      void fetchReviewCase(caseId)
        .then(async (updated) => {
          if (!live) return;
          setReviewCase(updated);
          if (updated.refresh?.state !== 'running') {
            await refreshQueue();
            if (updated.refresh?.state === 'error')
              setError(updated.refresh.error || 'Album refresh failed');
            else setNotice('Album plan updated.');
          }
        })
        .catch(async () => {
          if (!live) return;
          const updatedQueue = await refreshQueue();
          if (!live) return;
          setReviewCase(null);
          setCaseId(updatedQueue.items.find((item) => item.case_id !== caseId)?.case_id || '');
          setNotice('Album review completed.');
        });
    }, 1200);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [caseId, reviewCase?.refresh?.state, refreshQueue]);

  const active = useMemo(
    () => reviewCase?.items.find((finding) => finding.id === activeId),
    [reviewCase, activeId],
  );
  const itemIndex =
    active && reviewCase ? reviewCase.items.findIndex((f) => f.id === active.id) : -1;
  const caseIndex = queue?.items.findIndex((item) => item.case_id === caseId) ?? -1;
  const pageCount = queue ? Math.max(1, Math.ceil(queue.total / queue.limit)) : 1;
  const albumNumber = queue && caseIndex >= 0 ? page * queue.limit + caseIndex + 1 : null;
  const typeInfo = active ? catalog[active.finding_type] : undefined;
  const plan = reviewCase?.plan || emptyPlan;
  const decisions = [...(reviewCase?.decisions || plan.decisions || [])].sort(
    (left, right) => (decisionOrder[left.category] ?? 99) - (decisionOrder[right.category] ?? 99),
  );
  const assignments = reviewCase?.album_plan?.assignments || [];
  const pendingAssignments = assignments.filter(
    (row) => !['confirmed', 'not_owned', 'settled'].includes(row.status),
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
          row.status !== 'missing',
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
  const readyChangeGroups = useMemo(
    () => groupReadyChanges(reviewCase?.items || []),
    [reviewCase?.items],
  );
  const proposedChangeGroups = useMemo(
    () => groupReadyChanges(reviewCase?.items || [], false),
    [reviewCase?.items],
  );
  const matchingFilter = (row: AlbumAssignment) =>
    !trackFilter ||
    (trackFilter.startsWith('decision:')
      ? decisions.some(
          (decision) =>
            `decision:${decision.id}` === trackFilter &&
            row.finding_id != null &&
            decision.finding_ids.includes(row.finding_id),
        )
      : trackFilter === 'uncertain'
        ? row.status === 'uncertain'
        : trackFilter === 'duplicate'
          ? row.status === 'duplicate'
          : trackFilter === 'extra'
            ? row.status === 'extra'
            : trackFilter === 'change'
              ? row.status === 'change'
              : trackFilter === 'settled'
                ? ['confirmed', 'settled', 'not_owned'].includes(row.status)
                : trackFilter === 'missing'
                  ? row.status === 'missing'
                  : trackFilter === 'version_conflict'
                    ? row.relationship === 'version_conflict'
                    : Object.keys(row.changes || {}).some(
                        (field) => readyFieldGroup(field).key === trackFilter,
                      ));
  const activeDecision = active
    ? decisions.find((decision) => decision.finding_ids.includes(active.id))
    : undefined;
  const reviewDecisions = decisions.filter((decision) => decision.risk !== 'ready');
  const hasIntentDecision = reviewDecisions.some(
    (decision) => decision.category === 'album_completeness',
  );
  const readyDecisions = decisions.filter((decision) => decision.risk === 'ready');
  const reviewActionCount = reviewDecisions.reduce(
    (total, decision) => total + decision.action_count,
    0,
  );
  const activeAssignment = active
    ? assignments.find((row) => row.finding_id === active.id)
    : undefined;
  const primaryReadyDecision = readyDecisions.length === 1 ? readyDecisions[0] : undefined;
  const showDecisionBoard = reviewDecisions.length > 0;
  const primaryCandidate = reviewCase?.album_plan?.candidates?.[0];
  const mappedSlots =
    primaryCandidate?.matched ?? assignments.filter((row) => row.slot_title).length;
  const totalSlots = primaryCandidate?.total_slots ?? mappedSlots;
  const settledSlots =
    (reviewCase?.album_plan?.counts.confirmed || 0) + (reviewCase?.album_plan?.counts.settled || 0);

  const selectFinding = (findingId: number | null, rowKey?: string) => {
    setActiveId((current) => (current === findingId ? null : findingId));
    const target = assignments.find((row) => row.finding_id === findingId);
    setActiveRowKey(rowKey || (target ? assignmentKey(target) : null));
    if (target?.status === 'change')
      setOpenGroups((current) => (current.includes('changes') ? current : [...current, 'changes']));
    window.setTimeout(
      () => document.getElementById('selected-review-action')?.scrollIntoView({ block: 'nearest' }),
      0,
    );
  };

  const selectCase = (next: string) => {
    setNotice('');
    setAlbumView('plan');
    setTrackFilter('');
    setTrackView('issues');
    setOpenGroups([]);
    setPreviewCandidate(0);
    setCaseId(next);
  };

  const openDecision = (decision: ReviewDecision) => {
    const first = decision.finding_ids[0] ?? null;
    const albumWide = [
      'release_conflicts',
      'album_completeness',
      'numbering',
      'title_conventions',
      'metadata',
    ].includes(decision.category);
    if (albumWide) {
      setAlbumView('plan');
      setActiveId(first);
      setActiveRowKey(null);
    } else {
      setAlbumView('tracks');
      setTrackView('issues');
      setTrackFilter(`decision:${decision.id}`);
      setActiveId(first);
      setActiveRowKey(
        assignments.find((row) => row.finding_id === first)
          ? assignmentKey(assignments.find((row) => row.finding_id === first)!)
          : null,
      );
      setOpenGroups(['changes', 'missing', 'settled']);
    }
    window.setTimeout(
      () => document.getElementById('selected-review-action')?.scrollIntoView({ block: 'nearest' }),
      0,
    );
  };

  const toggleGroup = (group: string) =>
    setOpenGroups((current) =>
      current.includes(group) ? current.filter((item) => item !== group) : [...current, group],
    );

  const selectAssignment = (findingId: number, rowKey: string) => {
    if (activeId === findingId && activeRowKey === rowKey) {
      setActiveId(null);
      setActiveRowKey(null);
    } else {
      setActiveId(findingId);
      setActiveRowKey(rowKey);
    }
  };

  const refresh = async () => {
    setError('');
    try {
      const updatedQueue = await refreshQueue();
      if (caseId) {
        const updated = await fetchReviewCase(caseId).catch(() => null);
        setReviewCase(updated);
        if (!updated) setCaseId(updatedQueue.items[0]?.case_id || '');
      }
    } catch (cause) {
      setError((cause as Error).message);
    }
  };

  const runScan = async () => {
    setScanBusy(true);
    setError('');
    try {
      await runReconciliationScan();
      setNotice('Library reconciliation was queued. Refresh the queue after the scan finishes.');
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setScanBusy(false);
    }
  };

  const applyReady = async (scopeCaseId?: string, organize = false) => {
    const count = scopeCaseId ? plan.ready : 0;
    const confirmed = window.showConfirmDialog
      ? await window.showConfirmDialog({
          title: organize ? 'Apply and organize album' : 'Apply ready changes',
          message: organize
            ? `Apply ${count} supported metadata changes, then organize this album folder?`
            : 'Apply every metadata change in the queue that passed its album safety checks?',
          confirmText: organize ? 'Apply and organize' : 'Apply ready changes',
        })
      : window.confirm(
          scopeCaseId ? `Apply ${count} ready metadata changes?` : 'Apply all ready changes?',
        );
    if (!confirmed) return;
    setApplyBusy(true);
    setError('');
    setNotice('');
    try {
      const started = await startReadyChanges(scopeCaseId, organize);
      setApplyProgress(`Applying 0 of ${started.total || count} ready changes…`);
      const poll = async () => {
        const status = await fetchReviewApplyStatus();
        if (status.running) {
          setApplyProgress(
            `Applying ${status.done || 0} of ${status.total || started.total || count} ready changes…`,
          );
          window.setTimeout(() => void poll(), 750);
          return;
        }
        setApplyBusy(false);
        setApplyProgress('');
        if (status.error) {
          setError(`${status.fixed || 0} applied; ${status.failed || 0} failed. ${status.error}`);
        } else if (status.failed) {
          setError(
            `${status.fixed || 0} applied; ${status.failed} failed. ${status.errors?.[0]?.error || 'Open the affected album for details.'}`,
          );
        } else {
          const applied = status.fixed || started.total || count;
          setNotice(
            organize
              ? `${applied} change${applied === 1 ? '' : 's'} applied; folder organization was queued.${status.reconciliation_queued ? ' Reconciliation is queued.' : ''}`
              : `${applied} ready change${applied === 1 ? '' : 's'} applied.${status.reconciliation_queued ? ' Reconciliation is queued.' : ''}`,
          );
        }
        await refresh();
      };
      window.setTimeout(() => void poll(), 500);
    } catch (cause) {
      setApplyBusy(false);
      setApplyProgress('');
      setError((cause as Error).message);
    }
  };

  const act = async (finding: ReviewFinding, action: 'fix' | 'dismiss', override?: string) => {
    let fixAction: string | null = override || null;
    if (action === 'fix' && finding.finding_type === 'reconciliation_extra' && !fixAction) {
      fixAction = 'quarantine';
    }
    if (action === 'fix' && requireChoice.has(finding.finding_type) && !fixAction) return;
    if (
      action === 'fix' &&
      !fixAction?.startsWith('assign:') &&
      fixAction !== 'request_missing' &&
      (typeInfo?.destructive || finding.finding_type === 'reconciliation_extra')
    ) {
      const confirmed = window.showConfirmDialog
        ? await window.showConfirmDialog({
            title: fixAction === 'replace' ? 'Replace wrong audio' : 'Apply repair',
            message:
              fixAction === 'replace'
                ? 'Request the exact track from the selected release and move this file to recoverable quarantine?'
                : `Apply this action to ${finding.title}? Review the evidence and file path first.`,
            confirmText: fixAction === 'replace' ? 'Replace audio' : 'Apply',
            destructive: true,
          })
        : window.confirm(
            fixAction === 'replace'
              ? 'Request the exact track and move the wrong file to recoverable quarantine?'
              : `Apply this action to ${finding.title}?`,
          );
      if (!confirmed) return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await applyReviewDecision(caseId, finding, action, fixAction);
      if (!result.success && !result.partial)
        throw new Error(result.error || 'The action did not complete');
      if (result.partial)
        setError(
          `Partially completed: ${result.error || 'Review the updated album before retrying.'}`,
        );
      else
        setNotice(
          `${result.message || (action === 'dismiss' ? 'Accepted as-is' : 'Repair applied')}${result.refresh_queued ? ' Updating this album plan…' : ''}`,
        );
      const [updatedCase, updatedQueue] = await Promise.all([
        fetchReviewCase(caseId).catch(() => null),
        refreshQueue(),
      ]);
      if (updatedCase) {
        setReviewCase(updatedCase);
        const remaining = updatedCase.items;
        setActiveId(remaining[Math.min(itemIndex, remaining.length - 1)]?.id ?? null);
      } else {
        setReviewCase(null);
        const next = updatedQueue.items.find((item) => item.case_id !== caseId);
        setCaseId(next?.case_id || '');
        if (!next && updatedQueue.total > 0 && page > 0) setPage(0);
      }
    } catch (cause) {
      setError((cause as Error).message);
      // The backend may have updated the evidence or error on this finding.
      void fetchReviewCase(caseId)
        .then(setReviewCase)
        .catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  const actGroup = async (decision: ReviewDecision, action: 'fix' | 'dismiss') => {
    const verb = action === 'fix' ? 'apply' : 'keep the current values for';
    const confirmed = window.showConfirmDialog
      ? await window.showConfirmDialog({
          title: decision.label,
          message: `This will ${verb} ${decision.action_count} proposed action${decision.action_count === 1 ? '' : 's'}.`,
          confirmText: action === 'fix' ? 'Apply actions' : 'Keep current values',
        })
      : window.confirm(`${verb} ${decision.action_count} proposed actions?`);
    if (!confirmed) return;
    setBusy(true);
    setError('');
    try {
      const result = await applyReviewDecisionGroup(caseId, decision.category, action);
      if (!result.success) {
        setError(
          `${result.applied || 0} of ${result.total || decision.action_count} changes completed. ${result.error || 'The remaining change failed.'}`,
        );
      } else {
        setNotice(
          `${result.applied || 0} action${result.applied === 1 ? '' : 's'} handled.${result.refresh_queued ? ' Updating this album plan…' : ''}`,
        );
      }
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const readyActionControls =
    reviewCase && plan.ready > 0 ? (
      <div className={styles.planActions}>
        <button
          type="button"
          className={styles.primary}
          disabled={applyBusy}
          onClick={() => void applyReady(reviewCase.case_id)}
        >
          Apply {plan.ready} ready change
          {plan.ready === 1 ? '' : 's'}
        </button>
        {plan.can_organize && reviewCase.album_id != null && (
          <button
            type="button"
            disabled={applyBusy}
            onClick={() => void applyReady(reviewCase.case_id, true)}
          >
            Apply and organize folder
          </button>
        )}
      </div>
    ) : null;

  const readySummaryList =
    readyChangeGroups.length > 0 ? (
      <div className={styles.readySummaryList} aria-label="Ready change summary">
        {readyChangeGroups.map((group) => (
          <button
            key={group.key}
            type="button"
            onClick={() => {
              setTrackFilter(group.key);
              setAlbumView('tracks');
              setOpenGroups((current) =>
                current.includes('changes') ? current : [...current, 'changes'],
              );
            }}
          >
            <span>
              <strong>{group.label}</strong>
              <small>{group.valueSummary}</small>
            </span>
            <b>
              {group.count} track{group.count === 1 ? '' : 's'}
            </b>
          </button>
        ))}
      </div>
    ) : null;

  const comparisonPath = active
    ? (Array.isArray(active.details?.same_audio_copies) &&
        active.details.same_audio_copies.find(
          (path: unknown) => typeof path === 'string' && path !== active.file_path,
        )) ||
      assignments.find(
        (row) =>
          row.file_path &&
          row.file_path !== active.file_path &&
          row.slot_title &&
          (row.slot_title === active.details?.expected_title ||
            row.slot_title === active.details?.acoustid_title),
      )?.file_path ||
      ''
    : '';
  const comparisonDurationMs =
    assignments.find((row) => row.file_path === comparisonPath)?.file_duration_ms || 0;

  const selectedActionDetail =
    active && reviewCase ? (
      <SelectedActionDetail
        key={active.id}
        finding={active}
        itemIndex={itemIndex}
        itemCount={reviewCase.count}
        typeInfo={typeInfo}
        busy={busy}
        onAct={(action, choice) => void act(active, action, choice)}
        onSelect={selectFinding}
        previousId={reviewCase.items[itemIndex - 1]?.id}
        nextId={reviewCase.items[itemIndex + 1]?.id}
        comparisonPath={comparisonPath || undefined}
        comparisonDurationMs={comparisonDurationMs}
      />
    ) : null;
  const compactSelectedActionDetail =
    active && reviewCase ? (
      <SelectedActionDetail
        key={active.id}
        finding={active}
        itemIndex={itemIndex}
        itemCount={reviewCase.count}
        typeInfo={typeInfo}
        busy={busy}
        onAct={(action, choice) => void act(active, action, choice)}
        onSelect={selectFinding}
        previousId={reviewCase.items[itemIndex - 1]?.id}
        nextId={reviewCase.items[itemIndex + 1]?.id}
        comparisonPath={comparisonPath || undefined}
        comparisonDurationMs={comparisonDurationMs}
        compact
      />
    ) : null;

  const renderAssignmentRow = (row: AlbumAssignment, index: number) => {
    const key = assignmentKey(row);
    const selected = row.finding_id === active?.id && key === activeRowKey;
    return (
      <Fragment key={`${key}-${index}`}>
        <AssignmentRow
          row={row}
          selected={selected}
          onSelect={(findingId, rowKey) => {
            if (row.status === 'missing' && hasIntentDecision) {
              const intent = reviewDecisions.find(
                (decision) => decision.category === 'album_completeness',
              );
              if (intent) openDecision(intent);
            } else selectAssignment(findingId, rowKey);
          }}
        />
        {selected && compactSelectedActionDetail}
      </Fragment>
    );
  };

  const releaseOrderedAssignments = [...assignments].sort(
    (left, right) =>
      Number(left.slot_disc_number || left.current_disc_number || 1) -
        Number(right.slot_disc_number || right.current_disc_number || 1) ||
      Number(left.slot_track_number || left.current_track_number || 0) -
        Number(right.slot_track_number || right.current_track_number || 0) ||
      (left.file || '').localeCompare(right.file || ''),
  );

  return (
    <main className={`page-shell ${styles.page}`}>
      <header className={styles.header}>
        <div>
          <a href="/tools" className={styles.back}>
            ← Library Maintenance
          </a>
          <h1>Review Queue</h1>
          <p>Review album plans, apply supported changes, and resolve exceptions.</p>
        </div>
        <div className={styles.headerTools}>
          <div className={styles.headerCount}>
            <strong>{queueTotals?.all ?? queue?.total ?? 0} albums</strong>
            <span>
              {queueTotals?.ready ?? 0} ready albums · {queue?.ready_total || 0} ready changes
            </span>
          </div>
          <button
            type="button"
            onClick={() => {
              setQueueMode('ready');
              setPage(0);
              setCaseId('');
            }}
          >
            Review ready batch
          </button>
          <button
            type="button"
            className={styles.safeAction}
            disabled={applyBusy || !queue?.ready_total}
            onClick={() => void applyReady()}
          >
            {applyBusy
              ? applyProgress || 'Applying…'
              : `Apply ready changes (${queue?.ready_total || 0})`}
          </button>
          <details className={styles.moreMenu}>
            <summary aria-label="More queue actions">More</summary>
            <div>
              <button
                type="button"
                disabled={scanBusy || scanStatus?.current_job?.job_id === 'library_reconciliation'}
                onClick={() => void runScan()}
              >
                {scanBusy
                  ? 'Queuing…'
                  : scanStatus?.current_job?.job_id === 'library_reconciliation'
                    ? 'Scanning…'
                    : 'Scan library'}
              </button>
              <button type="button" onClick={() => void refresh()}>
                Refresh queue
              </button>
            </div>
          </details>
        </div>
      </header>

      {error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className={styles.notice} role="status">
          {notice}
        </div>
      )}
      {(scanStatus?.current_job?.job_id === 'library_reconciliation' || lastRun) && (
        <div className={styles.scanStatus} role="status">
          {scanStatus?.current_job?.job_id === 'library_reconciliation'
            ? `Reconciliation running: ${scanStatus.current_job.progress?.scanned ?? 0} of ${scanStatus.current_job.progress?.total ?? '?'} folders (${scanStatus.current_job.progress?.percent ?? 0}%).`
            : lastRun?.status === 'completed'
              ? `Last reconciliation completed: ${lastRun.items_scanned} files checked.`
              : `Last reconciliation ${lastRun?.status}: ${lastRun?.error_text || 'No reason recorded'}. ${lastRun?.items_scanned ?? 0} files checked; a rerun reuses cached fingerprints.`}
        </div>
      )}

      <div className={styles.workspace}>
        <aside className={styles.sidebar} aria-label="Reconciliation albums">
          <nav className={styles.queueModes} aria-label="Queue view">
            {queueModes.map((mode) => (
              <button
                key={mode.value}
                type="button"
                aria-current={queueMode === mode.value ? 'page' : undefined}
                onClick={() => {
                  setQueueMode(mode.value);
                  setPage(0);
                  setCaseId('');
                }}
              >
                <span>{mode.label}</span>
                <b>{queueTotals?.[mode.value] ?? '—'}</b>
              </button>
            ))}
          </nav>
          <label htmlFor="review-search">Find album or track</label>
          <input
            id="review-search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Search findings"
          />
          <label className={styles.categoryFilter}>
            Filter by type
            <select
              value={category}
              onChange={(event) => {
                setCategory(event.target.value);
                setPage(0);
                setCaseId('');
              }}
            >
              {categoryOptions.map(([value, label]) => (
                <option key={value || 'all'} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <div className={styles.caseList}>
            {queue?.items.map((item) => (
              <button
                key={item.case_id}
                type="button"
                className={item.case_id === caseId ? styles.selectedCase : styles.caseButton}
                onClick={() => selectCase(item.case_id)}
              >
                <ReviewArtwork
                  title={item.album || 'Unassigned files'}
                  url={item.album_thumb_url}
                  size="list"
                />
                <span className={styles.caseCopy}>
                  <strong>{item.album || 'Unassigned files'}</strong>
                  <span>{item.artist || item.case_id.split(':')[0]}</span>
                  <small>
                    {caseStatusSummary(item)}
                    {item.error_count ? ` · ${item.error_count} failed` : ''}
                  </small>
                </span>
              </button>
            ))}
          </div>
          <div className={styles.pagination}>
            <button
              type="button"
              disabled={page === 0}
              onClick={() => {
                setCaseId('');
                setPage(page - 1);
              }}
            >
              Previous
            </button>
            <span>
              Page {page + 1} of {pageCount}
            </span>
            <select
              aria-label="Jump to page"
              value={page}
              onChange={(event) => {
                setCaseId('');
                setPage(Number(event.target.value));
              }}
            >
              {Array.from({ length: pageCount }, (_, index) => (
                <option key={index} value={index}>
                  Page {index + 1}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={!queue || (page + 1) * queue.limit >= queue.total}
              onClick={() => {
                setCaseId('');
                setPage(page + 1);
              }}
            >
              Next
            </button>
          </div>
        </aside>

        <section className={styles.review} aria-label="Current review case">
          {reviewCase ? (
            <>
              <div className={styles.albumHeader}>
                <div className={styles.albumIdentity}>
                  <ReviewArtwork
                    title={reviewCase.album || 'Unassigned files'}
                    url={reviewCase.album_thumb_url}
                    size="header"
                  />
                  <div>
                    <div className={styles.eyebrow}>
                      Album {albumNumber ?? '—'} of {queue?.total ?? '—'}
                      {' · '}
                      {reviewCase.count} pending action
                      {reviewCase.count === 1 ? '' : 's'}
                    </div>
                    <h2>{reviewCase.album || 'Unassigned files'}</h2>
                    <p>{reviewCase.artist || 'Artist not established'}</p>
                  </div>
                </div>
                <div className={styles.nav}>
                  <button
                    type="button"
                    disabled={caseIndex <= 0}
                    onClick={() => selectCase(queue!.items[caseIndex - 1].case_id)}
                  >
                    ← Album
                  </button>
                  <button
                    type="button"
                    disabled={!queue || caseIndex < 0 || caseIndex >= queue.items.length - 1}
                    onClick={() => selectCase(queue!.items[caseIndex + 1].case_id)}
                  >
                    Album →
                  </button>
                </div>
              </div>

              <nav className={styles.albumViews} aria-label="Album review views">
                <button
                  type="button"
                  aria-current={albumView === 'plan' ? 'page' : undefined}
                  onClick={() => setAlbumView('plan')}
                >
                  Plan
                  <span>{reviewDecisions.length || plan.ready}</span>
                </button>
                <button
                  type="button"
                  aria-current={albumView === 'tracks' ? 'page' : undefined}
                  onClick={() => setAlbumView('tracks')}
                >
                  Tracks
                  <span>{assignments.length}</span>
                </button>
                <button
                  type="button"
                  aria-current={albumView === 'evidence' ? 'page' : undefined}
                  onClick={() => {
                    setAlbumView('evidence');
                    setActiveId((current) => current ?? reviewCase.items[0]?.id ?? null);
                  }}
                >
                  Evidence
                </button>
              </nav>

              {albumView === 'plan' && plan.status === 'ready' && (
                <section
                  className={styles.albumPlan}
                  data-state="ready"
                  aria-label="Album reconciliation plan"
                >
                  <div className={styles.planSummary}>
                    <div>
                      <div className={styles.eyebrow}>Ready to apply</div>
                      <strong>
                        {readyChangeGroups.length === 1
                          ? readyChangeGroups[0].valueSummary ||
                            `${readyChangeGroups[0].label} on ${readyChangeGroups[0].count} tracks`
                          : `${readyChangeGroups.length} supported updates across ${plan.ready} track${plan.ready === 1 ? '' : 's'}`}
                      </strong>
                      <p>
                        {readyChangeGroups.length > 1
                          ? `${readyChangeGroups.length} groups of changes passed the album and audio checks.`
                          : primaryReadyDecision?.summary ||
                            'All proposed changes are supported by the album and audio evidence.'}
                      </p>
                    </div>
                    <div className={styles.planBadges}>
                      <StatusChip tone="good" icon="check">
                        {plan.ready} ready
                      </StatusChip>
                    </div>
                  </div>
                  {readySummaryList}
                  {readyActionControls}
                  {active &&
                    activeDecision?.risk === 'ready' &&
                    !activeAssignment &&
                    selectedActionDetail}
                </section>
              )}

              {albumView === 'plan' && decisions.length > 0 && showDecisionBoard && (
                <section className={styles.decisionBoard} aria-label="Album decisions">
                  {reviewDecisions.length > 1 && (
                    <div className={styles.sectionHeading}>
                      <div>
                        <div className={styles.eyebrow}>Resolve in order</div>
                        <h3>{reviewDecisions.length} choices need your input</h3>
                        <p>
                          Work from top to bottom. Earlier choices can update later track actions.
                        </p>
                      </div>
                      <span>
                        {reviewActionCount} affected action{reviewActionCount === 1 ? '' : 's'}
                      </span>
                    </div>
                  )}
                  <div className={styles.decisionGrid}>
                    {reviewDecisions.map((decision, decisionIndex) => {
                      const selected = active ? decision.finding_ids.includes(active.id) : false;
                      return (
                        <Fragment key={decision.id}>
                          <article data-risk={decision.risk} data-selected={selected || undefined}>
                            <button
                              type="button"
                              className={styles.decisionSelect}
                              onClick={() => openDecision(decision)}
                            >
                              <span className={styles.decisionStep} aria-hidden="true">
                                {reviewDecisions.length > 1 ? decisionIndex + 1 : '!'}
                              </span>
                              <span>
                                <small>{decisionCategoryLabel(decision.category)}</small>
                                <strong>
                                  {decisionInstruction(
                                    decision,
                                    reviewCase.album_plan?.numbering_mode,
                                  )}
                                </strong>
                                {decision.summary && <small>{decision.summary}</small>}
                              </span>
                              {reviewDecisions.length > 1 && (
                                <b>
                                  {decision.action_count} action
                                  {decision.action_count === 1 ? '' : 's'}
                                  {' →'}
                                </b>
                              )}
                            </button>
                            {['numbering', 'title_conventions', 'metadata'].includes(
                              decision.category,
                            ) && (
                              <div className={styles.decisionActions}>
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => void actGroup(decision, 'fix')}
                                >
                                  {decision.category === 'numbering'
                                    ? reviewCase.album_plan?.numbering_mode === 'digital'
                                      ? 'Use continuous numbering'
                                      : 'Use release numbering'
                                    : decision.category === 'title_conventions'
                                      ? 'Apply title conventions'
                                      : `Apply ${decision.label.toLowerCase()}`}
                                </button>
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => void actGroup(decision, 'dismiss')}
                                >
                                  {decision.category === 'numbering'
                                    ? 'Keep current numbering'
                                    : 'Keep current values'}
                                </button>
                              </div>
                            )}
                            {decision.category === 'album_completeness' &&
                              (() => {
                                const intent = reviewCase.items.find(
                                  (item) =>
                                    item.finding_type === 'reconciliation_intent' &&
                                    decision.finding_ids.includes(item.id),
                                );
                                return intent ? (
                                  <CompletenessChoice
                                    key={intent.id}
                                    finding={intent}
                                    busy={busy}
                                    onChoose={(choice) => void act(intent, 'fix', choice)}
                                  />
                                ) : null;
                              })()}
                          </article>
                          {selected &&
                            !activeAssignment &&
                            active?.finding_type !== 'reconciliation_intent' &&
                            active?.finding_type !== 'reconciliation_release' &&
                            !['numbering', 'title_conventions', 'metadata'].includes(
                              decision.category,
                            ) &&
                            selectedActionDetail}
                          {selected && active?.finding_type === 'reconciliation_release' && (
                            <section
                              className={styles.releaseDecision}
                              id="selected-review-action"
                              aria-label="Release decision"
                            >
                              <div>
                                <div className={styles.eyebrow}>Release options</div>
                                <h4>Compare the editions that fit this folder</h4>
                                <p>
                                  Choose using the edition details below. Open Evidence when you
                                  need the file-by-file comparison.
                                </p>
                                <p>
                                  Choosing a release pins its track list. It does not replace the
                                  album artwork.
                                </p>
                              </div>
                              <div className={styles.releaseChoiceList}>
                                {records(record(active.details).candidates).map(
                                  (candidate, candidateIndex) => {
                                    const context = candidateContext(candidate);
                                    const current = record(record(active.details).current_release);
                                    const isCurrent =
                                      Boolean(current.release_id) &&
                                      current.source === candidate.source &&
                                      String(current.release_id) === String(candidate.release_id);
                                    return (
                                      <article
                                        key={`${textValue(candidate.source)}-${textValue(candidate.release_id)}-${candidateIndex}`}
                                      >
                                        <span className={styles.candidateRank}>
                                          {candidateIndex + 1}
                                        </span>
                                        <ReviewArtwork
                                          title={textValue(candidate.release_title) || 'Release'}
                                          url={textValue(candidate.image_url)}
                                          size="release"
                                        />
                                        <div>
                                          <strong>
                                            {textValue(candidate.release_title) ||
                                              `Candidate ${candidateIndex + 1}`}
                                          </strong>
                                          <small>
                                            {textValue(candidate.source) || 'catalog'}{' '}
                                            <ProviderReleaseId
                                              source={textValue(candidate.source)}
                                              releaseId={textValue(candidate.release_id)}
                                            />
                                          </small>
                                          {context && <small>{context}</small>}
                                          {isCurrent && (
                                            <StatusChip tone="info" icon="disc">
                                              Current library release
                                            </StatusChip>
                                          )}
                                        </div>
                                        <CandidateFit candidate={candidate} />
                                        <button
                                          type="button"
                                          className={styles.compareCandidate}
                                          onClick={() => {
                                            setAlbumView('evidence');
                                            setPreviewCandidate(candidateIndex);
                                          }}
                                        >
                                          Compare tracks
                                        </button>
                                        <button
                                          type="button"
                                          disabled={busy}
                                          onClick={() =>
                                            void act(active, 'fix', String(candidateIndex))
                                          }
                                        >
                                          Use this release
                                        </button>
                                      </article>
                                    );
                                  },
                                )}
                              </div>
                              <div className={styles.actions}>
                                <button type="button" onClick={() => setAlbumView('evidence')}>
                                  Compare full evidence
                                </button>
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => void act(active, 'dismiss')}
                                >
                                  Keep current release
                                </button>
                              </div>
                            </section>
                          )}
                        </Fragment>
                      );
                    })}
                  </div>
                </section>
              )}

              {albumView === 'plan' &&
                plan.status !== 'ready' &&
                reviewCase.album_plan &&
                !reviewDecisions.some((decision) => decision.category === 'release_conflicts') && (
                  <section className={styles.planContext} aria-label="Album plan context">
                    <div>
                      <div className={styles.eyebrow}>Album context</div>
                      <h4>What this decision affects</h4>
                    </div>
                    <dl>
                      <div>
                        <dt>Selected release</dt>
                        <dd>
                          {reviewCase.album_plan.source || 'Catalog'}{' '}
                          <ProviderReleaseId
                            source={reviewCase.album_plan.source}
                            releaseId={reviewCase.album_plan.release_id}
                          />
                        </dd>
                      </div>
                      <div>
                        <dt>Track coverage</dt>
                        <dd>
                          {mappedSlots} of {totalSlots || '?'} release slots assigned
                        </dd>
                      </div>
                      <div>
                        <dt>Already settled</dt>
                        <dd>
                          {settledSlots === 0
                            ? 'None yet'
                            : `${settledSlots} track${settledSlots === 1 ? '' : 's'} need${
                                settledSlots === 1 ? 's' : ''
                              } no decision`}
                        </dd>
                      </div>
                      <div>
                        <dt>Planned numbering</dt>
                        <dd>
                          {reviewCase.album_plan.numbering_mode === 'digital'
                            ? 'One continuous album sequence'
                            : 'Disc and track positions from the release'}
                        </dd>
                      </div>
                    </dl>
                    <div className={`${styles.actions} ${styles.planContextActions}`}>
                      <button type="button" onClick={() => setAlbumView('tracks')}>
                        Review track assignments
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setAlbumView('evidence');
                          setActiveId((current) => current ?? reviewCase.items[0]?.id ?? null);
                        }}
                      >
                        View supporting evidence
                      </button>
                    </div>
                  </section>
                )}

              {albumView === 'plan' &&
                plan.status !== 'ready' &&
                proposedChangeGroups.length > 0 && (
                  <section className={styles.planContext} aria-label="Proposed metadata by field">
                    <div>
                      <div className={styles.eyebrow}>Proposed metadata</div>
                      <h4>Changes by field</h4>
                      <p>Choose a field to see the affected tracks and exact values.</p>
                    </div>
                    <div className={styles.readySummaryList}>
                      {proposedChangeGroups.map((group) => (
                        <button
                          key={group.key}
                          type="button"
                          onClick={() => {
                            setTrackFilter(group.key);
                            setAlbumView('tracks');
                            setOpenGroups((current) =>
                              current.includes('changes') ? current : [...current, 'changes'],
                            );
                          }}
                        >
                          <span>
                            <strong>{group.label}</strong>
                            <small>{group.valueSummary}</small>
                          </span>
                          <b>
                            {group.count} track{group.count === 1 ? '' : 's'} →
                          </b>
                        </button>
                      ))}
                    </div>
                  </section>
                )}

              {albumView === 'plan' && plan.status !== 'ready' && plan.ready > 0 && (
                <section
                  className={`${styles.albumPlan} ${styles.safeChangesPanel}`}
                  data-state="supporting"
                  aria-label="Independent safe changes"
                >
                  <div className={styles.planSummary}>
                    <div>
                      <div className={styles.eyebrow}>Safe changes</div>
                      <strong>
                        {plan.ready} independent change{plan.ready === 1 ? ' is' : 's are'} ready
                      </strong>
                      <p>These can be applied without resolving the decisions above.</p>
                    </div>
                    <div className={styles.planBadges}>
                      <StatusChip tone="good" icon="check">
                        {plan.ready} ready
                      </StatusChip>
                    </div>
                  </div>
                  {readyActionControls}
                  {active &&
                    activeDecision?.risk === 'ready' &&
                    !activeAssignment &&
                    selectedActionDetail}
                </section>
              )}

              {albumView === 'plan' && !showDecisionBoard && plan.ready === 0 && (
                <div className={styles.empty}>No album-level action is pending.</div>
              )}

              {albumView === 'tracks' && assignments.length > 0 && (
                <section
                  className={styles.trackMatrix}
                  aria-label="Complete album track assignment"
                >
                  <div className={styles.sectionHeading}>
                    <div>
                      <div className={styles.eyebrow}>Album track list</div>
                      <h3>Track assignments</h3>
                      <p>
                        Compare each local file with the selected release slot and proposed result.
                      </p>
                    </div>
                    <div className={styles.matrixCounts}>
                      {(reviewCase.album_plan?.counts.confirmed || 0) > 0 && (
                        <StatusChip tone="good" icon="check">
                          {reviewCase.album_plan?.counts.confirmed} confirmed
                        </StatusChip>
                      )}
                      {(reviewCase.album_plan?.counts.changed || 0) > 0 && (
                        <StatusChip tone="info" icon="arrow">
                          {reviewCase.album_plan?.counts.changed} changes
                        </StatusChip>
                      )}
                      {(reviewCase.album_plan?.counts.uncertain || 0) > 0 && (
                        <StatusChip tone="warning" icon="warning">
                          {reviewCase.album_plan?.counts.uncertain} uncertain
                        </StatusChip>
                      )}
                      {(reviewCase.album_plan?.counts.missing || 0) > 0 && (
                        <StatusChip tone="danger" icon="x">
                          {reviewCase.album_plan?.counts.missing} missing
                        </StatusChip>
                      )}
                      {(reviewCase.album_plan?.counts.not_owned || 0) > 0 && (
                        <StatusChip tone="muted" icon="info">
                          {reviewCase.album_plan?.counts.not_owned} outside collection
                        </StatusChip>
                      )}
                      {(reviewCase.album_plan?.counts.settled || 0) > 0 && (
                        <StatusChip tone="muted" icon="check">
                          {reviewCase.album_plan?.counts.settled} already reviewed
                        </StatusChip>
                      )}
                      {(reviewCase.album_plan?.counts.extra || 0) > 0 && (
                        <StatusChip tone="danger" icon="warning">
                          {reviewCase.album_plan?.counts.extra} extra
                        </StatusChip>
                      )}
                    </div>
                  </div>
                  <div className={styles.releaseContext}>
                    <span>
                      Selected {reviewCase.album_plan?.source || 'catalog'} release
                      <ProviderReleaseId
                        source={reviewCase.album_plan?.source}
                        releaseId={reviewCase.album_plan?.release_id}
                      />
                    </span>
                    {reviewCase.album_plan?.intent_scope && (
                      <span>Intent: {reviewCase.album_plan.intent_scope} album</span>
                    )}
                    {reviewCase.album_plan?.candidates?.[0] && (
                      <span>
                        {reviewCase.album_plan.numbering_mode === 'digital'
                          ? 'Continuous digital numbering'
                          : 'Multi-disc release numbering'}
                      </span>
                    )}
                  </div>
                  {pairedConflictCount > 0 && (
                    <p className={styles.versionExplanation}>
                      {pairedConflictCount} version conflict{pairedConflictCount === 1 ? '' : 's'}{' '}
                      pair a local file with an unmatched release slot. Each pair contributes one
                      extra file and one missing slot to the counts above.
                    </p>
                  )}
                  <div className={styles.trackFilters}>
                    <div
                      className={styles.trackViewSwitch}
                      role="group"
                      aria-label="Track arrangement"
                    >
                      <button
                        type="button"
                        aria-pressed={trackView === 'issues'}
                        onClick={() => setTrackView('issues')}
                      >
                        Group by action
                      </button>
                      <button
                        type="button"
                        aria-pressed={trackView === 'release'}
                        onClick={() => setTrackView('release')}
                      >
                        Release order
                      </button>
                    </div>
                    <label>
                      Show tracks
                      <select
                        value={trackFilter}
                        onChange={(event) => setTrackFilter(event.target.value)}
                      >
                        <option value="">All tracks</option>
                        {trackFilter.startsWith('decision:') && (
                          <option value={trackFilter}>
                            {decisions.find((decision) => `decision:${decision.id}` === trackFilter)
                              ?.label || 'Selected decision'}
                          </option>
                        )}
                        <option value="uncertain">Uncertain assignments</option>
                        <option value="duplicate">Duplicate files</option>
                        <option value="extra">Unassigned files</option>
                        <option value="change">All proposed changes</option>
                        <option value="settled">Settled tracks</option>
                        {proposedChangeGroups.map((group) => (
                          <option value={group.key} key={group.key}>
                            {group.label} ({group.count})
                          </option>
                        ))}
                        {missingAssignments.length > 0 && (
                          <option value="missing">Missing ({missingAssignments.length})</option>
                        )}
                        {pairedConflictCount > 0 && (
                          <option value="version_conflict">
                            Version conflicts ({pairedConflictCount})
                          </option>
                        )}
                      </select>
                    </label>
                    {trackFilter && (
                      <button type="button" onClick={() => setTrackFilter('')}>
                        Clear filter
                      </button>
                    )}
                  </div>
                  <div className={styles.trackTable} role="table">
                    <div className={styles.trackTableHead} role="row">
                      <span>Status</span>
                      <span>Position</span>
                      <span>Local file → release slot</span>
                      <span>Result</span>
                    </div>
                    {trackView === 'release' ? (
                      releaseOrderedAssignments.filter(matchingFilter).map(renderAssignmentRow)
                    ) : (
                      <>
                        {attentionAssignments.filter(matchingFilter).map(renderAssignmentRow)}
                        {changeAssignments.filter(matchingFilter).length > 0 && (
                          <div className={styles.assignmentGroup} data-tone="info">
                            <button
                              type="button"
                              aria-expanded={openGroups.includes('changes')}
                              onClick={() => toggleGroup('changes')}
                            >
                              {openGroups.includes('changes') ? '▾' : '▸'} ↻{' '}
                              {changeAssignments.filter(matchingFilter).length} proposed track
                              changes
                            </button>
                            {openGroups.includes('changes') &&
                              changeAssignments.filter(matchingFilter).map(renderAssignmentRow)}
                          </div>
                        )}
                        {missingAssignments.filter(matchingFilter).length > 0 && (
                          <div className={styles.assignmentGroup} data-tone="danger">
                            <button
                              type="button"
                              aria-expanded={openGroups.includes('missing')}
                              onClick={() => toggleGroup('missing')}
                            >
                              {openGroups.includes('missing') ? '▾' : '▸'}{' '}
                              {missingAssignments.filter(matchingFilter).length} absent release
                              tracks
                            </button>
                            {openGroups.includes('missing') && (
                              <>
                                {hasIntentDecision && (
                                  <p>
                                    Choose album completeness on Plan; each absent track is listed
                                    here for context.
                                  </p>
                                )}
                                {missingAssignments.filter(matchingFilter).map(renderAssignmentRow)}
                              </>
                            )}
                          </div>
                        )}
                        {settledAssignments.filter(matchingFilter).length > 0 && (
                          <div className={styles.assignmentGroup} data-tone="good">
                            <button
                              type="button"
                              aria-expanded={openGroups.includes('settled')}
                              onClick={() => toggleGroup('settled')}
                            >
                              {openGroups.includes('settled') ? '▾' : '▸'} ✓{' '}
                              {settledAssignments.filter(matchingFilter).length} settled release
                              slots
                            </button>
                            {openGroups.includes('settled') &&
                              settledAssignments.filter(matchingFilter).map(renderAssignmentRow)}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                  {active && !activeAssignment && selectedActionDetail}
                </section>
              )}

              {albumView === 'tracks' && assignments.length === 0 && (
                <div className={styles.empty}>
                  No track assignment data is available for this album.
                </div>
              )}

              {albumView === 'evidence' && (
                <section className={styles.evidenceView} aria-label="Album evidence">
                  <div className={styles.sectionHeading}>
                    <div>
                      <div className={styles.eyebrow}>Supporting information</div>
                      <h3>Why this plan was chosen</h3>
                      <p>
                        Catalog source, album scope, numbering, and the evidence behind each action.
                      </p>
                    </div>
                  </div>
                  <dl className={styles.albumFacts}>
                    <div>
                      <dt>Selected release</dt>
                      <dd>
                        {reviewCase.album_plan?.source || 'Catalog'}{' '}
                        <ProviderReleaseId
                          source={reviewCase.album_plan?.source}
                          releaseId={reviewCase.album_plan?.release_id}
                        />
                      </dd>
                    </div>
                    <div>
                      <dt>Album scope</dt>
                      <dd>
                        {reviewCase.album_plan?.intent_scope
                          ? `${reviewCase.album_plan.intent_scope} album`
                          : 'Not decided'}
                      </dd>
                    </div>
                    <div>
                      <dt>Numbering</dt>
                      <dd>
                        {reviewCase.album_plan?.numbering_mode === 'digital'
                          ? 'Continuous digital numbering'
                          : 'Release disc and track numbering'}
                      </dd>
                    </div>
                    <div>
                      <dt>Track coverage</dt>
                      <dd>
                        {assignments.length} local and release slot record
                        {assignments.length === 1 ? '' : 's'} compared
                      </dd>
                    </div>
                  </dl>
                  <ReleaseComparison
                    candidates={
                      (reviewCase.album_plan?.candidates ||
                        records(record(active?.details).candidates)) as Array<
                        Record<string, unknown>
                      >
                    }
                    selected={previewCandidate}
                    onSelect={setPreviewCandidate}
                    currentRelease={record(record(active?.details).current_release)}
                    onPin={
                      active?.finding_type === 'reconciliation_release'
                        ? (index) => void act(active, 'fix', String(index))
                        : undefined
                    }
                    busy={busy}
                  />
                  {reviewCase.items.length > 0 && (
                    <label className={styles.evidencePicker}>
                      Evidence for action
                      <select
                        value={activeId ?? ''}
                        onChange={(event) => selectFinding(Number(event.target.value))}
                      >
                        {reviewCase.items.map((finding) => (
                          <option key={finding.id} value={finding.id}>
                            {findingHeading(finding)}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {active && active.finding_type !== 'reconciliation_release'
                    ? selectedActionDetail
                    : null}
                </section>
              )}

              {albumView === 'plan' &&
                active &&
                !activeAssignment &&
                !activeDecision &&
                selectedActionDetail}
            </>
          ) : queue && queue.total === 0 ? (
            <div className={styles.empty}>
              No pending library reconciliation findings. Run a scan to check the library.
            </div>
          ) : (
            <div className={styles.empty}>Select an album to review.</div>
          )}
        </section>
      </div>
    </main>
  );
}
