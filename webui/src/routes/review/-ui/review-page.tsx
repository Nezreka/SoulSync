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
import { ReconciliationEvidence } from './reconciliation-evidence';
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
  const changes =
    details.changes && typeof details.changes === 'object' && !Array.isArray(details.changes)
      ? Object.keys(details.changes as Record<string, unknown>)
      : [];
  const expected = String(details.expected_title || finding.title.replace(/^Retag\s+/i, ''));
  if (changes.length === 1 && changes[0] === 'filename') return `Rename file: ${expected}`;
  if (finding.finding_type === 'reconciliation_unresolved' && changes.length === 0) {
    return `Confirm assignment: ${expected.replace(/^Confirm:\s*/i, '')}`;
  }
  if (/^Retag\s+/i.test(finding.title)) return `Update metadata: ${expected}`;
  return finding.title;
}

function statusLabel(status: string) {
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

function AssignmentRow({
  row,
  onSelect,
  selected,
}: {
  row: AlbumAssignment;
  onSelect: (findingId: number) => void;
  selected: boolean;
}) {
  const position = `${row.current_disc_number || row.slot_disc_number || 1}.${
    row.current_track_number ?? row.slot_track_number ?? '—'
  }`;
  const evidence = row.audio_title
    ? `${row.audio_title}${row.audio_score ? ` · ${Math.round(row.audio_score * 100)}%` : ''}`
    : row.confidence || '';
  const action =
    row.relationship === 'version_conflict'
      ? row.status === 'extra'
        ? `Possible version of ${row.counterpart_title || 'a missing track'}`
        : `Possible local version: ${row.counterpart_file || row.counterpart_title || ''}`
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
      onClick={() => row.finding_id && onSelect(row.finding_id)}
      disabled={!row.finding_id}
    >
      <span className={styles.rowStatus}>
        <i>{statusLabel(row.status)}</i>
      </span>
      <span className={styles.rowPosition}>{position}</span>
      <span className={styles.trackComparison}>
        <span>
          <strong>{row.current_title || 'No local file'}</strong>
          <small>{row.file || 'Missing from this folder'}</small>
        </span>
        <b aria-hidden="true">→</b>
        <span>
          <strong>{row.slot_title || 'No release slot'}</strong>
          <small>
            Release {row.slot_disc_number || 1}.{row.slot_track_number ?? '—'}
          </small>
        </span>
      </span>
      <span className={styles.rowResult}>
        <strong>{action}</strong>
        {evidence ? <small>{evidence}</small> : null}
        {row.playlists?.length ? <small>Playlist: {row.playlists.join(', ')}</small> : null}
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

function groupReadyChanges(items: ReviewFinding[]): ReadyChangeGroup[] {
  const groups = new Map<
    string,
    ReadyChangeGroup & { values: Array<{ old?: unknown; new?: unknown }> }
  >();
  for (const item of items.filter((finding) => finding.review_tier === 'ready')) {
    const details = item.details && typeof item.details === 'object' ? item.details : {};
    const changes =
      details.changes && typeof details.changes === 'object' && !Array.isArray(details.changes)
        ? (details.changes as Record<string, { old?: unknown; new?: unknown }>)
        : {};
    const fields = Object.keys(changes).sort();
    const key = fields.join('|') || 'other';
    const group = groups.get(key) || {
      key,
      label: fields.length ? fields.map(changeFieldLabel).join(' + ') : 'Other repair',
      count: 0,
      findingIds: [],
      values: [],
    };
    group.count += 1;
    group.findingIds.push(item.id);
    if (fields.length === 1) group.values.push(changes[fields[0]] || {});
    groups.set(key, group);
  }
  return [...groups.values()].map(({ values, ...group }) => {
    const fields = group.key.split('|');
    if (
      fields.includes('track_artist') &&
      fields.every((field) => ['artist', 'library_track_artist', 'track_artist'].includes(field))
    ) {
      return {
        ...group,
        label: 'Featured artist credits',
        valueSummary: 'Adding missing featured artists to metadata',
      };
    }
    if (
      fields.length > 0 &&
      fields.every((field) =>
        ['library_recording_mbid', 'musicbrainz_recording_id'].includes(field),
      )
    ) {
      return {
        ...group,
        label: 'Recording IDs',
        valueSummary: `Add MusicBrainz recording IDs to ${group.count} track${group.count === 1 ? '' : 's'}`,
      };
    }
    if (!values.length) return group;
    const first = values[0];
    const same = values.every(
      (value) =>
        String(value.old ?? '') === String(first.old ?? '') &&
        String(value.new ?? '') === String(first.new ?? ''),
    );
    return {
      ...group,
      valueSummary: same
        ? `${String(first.old ?? 'empty')} → ${String(first.new ?? 'empty')}`
        : undefined,
    };
  });
}

function SelectedActionDetail({
  finding,
  itemIndex,
  itemCount,
  typeInfo,
  busy,
  onAct,
  onPin,
  onSelect,
  previousId,
  nextId,
  compact = false,
}: {
  finding: ReviewFinding;
  itemIndex: number;
  itemCount: number;
  typeInfo?: FindingTypeInfo;
  busy: boolean;
  onAct: (action: 'fix' | 'dismiss', choice?: string) => void;
  onPin?: (index: number) => void;
  onSelect: (findingId: number | null) => void;
  previousId?: number;
  nextId?: number;
  compact?: boolean;
}) {
  const canApply = Boolean(typeInfo?.fixable) && finding.finding_type !== 'suspect_album_tag';
  const duplicateChoice = finding.finding_type === 'reconciliation_duplicate';
  const suggested = finding.review_tier === 'ready';
  const compactReason = finding.review_reason || finding.description;
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
        {compact
          ? compactReason || 'Review the supporting evidence.'
          : finding.description || 'Review the evidence below.'}
      </p>
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
              {duplicateChoice
                ? 'Choose the copy to keep'
                : suggested
                  ? typeInfo?.verb || 'Apply repair'
                  : canApply
                    ? 'Choose the correct action after reviewing the evidence'
                    : 'Review manually'}
            </strong>
            <p>
              {duplicateChoice
                ? 'Both files identify as the same release track. Playlist membership is shown with each copy.'
                : suggested
                  ? 'Evidence supports a direct repair.'
                  : finding.review_reason || 'The evidence still requires a decision.'}
            </p>
          </div>
        </section>
      )}
      {compact ? (
        <details className={styles.rowEvidence}>
          <summary>Evidence and exact changes</summary>
          <ReconciliationEvidence
            finding={finding}
            busy={busy}
            onPin={onPin}
            onKeepCopy={duplicateChoice ? (trackId) => onAct('fix', trackId) : undefined}
          />
        </details>
      ) : (
        <ReconciliationEvidence
          finding={finding}
          busy={busy}
          onPin={onPin}
          onKeepCopy={duplicateChoice ? (trackId) => onAct('fix', trackId) : undefined}
        />
      )}
      <div className={styles.actions}>
        {finding.finding_type === 'reconciliation_intent' ? (
          <>
            <button
              type="button"
              disabled={busy}
              className={styles.primary}
              onClick={() => onAct('fix', 'complete')}
            >
              I want the complete album
            </button>
            <button type="button" disabled={busy} onClick={() => onAct('fix', 'partial')}>
              Playlist tracks or partial album
            </button>
          </>
        ) : (
          canApply &&
          finding.finding_type !== 'reconciliation_release' &&
          finding.finding_type !== 'reconciliation_duplicate' && (
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
                    : 'Choose resolution'}
            </button>
          )
        )}
        <button type="button" disabled={busy} onClick={() => onAct('dismiss')}>
          Accept as-is
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
  const [albumView, setAlbumView] = useState<AlbumView>('plan');
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
      })
      .catch((cause: unknown) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
    };
  }, [caseId, queueMode]);

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
    .filter((row) => row.relationship === 'version_conflict' && row.status !== 'change')
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
      .filter((row) => row.relationship !== 'version_conflict' && row.status !== 'change')
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
  const settledAssignments = assignments.filter((row) =>
    ['confirmed', 'not_owned', 'settled'].includes(row.status),
  );
  const readyChangeGroups = useMemo(
    () => groupReadyChanges(reviewCase?.items || []),
    [reviewCase?.items],
  );
  const activeDecision = active
    ? decisions.find((decision) => decision.finding_ids.includes(active.id))
    : undefined;
  const reviewDecisions = decisions.filter((decision) => decision.risk !== 'ready');
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

  const selectFinding = (findingId: number | null) => {
    setActiveId(findingId);
    window.setTimeout(
      () => document.getElementById('selected-review-action')?.scrollIntoView({ block: 'nearest' }),
      0,
    );
  };

  const selectCase = (next: string) => {
    setNotice('');
    setAlbumView('plan');
    setCaseId(next);
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
          setError(status.error);
        } else if (status.failed) {
          setError(`${status.failed} ready change${status.failed === 1 ? '' : 's'} failed.`);
        } else {
          const applied = status.fixed || started.total || count;
          setNotice(
            organize
              ? `${applied} change${applied === 1 ? '' : 's'} applied; folder organization was queued.`
              : `${applied} ready change${applied === 1 ? '' : 's'} applied.`,
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
    if (action === 'fix' && finding.finding_type === 'reconciliation_extra') {
      fixAction = 'quarantine';
    }
    if (action === 'fix' && requireChoice.has(finding.finding_type) && !fixAction) return;
    if (
      action === 'fix' &&
      (typeInfo?.destructive || finding.finding_type === 'reconciliation_extra')
    ) {
      const confirmed = window.showConfirmDialog
        ? await window.showConfirmDialog({
            title: 'Apply repair',
            message: `Apply this action to ${finding.title}? Review the evidence and file path first.`,
            confirmText: 'Apply',
            destructive: true,
          })
        : window.confirm(`Apply this action to ${finding.title}?`);
      if (!confirmed) return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await applyReviewDecision(caseId, finding, action, fixAction);
      if (!result.success) throw new Error(result.error || 'The action did not complete');
      setNotice(result.message || (action === 'dismiss' ? 'Accepted as-is' : 'Repair applied'));
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
      setNotice(`${result.applied || 0} action${result.applied === 1 ? '' : 's'} handled.`);
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
        {!(plan.status === 'ready' && readyChangeGroups.length === 1 && assignments.length > 0) && (
          <details className={styles.readyChanges}>
            <summary>Review ready changes</summary>
            <div className={styles.readyChangeGroups}>
              {readyChangeGroups.map((group) => (
                <button
                  key={group.key}
                  type="button"
                  onClick={() => selectFinding(group.findingIds[0] ?? null)}
                >
                  <strong>{group.label}</strong>
                  <span>
                    {group.count} track{group.count === 1 ? '' : 's'}
                    {group.valueSummary ? ` · ${group.valueSummary}` : ''}
                  </span>
                </button>
              ))}
            </div>
          </details>
        )}
      </div>
    ) : null;

  const selectedActionDetail =
    active && reviewCase ? (
      <SelectedActionDetail
        finding={active}
        itemIndex={itemIndex}
        itemCount={reviewCase.count}
        typeInfo={typeInfo}
        busy={busy}
        onAct={(action, choice) => void act(active, action, choice)}
        onPin={
          active.finding_type === 'reconciliation_release'
            ? (index) => void act(active, 'fix', String(index))
            : undefined
        }
        onSelect={selectFinding}
        previousId={reviewCase.items[itemIndex - 1]?.id}
        nextId={reviewCase.items[itemIndex + 1]?.id}
      />
    ) : null;
  const compactSelectedActionDetail =
    active && reviewCase ? (
      <SelectedActionDetail
        finding={active}
        itemIndex={itemIndex}
        itemCount={reviewCase.count}
        typeInfo={typeInfo}
        busy={busy}
        onAct={(action, choice) => void act(active, action, choice)}
        onPin={
          active.finding_type === 'reconciliation_release'
            ? (pinIndex) => void act(active, 'fix', String(pinIndex))
            : undefined
        }
        onSelect={selectFinding}
        previousId={reviewCase.items[itemIndex - 1]?.id}
        nextId={reviewCase.items[itemIndex + 1]?.id}
        compact
      />
    ) : null;

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
            <span>{queue?.ready_total || 0} ready changes</span>
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
                <strong>{item.album || 'Unassigned files'}</strong>
                <span>{item.artist || item.case_id.split(':')[0]}</span>
                <small>
                  {caseStatusSummary(item)}
                  {item.error_count ? ` · ${item.error_count} failed` : ''}
                </small>
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
                  onClick={() => setAlbumView('evidence')}
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
                          : primaryReadyDecision?.label ||
                            `${plan.ready} supported changes are ready`}
                      </strong>
                      <p>
                        {readyChangeGroups.length > 1
                          ? `${readyChangeGroups.length} groups of changes passed the album and audio checks.`
                          : primaryReadyDecision?.summary ||
                            'All proposed changes are supported by the album and audio evidence.'}
                      </p>
                    </div>
                    <div className={styles.planBadges}>
                      <span data-tone="good">✓ {plan.ready} ready</span>
                    </div>
                  </div>
                  {readyActionControls}
                  {active &&
                    activeDecision?.risk === 'ready' &&
                    !activeAssignment &&
                    selectedActionDetail}
                </section>
              )}

              {albumView === 'plan' && decisions.length > 0 && showDecisionBoard && (
                <section className={styles.decisionBoard} aria-label="Album decisions">
                  <div className={styles.sectionHeading}>
                    <div>
                      <div className={styles.eyebrow}>Resolve first</div>
                      <h3>
                        {reviewDecisions.length === 1
                          ? 'One choice needs your input'
                          : `${reviewDecisions.length} choices need your input`}
                      </h3>
                      {reviewDecisions.length > 1 && (
                        <p>
                          Work from top to bottom. Earlier choices can update later track actions.
                        </p>
                      )}
                    </div>
                    <span>
                      {reviewActionCount} affected action{reviewActionCount === 1 ? '' : 's'}
                    </span>
                  </div>
                  <div className={styles.decisionGrid}>
                    {reviewDecisions.map((decision, decisionIndex) => {
                      const selected = active ? decision.finding_ids.includes(active.id) : false;
                      return (
                        <Fragment key={decision.id}>
                          <article data-risk={decision.risk} data-selected={selected || undefined}>
                            <button
                              type="button"
                              className={styles.decisionSelect}
                              onClick={() => selectFinding(decision.finding_ids[0] ?? null)}
                            >
                              <span className={styles.decisionStep} aria-hidden="true">
                                {reviewDecisions.length > 1 ? decisionIndex + 1 : '!'}
                              </span>
                              <span>
                                <small>{decision.label}</small>
                                <strong>
                                  {decisionInstruction(
                                    decision,
                                    reviewCase.album_plan?.numbering_mode,
                                  )}
                                </strong>
                                {decision.summary && <small>{decision.summary}</small>}
                              </span>
                              <b>
                                {decision.action_count} action
                                {decision.action_count === 1 ? '' : 's'}
                              </b>
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
                          </article>
                          {selected &&
                            !activeAssignment &&
                            active?.finding_type !== 'reconciliation_release' &&
                            selectedActionDetail}
                          {selected && active?.finding_type === 'reconciliation_release' && (
                            <section
                              className={styles.releaseDecision}
                              id="selected-review-action"
                              aria-label="Release decision"
                            >
                              <div>
                                <div className={styles.eyebrow}>Choose this first</div>
                                <h4>{findingHeading(active)}</h4>
                                <p>{active.description}</p>
                              </div>
                              <ReconciliationEvidence
                                finding={active}
                                busy={busy}
                                onPin={(index) => void act(active, 'fix', String(index))}
                              />
                              <div className={styles.actions}>
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
                      <span data-tone="good">✓ {plan.ready} ready</span>
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
                        <span data-status="confirmed">
                          ✓ {reviewCase.album_plan?.counts.confirmed} confirmed
                        </span>
                      )}
                      {(reviewCase.album_plan?.counts.changed || 0) > 0 && (
                        <span data-status="change">
                          ↻ {reviewCase.album_plan?.counts.changed} changes
                        </span>
                      )}
                      {(reviewCase.album_plan?.counts.uncertain || 0) > 0 && (
                        <span data-status="uncertain">
                          ! {reviewCase.album_plan?.counts.uncertain} uncertain
                        </span>
                      )}
                      {(reviewCase.album_plan?.counts.missing || 0) > 0 && (
                        <span data-status="missing">
                          − {reviewCase.album_plan?.counts.missing} missing
                        </span>
                      )}
                      {(reviewCase.album_plan?.counts.not_owned || 0) > 0 && (
                        <span data-status="not-owned">
                          ○ {reviewCase.album_plan?.counts.not_owned} outside collection
                        </span>
                      )}
                      {(reviewCase.album_plan?.counts.settled || 0) > 0 && (
                        <span data-status="settled">
                          ✓ {reviewCase.album_plan?.counts.settled} already reviewed
                        </span>
                      )}
                      {(reviewCase.album_plan?.counts.extra || 0) > 0 && (
                        <span data-status="extra">
                          + {reviewCase.album_plan?.counts.extra} extra
                        </span>
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
                  <div className={styles.trackTable} role="table">
                    <div className={styles.trackTableHead} role="row">
                      <span>Status</span>
                      <span>Position</span>
                      <span>Local file → release slot</span>
                      <span>Result</span>
                    </div>
                    {attentionAssignments.map((row, index) => (
                      <Fragment key={`${row.file_path || row.slot_title}-${index}`}>
                        <AssignmentRow
                          row={row}
                          onSelect={selectFinding}
                          selected={row.finding_id === active?.id}
                        />
                        {row.finding_id === active?.id && compactSelectedActionDetail}
                      </Fragment>
                    ))}
                    {changeAssignments.length > 0 && (
                      <details
                        className={styles.proposedAssignments}
                        open={activeAssignment?.status === 'change' || undefined}
                      >
                        <summary>
                          ↻ {changeAssignments.length} proposed track change
                          {changeAssignments.length === 1 ? '' : 's'}
                        </summary>
                        {changeAssignments.map((row, index) => (
                          <Fragment key={`${row.file_path || row.slot_title}-${index}`}>
                            <AssignmentRow
                              row={row}
                              onSelect={selectFinding}
                              selected={row.finding_id === active?.id}
                            />
                            {row.finding_id === active?.id && compactSelectedActionDetail}
                          </Fragment>
                        ))}
                      </details>
                    )}
                    {settledAssignments.length > 0 && (
                      <details className={styles.confirmedAssignments}>
                        <summary>✓ {settledAssignments.length} settled release slots</summary>
                        {settledAssignments.map((row, index) => (
                          <Fragment key={`${row.file_path || row.slot_title}-${index}`}>
                            <AssignmentRow
                              row={row}
                              onSelect={selectFinding}
                              selected={row.finding_id === active?.id}
                            />
                            {row.finding_id === active?.id && compactSelectedActionDetail}
                          </Fragment>
                        ))}
                      </details>
                    )}
                  </div>
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
                      <dd>{reviewCase.album_plan?.intent_scope || 'Complete'} album</dd>
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
                  {(reviewCase.album_plan?.candidates?.length || 0) > 1 && (
                    <details className={styles.albumCandidates}>
                      <summary>
                        Compare {reviewCase.album_plan?.candidates?.length} release candidates
                      </summary>
                      <div>
                        {reviewCase.album_plan?.candidates?.map((candidate, index) => (
                          <article key={`${candidate.source}-${candidate.release_id}`}>
                            <b>#{index + 1}</b>
                            <span>
                              <strong>{candidate.source}</strong>
                              <ProviderReleaseId
                                source={candidate.source}
                                releaseId={candidate.release_id}
                              />
                            </span>
                            <small>
                              {candidate.matched}/{candidate.total_slots} assigned ·{' '}
                              {candidate.missing} missing · {candidate.extra} extra
                            </small>
                          </article>
                        ))}
                      </div>
                    </details>
                  )}
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
                  {active ? selectedActionDetail : null}
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
