import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ActionMenu } from '@/components/action-menu/action-menu';
import { useConfirmDialog } from '@/components/dialog/use-confirm-dialog';
import { Button, Select, TextInput } from '@/components/form/form';
import { PageHeader } from '@/components/page-header';
import { Notice } from '@/components/primitives';

import type { FindingTypeInfo } from '../../tools/-tools.groups';

import {
  resetAlbumScope,
  applyReviewDecision,
  reopenReviewAssignment,
  applyReviewDecisionGroup,
  fetchLastReconciliationRun,
  fetchReviewCase,
  fetchReviewApplyStatus,
  fetchReviewQueue,
  fetchReviewQueueModeTotals,
  pinReviewRelease,
  fetchReconciliationScanStatus,
  runReconciliationScan,
  reanalyzeReviewCase,
  startMissingTrackRequests,
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
import { usePolledStatus } from '../../tools/-tools.polling';
import { AssignmentRow } from './assignment-row';
import { CompletenessChoice } from './completeness-choice';
import { MissingTracksChoice } from './missing-tracks-choice';
import { ProviderReleaseId } from './provider-release-id';
import { AssignmentEvidence, ReleaseComparison, StatusChip } from './reconciliation-evidence';
import { buildAlbumModel, decisionOrder } from './review-album-model';
import { ReviewArtwork } from './review-artwork';
import styles from './review-page.module.css';
import { assignmentKey, changeFieldLabel, record, records, textValue } from './review-presentation';
import { SelectedActionDetail } from './selected-action-detail';

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
  ['metadata', 'Metadata changes'],
  ['title_conventions', 'Title conventions'],
  ['title_issues', 'Title identity'],
  ['numbering', 'Numbering schemes'],
  ['release_conflicts', 'Release or edition'],
  ['audio_conflicts', 'Audio conflicts'],
  ['album_completeness', 'Album completeness'],
  ['duplicates', 'Duplicates'],
  ['extra_files', 'Extra files'],
] as const;

type QueueMode = 'review' | 'ready' | 'files' | 'waiting' | 'all';
type AlbumView = 'plan' | 'release' | 'tracks';
type TrackView = 'issues' | 'release';

const queueModes: Array<{ value: QueueMode; label: string; risk: string }> = [
  { value: 'review', label: 'Needs input', risk: 'manual' },
  { value: 'ready', label: 'Ready to apply', risk: 'ready' },
  { value: 'files', label: 'File actions', risk: 'destructive' },
  { value: 'waiting', label: 'Waiting', risk: 'waiting' },
  { value: 'all', label: 'All', risk: '' },
];

function caseStatusSummary(item: ReviewCaseSummary): string {
  if (item.refreshing) return 'Reanalyzing this album · updated decisions will appear here';
  if (!item.plan) {
    return `${item.pending} pending action${item.pending === 1 ? '' : 's'}`;
  }
  const ordered = [...(item.plan.decisions || [])].sort(
    (left, right) => (decisionOrder[left.category] ?? 99) - (decisionOrder[right.category] ?? 99),
  );
  const primary = ordered.find((decision) => decision.risk !== 'ready') || ordered[0];
  const status =
    primary?.category === 'pending_fulfillment'
      ? 'Waiting for download'
      : item.plan.status === 'waiting'
        ? 'Waiting for download'
        : item.plan.status === 'ready'
          ? 'Ready'
          : 'Needs input';
  const more = ordered.length > 1 ? ` · +${ordered.length - 1} more` : '';
  return `${status}${primary ? ` · ${primary.label}` : ''} · ${item.pending} action${item.pending === 1 ? '' : 's'}${more}`;
}

function decisionInstruction(
  decision: ReviewDecision,
  numberingMode?: string,
  multipleDiscs = false,
): string {
  switch (decision.category) {
    case 'release_conflicts':
      return 'Choose the release this album should follow';
    case 'album_completeness':
      return 'Choose whether this folder represents the complete album';
    case 'numbering':
      if (decision.label === 'Disc numbering') return decision.summary || 'Update disc numbering';
      if (!multipleDiscs) return 'Renumber tracks to match the selected release';
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
      pending_fulfillment: 'Waiting for tracks',
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

function numberingSchemeLabel(multipleDiscs: boolean, mode?: string): string {
  if (!multipleDiscs) return 'One disc · match release positions';
  return mode === 'digital' ? 'One continuous album sequence' : 'Numbered by release disc';
}

interface ReadyChangeGroup {
  key: string;
  label: string;
  count: number;
  findingIds: number[];
  valueSummary?: string;
}

function readyFieldGroup(field: string): { key: string; label: string } {
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

function groupReadyChanges(items: ReviewFinding[], readyOnly = true): ReadyChangeGroup[] {
  const groups = new Map<
    string,
    ReadyChangeGroup & {
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
        count: 0,
        findingIds: [],
        values: [],
        findingIdSet: new Set<number>(),
      };
      group.findingIdSet.add(item.id);
      group.values.push(changes[field] || {});
      groups.set(semantic.key, group);
    }
  }
  return [...groups.values()].map(({ values, findingIdSet, ...group }) => {
    const findingIds = [...findingIdSet];
    const count = findingIds.length;
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
    if (group.key === 'track_artist' && values.every((value) => !String(value.old ?? '').trim())) {
      return {
        ...group,
        count,
        findingIds,
        label: 'Track artist credits',
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

export function ReviewPage() {
  const { confirm, dialog } = useConfirmDialog();
  const initialParams = useMemo(() => new URLSearchParams(window.location.search), []);
  const initialMode = initialParams.get('mode');
  const [queue, setQueue] = useState<ReviewQueuePage | null>(null);
  const caseListRef = useRef<HTMLDivElement | null>(null);
  const caseRequestVersion = useRef(0);
  const [queueTotals, setQueueTotals] = useState<ReviewQueueModeTotals | null>(null);
  const [caseId, setCaseId] = useState(() => initialParams.get('case') || '');
  const [reviewCase, setReviewCase] = useState<ReviewCase | null>(null);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [activeRowKey, setActiveRowKey] = useState<string | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const trackListScroll = useRef<Array<{ element: HTMLElement; top: number }>>([]);
  const inspectorRef = useRef<HTMLElement | null>(null);
  const [albumView, setAlbumView] = useState<AlbumView>('plan');
  const [trackFilter, setTrackFilter] = useState('');
  const [trackView, setTrackView] = useState<TrackView>('issues');
  const [openGroups, setOpenGroups] = useState<string[]>(['attention', 'changes']);
  const [previewCandidate, setPreviewCandidate] = useState(0);
  const [releaseIdInput, setReleaseIdInput] = useState('');
  const [manualRelease, setManualRelease] = useState<Record<string, unknown> | null>(null);
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
  const [reanalyzingCase, setReanalyzingCase] = useState<string | null>(null);
  const reanalyzing = reanalyzingCase === caseId;
  const [busyDecision, setBusyDecision] = useState('');
  const [scanBusy, setScanBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [applyProgress, setApplyProgress] = useState('');
  const [operationCase, setOperationCase] = useState<string | null>(
    window.sessionStorage.getItem('review-operation-case'),
  );
  const [error, setErrorValue] = useState('');
  const [errorCase, setErrorCase] = useState<string | null>(null);
  const setError = useCallback((value: string, scope?: string | null) => {
    setErrorValue(value);
    setErrorCase(value ? (scope === undefined ? selectedCaseRef.current || null : scope) : null);
  }, []);
  const [notice, setNotice] = useState('');
  const [globalNotice, setGlobalNotice] = useState('');
  const [reloadBusy, setReloadBusy] = useState(false);
  const [scanStatus, setScanStatus] = useState<ReconciliationScanStatus | null>(null);
  const [lastRun, setLastRun] = useState<ReconciliationRun | null>(null);
  const wasScanning = useRef(false);
  const risk = queueModes.find((mode) => mode.value === queueMode)?.risk || '';

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(''), 6000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (caseListRef.current) caseListRef.current.scrollTop = 0;
  }, [page, queueMode, category]);

  const scanHistoryLive = useRef(true);
  useEffect(() => {
    scanHistoryLive.current = true;
    void fetchLastReconciliationRun()
      .then((run) => {
        if (scanHistoryLive.current) setLastRun(run);
      })
      .catch(() => undefined);
    return () => {
      scanHistoryLive.current = false;
    };
  }, []);
  usePolledStatus({
    fetcher: () => fetchReconciliationScanStatus().catch(() => null),
    isRunning: (status) => status.current_job?.job_id === 'library_reconciliation',
    intervalMs: 15000,
    pollWhileIdle: true,
    onState: (status) => {
      setScanStatus(status);
      const scanning = status.current_job?.job_id === 'library_reconciliation';
      if (wasScanning.current && !scanning) {
        void fetchLastReconciliationRun()
          .then((run) => {
            if (scanHistoryLive.current) setLastRun(run);
          })
          .catch(() => undefined);
      }
      wasScanning.current = scanning;
    },
  });

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
    void fetchReviewQueueModeTotals(query, category)
      .then((totals) => {
        if (live) setQueueTotals(totals);
      })
      .catch(() => undefined);
    void fetchReviewQueue(page, query, category, risk)
      .then((result) => {
        if (!live) return;
        setQueue(result);
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
  }, [caseId, queueMode]);

  useEffect(() => {
    if (!caseId) {
      setReviewCase(null);
      return;
    }
    let live = true;
    const requestVersion = ++caseRequestVersion.current;
    setReviewCase(null);
    setError('');
    void fetchReviewCase(caseId)
      .then((result) => {
        if (!live || requestVersion !== caseRequestVersion.current) return;
        if (result.case_id !== caseId) {
          setCaseId(result.case_id);
          return;
        }
        if (
          result.status === 'completed' &&
          result.refresh?.state !== 'running' &&
          result.refresh?.state !== 'error'
        ) {
          void refreshRef.current();
          return;
        }
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
        setManualRelease(null);
        setPreviewCandidate(0);
        setOpenGroups(['attention', 'changes']);
      })
      .catch((cause: unknown) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
    };
  }, [caseId]);

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
  const mutationBusy = busy || reanalyzing || applyBusy || reviewCase?.refresh?.state === 'running';
  const plan = reviewCase?.plan || emptyPlan;
  const albumModel = useMemo(() => buildAlbumModel(reviewCase), [reviewCase]);
  const {
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
  } = albumModel;
  const sourceFolders = useMemo(
    () =>
      [
        ...new Set(
          assignments.flatMap((row) =>
            row.file_path ? [row.file_path.slice(0, row.file_path.lastIndexOf('/'))] : [],
          ),
        ),
      ].sort(),
    [assignments],
  );
  const readyChangeGroups = useMemo(
    () => groupReadyChanges(reviewCase?.items || []),
    [reviewCase?.items],
  );
  const proposedChangeGroups = useMemo(
    () => groupReadyChanges(reviewCase?.items || [], false),
    [reviewCase?.items],
  );
  const matchesTrackFilter = (row: AlbumAssignment, trackFilter: string) =>
    !trackFilter ||
    (trackFilter.startsWith('decision:')
      ? row.finding_id != null &&
        Boolean(decisionMembers.get(trackFilter.slice(9))?.has(row.finding_id))
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
  const matchingFilter = (row: AlbumAssignment) => matchesTrackFilter(row, trackFilter);
  const filterRows =
    trackView === 'release'
      ? releaseOrderedAssignments
      : [
          ...attentionAssignments,
          ...changeAssignments,
          ...missingAssignments,
          ...settledAssignments,
        ];
  const filterCount = (filter: string) =>
    filterRows.filter((row) => matchesTrackFilter(row, filter)).length;
  const activeDecision = active ? decisionsByFinding.get(active.id) : undefined;
  const activeAssignment = active ? assignmentsByFinding.get(active.id) : undefined;
  const primaryReadyDecision = readyDecisions.length === 1 ? readyDecisions[0] : undefined;
  const releaseCandidates = [
    ...(reviewCase?.album_plan?.candidates || records(record(active?.details).candidates)).filter(
      (candidate) => !manualRelease || candidate.release_id !== manualRelease.release_id,
    ),
    ...(manualRelease ? [manualRelease] : []),
  ] as Array<Record<string, unknown>>;
  const primaryCandidate = reviewCase?.album_plan?.candidates?.find(
    (candidate) =>
      candidate.source === reviewCase.album_plan?.source &&
      candidate.release_id === reviewCase.album_plan?.release_id,
  );
  const mappedSlots =
    primaryCandidate?.matched ?? assignments.filter((row) => row.slot_title).length;
  const totalSlots = primaryCandidate?.total_slots ?? mappedSlots;
  const settledSlots =
    (reviewCase?.album_plan?.counts.confirmed || 0) + (reviewCase?.album_plan?.counts.settled || 0);

  useEffect(() => {
    if (albumView !== 'tracks' || !reviewCase) return;
    if (activeRowKey && assignments.some((row) => assignmentKey(row) === activeRowKey)) return;
    const first =
      assignmentsByFinding.get(activeId) ||
      assignments.find((row) => row.finding_id && row.action_state !== 'waiting') ||
      assignments[0];
    if (first) {
      setActiveId(first.finding_id ?? null);
      setActiveRowKey(assignmentKey(first));
    }
  }, [albumView, reviewCase, activeRowKey, activeId, assignments]);

  const selectFinding = (findingId: number | null, rowKey?: string) => {
    setActiveId((current) => (current === findingId ? null : findingId));
    const target = assignmentsByFinding.get(findingId);
    setActiveRowKey(rowKey || (target ? assignmentKey(target) : null));
    if (target?.status === 'change')
      setOpenGroups((current) => (current.includes('changes') ? current : [...current, 'changes']));
  };

  const selectCase = (next: string) => {
    setNotice('');
    setInspectorOpen(true);
    setAlbumView('plan');
    setTrackFilter('');
    setTrackView('issues');
    setOpenGroups(['attention', 'changes']);
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
        assignmentsByFinding.get(first) ? assignmentKey(assignmentsByFinding.get(first)!) : null,
      );
      setOpenGroups(['attention', 'changes', 'missing', 'settled']);
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

  const selectAssignment = (findingId: number | null, rowKey: string) => {
    setActiveId(findingId);
    setActiveRowKey(rowKey);
    setInspectorOpen(true);
  };

  const selectedCaseRef = useRef(caseId);
  selectedCaseRef.current = caseId;
  const refreshError = useRef<string | null>(null);
  const refresh = useCallback(async () => {
    const requestedId = caseId;
    const requestVersion = ++caseRequestVersion.current;
    try {
      if (!requestedId) {
        await refreshQueue();
        return true;
      }
      const updated = await fetchReviewCase(requestedId);
      if (selectedCaseRef.current !== requestedId || requestVersion !== caseRequestVersion.current)
        return true;
      if (updated.case_id !== requestedId) {
        setCaseId(updated.case_id);
        return true;
      }
      // Poll the active album while it is being analyzed. Recounting the whole
      // library every 1.2 seconds adds latency without exposing a final result.
      if (updated.refresh?.state === 'running') {
        setReviewCase(updated);
        return true;
      }
      setReviewCase(updated);
      const updatedQueue = await refreshQueue();
      if (selectedCaseRef.current !== requestedId || requestVersion !== caseRequestVersion.current)
        return true;
      if (refreshError.current) {
        const previous = refreshError.current;
        setErrorValue((current) => (current === previous ? '' : current));
        refreshError.current = null;
      }
      if (
        updated.status === 'completed' &&
        !updated.album_plan?.assignments.some((row) => row.fulfillment_state) &&
        updated.refresh?.state !== 'error'
      ) {
        const previousIndex = queue?.items.findIndex((item) => item.case_id === requestedId) ?? 0;
        const following = queue?.items.slice(Math.max(0, previousIndex + 1)) || [];
        const next =
          following.find((item) =>
            updatedQueue.items.some((current) => current.case_id === item.case_id),
          ) ||
          updatedQueue.items[Math.max(0, previousIndex)] ||
          updatedQueue.items[0];
        setReviewCase(null);
        selectCase(next?.case_id || '');
        if (!next && updatedQueue.total > 0 && page > 0) setPage(page - 1);
        setNotice('Album review completed.');
      } else {
        setReviewCase(updated);
        if (updated.refresh?.state === 'error')
          setError(updated.refresh.error || 'Album refresh failed');
      }
      return true;
    } catch (cause) {
      if (selectedCaseRef.current === requestedId) {
        refreshError.current = (cause as Error).message;
        setError(refreshError.current);
      }
      return false;
    }
  }, [caseId, refreshQueue, queue, page]);
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    if (!caseId || reviewCase?.refresh?.state !== 'running') return;
    let pending = false;
    const timer = window.setInterval(() => {
      if (pending) return;
      pending = true;
      void refreshRef.current().finally(() => {
        pending = false;
      });
    }, 1200);
    return () => window.clearInterval(timer);
  }, [caseId, reviewCase?.refresh?.state]);

  const operationRef = useRef<string | null>(window.sessionStorage.getItem('review-operation'));
  const rememberOperation = (id?: string, albumCase?: string) => {
    setOperationCase(albumCase || null);
    if (albumCase) window.sessionStorage.setItem('review-operation-case', albumCase);
    else window.sessionStorage.removeItem('review-operation-case');
    operationRef.current = id || 'current';
    window.sessionStorage.setItem('review-operation', operationRef.current);
    setApplyBusy(true);
  };
  useEffect(() => {
    let live = true;
    let pending = false;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const requestedOperation = operationRef.current;
        const status = await fetchReviewApplyStatus(
          requestedOperation && requestedOperation !== 'current' ? requestedOperation : undefined,
        );
        if (!live || requestedOperation !== operationRef.current) return;
        if (!operationRef.current && status.running && status.review_approved) {
          operationRef.current = status.operation_id || 'current';
          window.sessionStorage.setItem('review-operation', operationRef.current);
        }
        if (!operationRef.current) return;
        const reportCase =
          status.review_scope === 'global'
            ? null
            : status.review_scope === 'album'
              ? status.review_case_ids?.[0] || null
              : operationCase;
        if (status.review_scope)
          setOperationCase(
            status.review_scope === 'album' ? status.review_case_ids?.[0] || null : null,
          );
        if (!status.operation_id && operationRef.current !== 'current') {
          setError(
            'The server no longer has this operation report. Review the refreshed album before retrying.',
            reportCase,
          );
        } else if (
          status.operation_id &&
          operationRef.current !== 'current' &&
          status.operation_id !== operationRef.current
        ) {
          setError(
            'Another operation has replaced this progress report. Refreshing the current album.',
            reportCase,
          );
        } else if (status.running) {
          setApplyBusy(true);
          setApplyProgress(
            status.phase === 'waiting_for_scan'
              ? 'Waiting for reconciliation to finish before applying…'
              : status.phase === 'queueing_organization'
                ? 'Queueing folder organization…'
                : status.phase === 'queueing_reanalysis'
                  ? 'Queueing album reanalysis…'
                  : `Processing ${status.done || 0} of ${status.total || 0} album actions…`,
          );
          return;
        } else if (status.error || status.failed || status.stopped) {
          setError(
            `${status.fixed || 0} completed; ${status.failed || 0} failed. ${status.error || status.errors?.[0]?.error || 'The operation stopped before completing.'}`,
            reportCase,
          );
        } else {
          const message = `${status.fixed || 0} album action${status.fixed === 1 ? '' : 's'} completed.${status.organize_queued ? ' Folder organization was queued.' : ''}${status.reconciliation_queued ? ' Updating the album plan…' : ''}`;
          const targetCase =
            status.review_scope === 'album' ? status.review_case_ids?.[0] : operationCase;
          if (status.review_scope === 'global' || !targetCase) setGlobalNotice(message);
          else if (selectedCaseRef.current === targetCase) setNotice(message);
        }
        operationRef.current = null;
        window.sessionStorage.removeItem('review-operation');
        window.sessionStorage.removeItem('review-operation-case');
        setApplyBusy(false);
        setApplyProgress('');
        await refreshRef.current();
      } catch {
        if (live && operationRef.current)
          setApplyProgress(
            'Progress temporarily unavailable. The server operation may still be running; reconnecting…',
          );
      } finally {
        pending = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), applyBusy ? 1000 : 10000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [applyBusy, operationCase]);

  const runScan = async () => {
    setScanBusy(true);
    setError('');
    try {
      await runReconciliationScan();
      setGlobalNotice('Library reconciliation queued. Progress will appear here.');
    } catch (cause) {
      setError((cause as Error).message, null);
    } finally {
      setScanBusy(false);
    }
  };

  const [organizationMoves, setOrganizationMoves] = useState<string[]>([]);
  useEffect(() => {
    setOrganizationMoves([]);
    if (
      !reviewCase?.album_id ||
      !reviewCase.plan?.can_organize ||
      !reviewCase.plan?.can_apply_ready
    )
      return;
    const controller = new AbortController();
    void fetch(
      `/api/library/album/${encodeURIComponent(String(reviewCase.album_id))}/reorganize/preview`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'tags' }),
        signal: controller.signal,
      },
    )
      .then((response) => response.json())
      .then((result) => {
        if (controller.signal.aborted || !result.success) return;
        const moves = (result.tracks || []).filter(
          (track: {
            file_exists?: boolean;
            collision?: boolean;
            matched?: boolean;
            current_path?: string;
            new_path?: string;
          }) =>
            track.file_exists &&
            !track.collision &&
            track.matched !== false &&
            track.new_path &&
            track.current_path?.split('/').slice(0, -1).join('/') !==
              track.new_path.split('/').slice(0, -1).join('/'),
        );
        setOrganizationMoves([
          ...new Set<string>(
            moves.map((track: { new_path: string }) =>
              track.new_path.split('/').slice(0, -1).join('/'),
            ),
          ),
        ]);
      })
      .catch(() => {
        /* An unavailable preview must not advertise a move. */
      });
    return () => controller.abort();
  }, [
    reviewCase?.case_id,
    reviewCase?.plan_signature,
    reviewCase?.plan?.can_organize,
    reviewCase?.plan?.can_apply_ready,
    reviewCase?.album_id,
  ]);

  const reanalyzeAlbum = async (refreshCatalogue = false) => {
    if (!reviewCase) return;
    const requestedId = reviewCase.case_id;
    const requestVersion = ++caseRequestVersion.current;
    setError('');
    setNotice('');
    setReanalyzingCase(requestedId);
    try {
      await reanalyzeReviewCase(requestedId, refreshCatalogue);
      const updated = await fetchReviewCase(requestedId);
      if (
        selectedCaseRef.current === requestedId &&
        requestVersion === caseRequestVersion.current
      ) {
        if (updated.case_id !== requestedId) setCaseId(updated.case_id);
        else setReviewCase(updated);
      }
    } catch (cause) {
      if (selectedCaseRef.current === requestedId) setError((cause as Error).message);
    } finally {
      setReanalyzingCase((current) => (current === requestedId ? null : current));
    }
  };

  const applyReady = async (scopeCaseId?: string, organize = false) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const response = await fetch(
        `/api/repair/review-queue/apply-ready${scopeCaseId ? `?case_id=${encodeURIComponent(scopeCaseId)}` : ''}`,
      );
      if (!response.ok) throw new Error('Could not load the ready album plans.');
      const preview = (await response.json()) as {
        signature: string;
        action_count: number;
        file_actions: number;
        albums: Array<{ album: string; actions: Array<{ description: string }> }>;
      };
      if (!preview.action_count)
        throw new Error('No fully decided album plans are ready to apply.');
      const message = (
        <div className={styles.applyPreview}>
          <p>
            Apply {preview.action_count} planned actions across {preview.albums.length} fully
            decided album{preview.albums.length === 1 ? '' : 's'}?
          </p>
          {preview.file_actions > 0 && (
            <Notice tone="warning">
              {preview.file_actions} file action{preview.file_actions === 1 ? ' is' : 's are'}{' '}
              included. Files moved to quarantine remain recoverable.
            </Notice>
          )}
          {preview.albums.map((album, index) => (
            <details key={`${album.album}-${index}`} open={Boolean(scopeCaseId)}>
              <summary>
                {album.album} · {album.actions.length} action{album.actions.length === 1 ? '' : 's'}
              </summary>
              <ul>
                {album.actions.map((action, i) => (
                  <li key={i}>{action.description}</li>
                ))}
              </ul>
            </details>
          ))}
          {organize && <p>Then organize into: {organizationMoves.join(', ')}</p>}
          <p>
            Albums with open decisions are excluded. These plans will be checked again before
            execution. If a plan changes, review a fresh preview before applying.
          </p>
        </div>
      );
      const confirmed = await confirm({
        title: 'Apply ready album plans',
        message,
        destructive: preview.file_actions > 0,
        confirmText: 'Apply plans',
      });
      if (!confirmed) return;
      setApplyBusy(true);
      const result = await fetch('/api/repair/review-queue/apply-ready', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ case_id: scopeCaseId, organize, signature: preview.signature }),
      });
      const started = await result.json();
      if (!started.started) throw new Error(started.error || 'Could not start album changes');
      rememberOperation(started.operation_id, scopeCaseId);
    } catch (cause) {
      setApplyBusy(false);
      setApplyProgress('');
      setError((cause as Error).message, scopeCaseId || null);
    } finally {
      setBusy(false);
    }
  };

  const requestAllMissing = async (missingCount: number, findingIds?: number[]) => {
    if (!reviewCase || !missingCount) return;
    const confirmed = await confirm({
      title: 'Request missing album tracks',
      message: `Request ${missingCount === 1 ? 'the missing track' : `${findingIds ? 'the selected' : 'all'} ${missingCount} missing tracks`} from the selected release? ${missingCount === 1 ? 'It will' : 'They will'} be added to the wishlist.`,
      confirmText: `Request ${missingCount} track${missingCount === 1 ? '' : 's'}`,
    });
    if (!confirmed) return;
    setApplyBusy(true);
    setError('');
    setApplyProgress(`Requesting 0 of ${missingCount} missing tracks…`);
    try {
      const started = await startMissingTrackRequests(reviewCase.case_id, findingIds);
      if (!started.started) throw new Error(started.error || 'Could not request missing tracks');
      rememberOperation(started.operation_id, reviewCase.case_id);
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
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await applyReviewDecision(caseId, finding, action, fixAction);
      if (!result.success && !result.partial) {
        await refresh();
        throw new Error(result.error || 'The action did not complete');
      }
      if (result.partial)
        setError(
          `Partially completed: ${result.error || 'Review the updated album before retrying.'}`,
        );
      else
        setNotice(
          `${result.message || (action === 'dismiss' ? 'Accepted as-is' : 'Repair applied')}${result.refresh_queued ? ' Updating this album plan…' : ''}`,
        );
      await refresh();
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

  const pinCandidate = async (index: number) => {
    if (!reviewCase?.album_plan) return;
    const candidate = releaseCandidates[index];
    if (!candidate) return;
    const current = reviewCase.album_plan;
    setBusy(true);
    setError('');
    try {
      const result = await pinReviewRelease(
        reviewCase.case_id,
        { source: String(candidate.source), release_id: String(candidate.release_id) },
        current,
        { manual: candidate === manualRelease },
      );
      if (!result.success) throw new Error(result.error || 'Could not select this release');
      setNotice(result.message || 'Release selected; updating this album plan');
      const updated = await fetchReviewCase(reviewCase.case_id);
      setReviewCase(updated);
      setManualRelease(null);
      setPreviewCandidate(
        Math.max(
          0,
          updated.album_plan?.candidates?.findIndex(
            (item) =>
              item.source === updated.album_plan?.source &&
              item.release_id === updated.album_plan?.release_id,
          ) ?? 0,
        ),
      );
      await refreshQueue();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const inspectReleaseId = async () => {
    if (!reviewCase?.album_plan) return;
    const id =
      releaseIdInput.trim().match(/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}/)?.[0] ||
      '';
    if (!id) {
      setError('Enter a MusicBrainz release MBID or release URL.');
      return;
    }
    setBusy(true);
    setError('');
    setManualRelease(null);
    try {
      const result = await pinReviewRelease(
        reviewCase.case_id,
        { source: 'musicbrainz', release_id: id },
        reviewCase.album_plan,
        { preview: true },
      );
      if (!result.success) throw new Error(result.error || 'Could not load this release');
      if (!result.candidate) throw new Error('The release comparison could not be loaded');
      setManualRelease(result.candidate);
      setPreviewCandidate(
        (reviewCase.album_plan.candidates || []).filter((candidate) => candidate.release_id !== id)
          .length,
      );
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const actGroup = async (decision: ReviewDecision, action: 'fix' | 'dismiss' | 'reset') => {
    setBusy(true);
    setBusyDecision(decision.category);
    setError('');
    try {
      const result = await applyReviewDecisionGroup(caseId, decision.category, action);
      if (result.started) {
        rememberOperation(result.operation_id);
        return;
      }
      if (!result.success) {
        setError(
          `${result.applied == null ? 'The operation outcome could not be confirmed.' : `${result.applied} of ${result.total || decision.action_count} changes completed.`} ${result.error || 'Review the current operation status before retrying.'}`,
        );
      } else {
        setNotice('Album plan saved. Apply it when you are ready.');
      }
      if (!(await refresh()) && result.success) {
        setError(
          'The decision was handled, but the updated album plan could not be loaded. Refresh the queue.',
        );
      }
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
      setBusyDecision('');
    }
  };

  const reconsiderScope = async () => {
    if (!reviewCase) return;
    setBusy(true);
    setNotice('');
    setError('');
    try {
      await resetAlbumScope(reviewCase.case_id);
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const readyActionControls =
    reviewCase && plan.can_apply_ready ? (
      <div className={styles.inlineActions}>
        <Button
          type="button"
          variant="primary"
          className={styles.primary}
          disabled={mutationBusy}
          onClick={() => void applyReady(reviewCase.case_id)}
        >
          Apply {plan.ready} ready change
          {plan.ready === 1 ? '' : 's'}
        </Button>
        {plan.can_organize &&
          organizationMoves.length > 0 &&
          reviewMetadataCount === 0 &&
          reviewCase.album_id != null && (
            <Button
              type="button"
              disabled={mutationBusy}
              onClick={() => void applyReady(reviewCase.case_id, true)}
            >
              Apply ready edits, then organize folder
            </Button>
          )}
      </div>
    ) : null;

  const readySummaryList =
    readyChangeGroups.length > 0 ? (
      <div className={styles.readySummaryList} aria-label="Ready change summary">
        {readyChangeGroups.map((group) => (
          <Button
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
          </Button>
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
  const comparisonDurationMs = assignmentsByPath.get(comparisonPath)?.file_duration_ms || 0;

  const actionDetail = (compact = false) =>
    active && reviewCase ? (
      <SelectedActionDetail
        key={active.id}
        finding={active}
        itemIndex={itemIndex}
        itemCount={reviewCase.count}
        typeInfo={typeInfo}
        busy={mutationBusy}
        onAct={(action, choice) => void act(active, action, choice)}
        onSelect={selectFinding}
        previousId={reviewCase.items[itemIndex - 1]?.id}
        nextId={reviewCase.items[itemIndex + 1]?.id}
        comparisonPath={comparisonPath || undefined}
        comparisonDurationMs={comparisonDurationMs}
        compact={compact}
        numberingPreview={activeAssignment?.numbering_preview}
      />
    ) : null;
  const selectedActionDetail = actionDetail();
  const compactSelectedActionDetail = actionDetail(true);

  const renderAssignmentRow = (row: AlbumAssignment, index: number) => {
    const key = assignmentKey(row);
    const selected = key === activeRowKey;
    return (
      <AssignmentRow
        key={`${key}-${index}`}
        row={row}
        selected={selected}
        numberingMode={reviewCase?.album_plan?.numbering_mode}
        onSelect={(findingId, rowKey) => {
          trackListScroll.current = [];
          let scrollElement = document.querySelector<HTMLElement>(
            '[aria-label="Complete album track assignment"]',
          );
          while (scrollElement) {
            trackListScroll.current.push({ element: scrollElement, top: scrollElement.scrollTop });
            scrollElement = scrollElement.parentElement;
          }
          selectAssignment(findingId, rowKey);
          requestAnimationFrame(() => {
            const inspector = inspectorRef.current;
            if (inspector && getComputedStyle(inspector).position === 'static') {
              inspector.focus();
              inspector.scrollIntoView({ block: 'start' });
            }
          });
        }}
      />
    );
  };

  return (
    <main className={`page-shell review-queue-root ${styles.page}`}>
      {dialog}
      <a href="/tools" className={styles.back}>
        ← Library Maintenance
      </a>
      <PageHeader
        title="Review Queue"
        subtitle={
          <>
            Review album plans, apply supported changes, and resolve exceptions.
            {lastRun?.status === 'completed' &&
              scanStatus?.current_job?.job_id !== 'library_reconciliation' && (
                <span className={styles.scanHistory}>
                  Last reconciliation: {lastRun.items_scanned} files checked.
                </span>
              )}
          </>
        }
        actions={
          <div className={styles.headerTools}>
            <div className={styles.headerCount}>
              <strong>
                {queueTotals || queue
                  ? `${queueTotals?.all ?? queue?.total} album${(queueTotals?.all ?? queue?.total) === 1 ? '' : 's'}${query || category ? ' in this view' : ''}`
                  : 'Loading queue…'}
              </strong>
              <span>
                {queueTotals?.ready_albums_total != null
                  ? `${queueTotals.ready_albums_total} fully decided album${queueTotals.ready_albums_total === 1 ? '' : 's'} · `
                  : ''}
                {queue
                  ? `${queueTotals?.ready_total ?? queue.ready_total ?? 0} ready changes across the library`
                  : 'Loading ready changes…'}
              </span>
            </div>
            <Button
              type="button"
              onClick={() => {
                setQueueMode('ready');
                setPage(0);
                setCaseId('');
              }}
            >
              Review ready batch
            </Button>
            <Button
              type="button"
              variant="primary"
              className={styles.safeAction}
              disabled={mutationBusy || !(queueTotals?.ready_total ?? queue?.ready_total)}
              onClick={() => void applyReady()}
            >
              {applyBusy && !operationCase
                ? applyProgress || 'Applying…'
                : queue
                  ? `Apply ready changes (${queueTotals?.ready_total ?? queue.ready_total ?? 0})`
                  : 'Loading ready changes…'}
            </Button>
            <div className={styles.inlineActions} aria-label="Queue actions">
              <Button
                type="button"
                disabled={scanBusy || scanStatus?.current_job?.job_id === 'library_reconciliation'}
                onClick={() => {
                  void runScan();
                }}
              >
                {scanBusy
                  ? 'Queuing…'
                  : scanStatus?.current_job?.job_id === 'library_reconciliation'
                    ? 'Scanning…'
                    : 'Scan library'}
              </Button>
              <Button
                type="button"
                disabled={reloadBusy}
                onClick={() => {
                  setReloadBusy(true);
                  setGlobalNotice('Reloading queue…');
                  void refresh()
                    .then((ok) => {
                      if (ok) setGlobalNotice('Queue updated.');
                    })
                    .finally(() => setReloadBusy(false));
                }}
              >
                {reloadBusy ? 'Reloading…' : 'Reload queue'}
              </Button>
            </div>
          </div>
        }
      />

      {error && (!errorCase || !reviewCase) && (
        <Notice tone="danger" role="alert" aria-label="Queue error">
          {error}
        </Notice>
      )}
      {globalNotice && (
        <Notice tone="info" className={styles.albumNotice} role="status">
          <span>{globalNotice}</span>
          <Button
            type="button"
            aria-label="Dismiss queue notice"
            onClick={() => setGlobalNotice('')}
          >
            ×
          </Button>
        </Notice>
      )}
      {notice && !reviewCase && (
        <div className={styles.notice} role="status">
          {notice}
        </div>
      )}
      {applyBusy && !operationCase && (
        <Notice tone="info" className={styles.applyBanner} role="status" aria-live="polite">
          <span className={styles.loadingSpinner} aria-hidden="true" />
          <strong>{applyProgress || 'Applying album changes…'}</strong>
          <span>This continues on the server. You can leave this page and return.</span>
        </Notice>
      )}
      {(scanStatus?.current_job?.job_id === 'library_reconciliation' ||
        (lastRun && lastRun.status !== 'completed')) && (
        <div
          className={styles.scanStatus}
          role="status"
          data-running={scanStatus?.current_job?.job_id === 'library_reconciliation' || undefined}
        >
          {scanStatus?.current_job?.job_id === 'library_reconciliation' && (
            <>
              <span className={styles.loadingSpinner} aria-hidden="true" />
              <progress
                aria-label="Library reconciliation progress"
                max={100}
                value={
                  scanStatus.current_job.progress?.total
                    ? scanStatus.current_job.progress?.percent || 0
                    : undefined
                }
              />
            </>
          )}
          {scanStatus?.current_job?.job_id === 'library_reconciliation'
            ? !scanStatus.current_job.progress?.total
              ? 'Discovering album folders…'
              : `Reconciliation running: ${scanStatus.current_job.progress?.scanned ?? 0} of ${scanStatus.current_job.progress?.total ?? '?'} folders (${scanStatus.current_job.progress?.percent ?? 0}%).`
            : lastRun?.status === 'completed'
              ? `Last reconciliation completed: ${lastRun.items_scanned} files checked.`
              : `Last reconciliation ${lastRun?.status}: ${lastRun?.error_text || 'No reason recorded'}. ${lastRun?.items_scanned ?? 0} files checked; a rerun reuses cached fingerprints.`}
        </div>
      )}

      <div className={styles.workspace}>
        <aside className={styles.sidebar} aria-label="Reconciliation albums">
          <nav className={styles.queueModes} aria-label="Queue view">
            {queueModes.map((mode) => (
              <Button
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
              </Button>
            ))}
          </nav>
          <label htmlFor="review-search">Find album or track</label>
          <TextInput
            id="review-search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Search findings"
          />
          <label className={styles.categoryFilter}>
            Filter by type
            <Select
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
            </Select>
          </label>
          <div className={styles.caseList} ref={caseListRef} aria-busy={!queue}>
            {!queue && <p role="status">Loading albums…</p>}
            {queue?.items.map((item) => (
              <Button
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
              </Button>
            ))}
            {queue && queue.total === 0 && (
              <div className={styles.queueEmpty}>
                <strong>No albums in this view</strong>
                <p>
                  {category
                    ? `There are no ${categoryOptions.find(([value]) => value === category)?.[1].toLowerCase() || 'matching'} albums in ${queueModes.find((mode) => mode.value === queueMode)?.label.toLowerCase()}.`
                    : 'No albums match the current search and queue view.'}
                </p>
                {queueMode !== 'all' && (
                  <Button
                    type="button"
                    onClick={() => {
                      setQueueMode('all');
                      setPage(0);
                    }}
                  >
                    Show all {category ? 'albums of this type' : 'albums'}
                  </Button>
                )}
                {query && (
                  <Button
                    type="button"
                    onClick={() => {
                      setSearchInput('');
                      setQuery('');
                    }}
                  >
                    Clear search
                  </Button>
                )}
              </div>
            )}
          </div>
          <div className={styles.pagination}>
            <Button
              type="button"
              disabled={page === 0}
              onClick={() => {
                setCaseId('');
                setPage(page - 1);
              }}
            >
              Previous
            </Button>
            <span className={styles.paginationCenter}>
              <span>
                Page {page + 1} of {pageCount}
              </span>
              <Select
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
              </Select>
            </span>
            <Button
              type="button"
              disabled={!queue || (page + 1) * queue.limit >= queue.total}
              onClick={() => {
                setCaseId('');
                setPage(page + 1);
              }}
            >
              Next
            </Button>
          </div>
        </aside>

        <section
          className={styles.review}
          aria-label="Current review case"
          data-refresh={reviewCase?.refresh?.state === 'running' ? 'running' : undefined}
        >
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
                      {albumNumber === null
                        ? 'Album review'
                        : `Album ${albumNumber} of ${queue?.total ?? '—'}`}
                      {' · '}
                      {reviewCase.count} pending action
                      {reviewCase.count === 1 ? '' : 's'}
                    </div>
                    <h2>{reviewCase.album || 'Unassigned files'}</h2>
                    <p>{reviewCase.artist || 'Artist not established'}</p>
                  </div>
                </div>
                <div className={styles.nav}>
                  <Button
                    type="button"
                    disabled={mutationBusy}
                    onClick={() => void reanalyzeAlbum()}
                  >
                    Reanalyze album
                  </Button>
                  <ActionMenu
                    trigger={(props) => (
                      <Button {...props} variant="ghost" aria-label="Album actions">
                        •••
                      </Button>
                    )}
                    items={[
                      {
                        key: 'catalogue',
                        label: 'Refresh catalog evidence',
                        disabled: mutationBusy,
                        onSelect: () => void reanalyzeAlbum(true),
                      },
                    ]}
                  />
                  <Button
                    type="button"
                    disabled={caseIndex <= 0}
                    onClick={() => selectCase(queue!.items[caseIndex - 1].case_id)}
                  >
                    ← Previous album
                  </Button>
                  <Button
                    type="button"
                    disabled={!queue || caseIndex < 0 || caseIndex >= queue.items.length - 1}
                    onClick={() => selectCase(queue!.items[caseIndex + 1].case_id)}
                  >
                    Next album →
                  </Button>
                </div>
              </div>

              {error && errorCase === reviewCase.case_id && (
                <Notice tone="danger" role="alert">
                  {error}
                </Notice>
              )}
              <nav
                className={styles.albumViews}
                role="tablist"
                aria-label="Album review views"
                onKeyDown={(event) => {
                  const views: AlbumView[] = ['plan', 'release', 'tracks'];
                  const index = views.indexOf(albumView);
                  const next =
                    event.key === 'ArrowRight'
                      ? views[(index + 1) % 3]
                      : event.key === 'ArrowLeft'
                        ? views[(index + 2) % 3]
                        : event.key === 'Home'
                          ? views[0]
                          : event.key === 'End'
                            ? views[2]
                            : null;
                  if (next) {
                    event.preventDefault();
                    setAlbumView(next);
                    document.getElementById(`review-tab-${next}`)?.focus();
                  }
                }}
              >
                <Button
                  type="button"
                  role="tab"
                  id="review-tab-plan"
                  aria-controls="review-tab-panel"
                  aria-selected={albumView === 'plan'}
                  tabIndex={albumView === 'plan' ? 0 : -1}
                  aria-current={albumView === 'plan' ? 'page' : undefined}
                  onClick={() => setAlbumView('plan')}
                >
                  Plan
                  <span>{reviewDecisions.length || plan.ready}</span>
                </Button>
                <Button
                  type="button"
                  role="tab"
                  id="review-tab-release"
                  aria-controls="review-tab-panel"
                  aria-selected={albumView === 'release'}
                  tabIndex={albumView === 'release' ? 0 : -1}
                  aria-current={albumView === 'release' ? 'page' : undefined}
                  onClick={() => setAlbumView('release')}
                >
                  Release
                </Button>
                <Button
                  type="button"
                  role="tab"
                  id="review-tab-tracks"
                  aria-controls="review-tab-panel"
                  aria-selected={albumView === 'tracks'}
                  tabIndex={albumView === 'tracks' ? 0 : -1}
                  aria-current={albumView === 'tracks' ? 'page' : undefined}
                  onClick={() => setAlbumView('tracks')}
                >
                  Tracks
                  <span>{assignments.length}</span>
                </Button>
              </nav>

              {applyBusy && operationCase === reviewCase.case_id && (
                <Notice tone="info" className={styles.applyBanner} role="status" aria-live="polite">
                  <span className={styles.loadingSpinner} aria-hidden="true" />
                  <strong>{applyProgress || 'Applying this album plan…'}</strong>
                  <span>This continues on the server. You can leave this page and return.</span>
                </Notice>
              )}
              {notice && !reanalyzing && reviewCase.refresh?.state !== 'running' && (
                <Notice tone="info" className={styles.albumNotice} role="status" aria-live="polite">
                  <span>{notice}</span>
                  <Button type="button" aria-label="Dismiss notice" onClick={() => setNotice('')}>
                    ×
                  </Button>
                </Notice>
              )}
              {!(applyBusy && operationCase === reviewCase.case_id) &&
                (reanalyzing || reviewCase.refresh?.state === 'running') && (
                  <Notice tone="info" className={styles.refreshBanner} role="status">
                    <span className={styles.loadingSpinner} aria-hidden="true" /> Reanalyzing this
                    album. The previous decisions remain visible while dependent track actions are
                    recalculated.
                  </Notice>
                )}

              {albumView === 'tracks' && plan.can_apply_ready && (
                <Notice tone="info" className={styles.albumNotice} role="status">
                  <span>All decisions are planned. This album is ready to apply.</span>
                  <Button type="button" onClick={() => setAlbumView('plan')}>
                    Return to plan →
                  </Button>
                </Notice>
              )}
              <div className={styles.tabWorkspace} data-view={albumView}>
                <div
                  className={styles.tabMain}
                  id="review-tab-panel"
                  role="tabpanel"
                  aria-labelledby={`review-tab-${albumView}`}
                >
                  {albumView === 'plan' &&
                    reviewCase.items.some((item) => item.details?.planned_choice) && (
                      <details className={styles.editPlan}>
                        <summary>Edit saved choices</summary>
                        <div>
                          <strong>Choices saved in this album plan</strong>
                          <p>
                            No file changes have been applied. You can change these choices before
                            applying.
                          </p>
                        </div>
                        <div className={styles.inlineActions}>
                          {[
                            ...new Set(
                              reviewCase.items.flatMap((item) => {
                                const choice = item.details?.planned_choice as
                                  | { category?: string }
                                  | undefined;
                                return choice?.category ? [choice.category] : [];
                              }),
                            ),
                          ].map((category) => (
                            <Button
                              key={category}
                              type="button"
                              disabled={mutationBusy}
                              onClick={() => void actGroup({ category } as ReviewDecision, 'reset')}
                            >
                              Review {category.replaceAll('_', ' ')} again
                            </Button>
                          ))}
                        </div>
                      </details>
                    )}

                  {albumView === 'plan' && plan.can_apply_ready && (
                    <section className={styles.albumApplyAll} aria-label="Apply album changes">
                      <div>
                        <strong>
                          Plan ready to apply · {plan.ready} action{plan.ready === 1 ? '' : 's'}
                        </strong>
                        <p>
                          Your choices are saved automatically. Apply this album now, or include it
                          in the next ready batch.
                        </p>
                      </div>
                      {readyActionControls}
                    </section>
                  )}

                  {albumView === 'plan' && (reviewCase.album_plan || decisions.length > 0) && (
                    <section className={styles.decisionBoard} aria-label="Album decisions">
                      <div className={styles.sectionHeading}>
                        <div>
                          <div className={styles.eyebrow}>Album review</div>
                          <h3>
                            {reviewDecisions.length
                              ? `${reviewDecisions.length} choice${reviewDecisions.length === 1 ? '' : 's'} need${reviewDecisions.length === 1 ? 's' : ''} your input`
                              : 'Decisions complete'}
                          </h3>
                          <p>Earlier choices update the proposals that follow.</p>
                        </div>
                      </div>
                      <div className={styles.decisionGrid}>
                        {!releaseChoicePending && reviewCase.album_plan && (
                          <article data-state="complete">
                            <Button
                              className={styles.decisionSelect}
                              type="button"
                              onClick={() => setAlbumView('release')}
                            >
                              <span className={styles.decisionStep} aria-hidden="true">
                                ✓
                              </span>
                              <span>
                                <small>Release</small>
                                <strong>
                                  Release selected · {mappedSlots}/{totalSlots} assigned
                                </strong>
                                <small>
                                  {reviewCase.album_plan.counts.missing
                                    ? `${reviewCase.album_plan.counts.missing} release tracks are absent · compare editions →`
                                    : 'Compare editions →'}
                                </small>
                              </span>
                            </Button>
                          </article>
                        )}
                        {!releaseChoicePending &&
                          !reviewDecisions.some((step) => step.category === 'numbering') && (
                            <article data-state="complete">
                              <div className={styles.decisionSelect}>
                                <span className={styles.decisionStep} aria-hidden="true">
                                  ✓
                                </span>
                                <span>
                                  <small>Numbering</small>
                                  <strong>
                                    {numberingSchemeLabel(
                                      releaseHasMultipleDiscs,
                                      reviewCase.album_plan?.numbering_mode,
                                    )}
                                  </strong>
                                </span>
                              </div>
                            </article>
                          )}
                        {reviewDecisions.map((decision, decisionIndex) => {
                          const selected = active
                            ? decision.finding_ids.includes(active.id)
                            : false;
                          return (
                            <Fragment key={decision.id}>
                              <article
                                data-risk={decision.risk}
                                data-state={
                                  releaseChoicePending && decision.category !== 'release_conflicts'
                                    ? 'pending'
                                    : 'needs-review'
                                }
                                data-selected={selected || undefined}
                              >
                                <Button
                                  type="button"
                                  className={styles.decisionSelect}
                                  onClick={() => openDecision(decision)}
                                >
                                  <span className={styles.decisionStep} aria-hidden="true">
                                    {decisionIndex + 1}
                                  </span>
                                  <span>
                                    <small>{decisionCategoryLabel(decision.category)}</small>
                                    <strong>
                                      {decisionInstruction(
                                        decision,
                                        reviewCase.album_plan?.numbering_mode,
                                        releaseHasMultipleDiscs,
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
                                </Button>
                                {['numbering', 'title_conventions', 'metadata'].includes(
                                  decision.category,
                                ) && (
                                  <div className={styles.decisionActions}>
                                    <Button
                                      type="button"
                                      disabled={
                                        busy ||
                                        applyBusy ||
                                        releaseChoicePending ||
                                        reviewCase.refresh?.state === 'running'
                                      }
                                      variant="primary"
                                      onClick={() => void actGroup(decision, 'fix')}
                                    >
                                      {busyDecision === decision.category
                                        ? `Saving ${decision.action_count} choices…`
                                        : decision.category === 'numbering'
                                          ? !releaseHasMultipleDiscs
                                            ? `Plan release track numbers for ${decision.action_count} track${decision.action_count === 1 ? '' : 's'}`
                                            : reviewCase.album_plan?.numbering_mode === 'digital'
                                              ? `Plan continuous numbering for ${decision.action_count} track${decision.action_count === 1 ? '' : 's'}`
                                              : `Plan release numbering for ${decision.action_count} track${decision.action_count === 1 ? '' : 's'}`
                                          : decision.category === 'title_conventions'
                                            ? 'Use proposed title conventions'
                                            : decision.label === 'Confirm album artist'
                                              ? 'Use proposed album artist'
                                              : `Use ${decision.action_count} proposed changes`}
                                    </Button>
                                    <Button
                                      type="button"
                                      disabled={
                                        busy ||
                                        applyBusy ||
                                        releaseChoicePending ||
                                        reviewCase.refresh?.state === 'running'
                                      }
                                      onClick={() => void actGroup(decision, 'dismiss')}
                                    >
                                      {decision.category === 'numbering'
                                        ? 'Keep current numbering'
                                        : decision.label === 'Confirm album artist'
                                          ? 'Keep current album artist'
                                          : 'Keep current values'}
                                    </Button>
                                  </div>
                                )}
                                {busyDecision === decision.category && (
                                  <p className={styles.decisionProgress} role="status">
                                    <span className={styles.loadingSpinner} aria-hidden="true" />
                                    Saving your choices in the album plan…
                                  </p>
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
                                        busy={mutationBusy}
                                        onChoose={(choice) => void act(intent, 'fix', choice)}
                                      />
                                    ) : (
                                      <div className={styles.decisionActions}>
                                        <Button
                                          type="button"
                                          disabled={
                                            applyBusy ||
                                            busy ||
                                            reviewCase.refresh?.state === 'running'
                                          }
                                          onClick={() =>
                                            void requestAllMissing(decision.action_count)
                                          }
                                        >
                                          Request{' '}
                                          {decision.action_count === 1
                                            ? 'the missing track'
                                            : `all ${decision.action_count} missing tracks`}
                                        </Button>
                                        <Button
                                          onClick={() => {
                                            setAlbumView('tracks');
                                            setTrackFilter('missing');
                                          }}
                                        >
                                          Choose individual tracks →
                                        </Button>
                                        <small>
                                          Each requested track is added to the wishlist for the
                                          selected release.
                                        </small>
                                      </div>
                                    );
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
                                <div className={styles.releaseStepLink}>
                                  <p>
                                    Compare the available editions before choosing this album's
                                    track list.
                                  </p>
                                  <Button
                                    type="button"
                                    className={styles.secondaryButton}
                                    onClick={() => setAlbumView('release')}
                                  >
                                    Compare releases →
                                  </Button>
                                </div>
                              )}
                            </Fragment>
                          );
                        })}
                        {!releaseChoicePending &&
                          !reviewDecisions.some((step) =>
                            [
                              'audio_conflicts',
                              'duplicates',
                              'extra_files',
                              'title_issues',
                            ].includes(step.category),
                          ) && (
                            <article data-state="complete">
                              <Button
                                className={styles.decisionSelect}
                                type="button"
                                onClick={() => setAlbumView('tracks')}
                              >
                                <span className={styles.decisionStep} aria-hidden="true">
                                  ✓
                                </span>
                                <span>
                                  <small>Track assignments</small>
                                  <strong>No assignment decisions remain</strong>
                                  <small>Inspect all local files and release tracks →</small>
                                </span>
                              </Button>
                            </article>
                          )}
                        {albumView === 'plan' && pendingDecisions.length > 0 && (
                          <section
                            className={styles.pendingFulfillment}
                            aria-label="Missing tracks being acquired"
                          >
                            <StatusChip tone="info" icon="info">
                              {wishlistedCount} requested
                            </StatusChip>
                            <div>
                              <strong>Waiting for missing tracks</strong>
                              <p>
                                {wishlistedCount === 1
                                  ? 'The missing track is'
                                  : 'These missing tracks are'}{' '}
                                already requested. Published downloads are checked automatically;
                                the album stays here until those files are assigned.
                              </p>
                              <a
                                className={styles.secondaryButton}
                                href={`/wishlist?q=${encodeURIComponent(reviewCase?.album || '')}`}
                              >
                                View album in Wishlist →
                              </a>
                            </div>
                          </section>
                        )}

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

                            {active &&
                              activeDecision?.risk === 'ready' &&
                              !activeAssignment &&
                              selectedActionDetail}
                          </section>
                        )}
                      </div>
                    </section>
                  )}

                  {albumView === 'plan' &&
                    plan.status !== 'ready' &&
                    plan.ready > 0 &&
                    !readyIncludedInGroup && (
                      <section
                        className={`${styles.albumPlan} ${styles.safeChangesPanel}`}
                        data-state="supporting"
                        aria-label="Independent safe changes"
                      >
                        <div className={styles.planSummary}>
                          <div>
                            <div className={styles.eyebrow}>Safe changes</div>
                            <strong>
                              {plan.ready} independent change{plan.ready === 1 ? ' is' : 's are'}{' '}
                              ready
                            </strong>
                            <p>
                              These edits are supported. Resolve the remaining album decisions
                              before applying the plan.
                            </p>
                          </div>
                          <div className={styles.planBadges}>
                            <StatusChip tone="good" icon="check">
                              {plan.ready} ready
                            </StatusChip>
                          </div>
                        </div>
                        {readySummaryList}
                        <Button
                          type="button"
                          className={styles.secondaryButton}
                          onClick={() => {
                            setTrackFilter('change');
                            setAlbumView('tracks');
                          }}
                        >
                          Inspect proposed track edits →
                        </Button>

                        {active &&
                          activeDecision?.risk === 'ready' &&
                          !activeAssignment &&
                          selectedActionDetail}
                      </section>
                    )}

                  {albumView === 'plan' &&
                    reviewDecisions.length === 0 &&
                    pendingDecisions.length === 0 &&
                    plan.ready === 0 && (
                      <div className={styles.empty}>No album-level action is pending.</div>
                    )}

                  {albumView === 'tracks' && assignments.length > 0 && (
                    <div className={styles.trackWorkspace} data-detail={inspectorOpen || undefined}>
                      <section
                        className={styles.trackMatrix}
                        aria-label="Complete album track assignment"
                      >
                        <div className={styles.sectionHeading}>
                          <div>
                            <div className={styles.eyebrow}>Album track list</div>
                            <h3>Track assignments</h3>
                            <p>
                              Compare each local file with the selected release slot and proposed
                              result.
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
                            {wishlistedCount > 0 && (
                              <StatusChip tone="info" icon="info">
                                {wishlistedCount} wishlisted
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
                              {numberingSchemeLabel(
                                releaseHasMultipleDiscs,
                                reviewCase.album_plan.numbering_mode,
                              )}
                            </span>
                          )}
                        </div>
                        {pairedConflictCount > 0 && (
                          <p className={styles.versionExplanation}>
                            {pairedConflictCount} version conflict
                            {pairedConflictCount === 1 ? '' : 's'} pair a local file with an
                            unmatched release slot. Each pair contributes one extra file and one
                            missing slot to the counts above.
                          </p>
                        )}
                        <div className={styles.trackFilters}>
                          <div
                            className={styles.trackViewSwitch}
                            role="group"
                            aria-label="Track arrangement"
                          >
                            <Button
                              type="button"
                              aria-pressed={trackView === 'issues'}
                              onClick={() => setTrackView('issues')}
                            >
                              Group by action
                            </Button>
                            <Button
                              type="button"
                              aria-pressed={trackView === 'release'}
                              onClick={() => setTrackView('release')}
                            >
                              Release order
                            </Button>
                          </div>
                          <label>
                            Show tracks
                            <Select
                              value={trackFilter}
                              onChange={(event) => setTrackFilter(event.target.value)}
                            >
                              <option value="">All tracks ({filterCount('')})</option>
                              {trackFilter.startsWith('decision:') && (
                                <option value={trackFilter}>
                                  {decisions.find(
                                    (decision) => `decision:${decision.id}` === trackFilter,
                                  )?.label || 'Selected decision'}{' '}
                                  ({filterCount(trackFilter)})
                                </option>
                              )}
                              <option value="uncertain">
                                Uncertain assignments ({filterCount('uncertain')})
                              </option>
                              <option value="duplicate">
                                Duplicate files ({filterCount('duplicate')})
                              </option>
                              <option value="extra">
                                Unassigned files ({filterCount('extra')})
                              </option>
                              <option value="change">
                                All proposed changes ({filterCount('change')})
                              </option>
                              <option value="settled">
                                Settled tracks ({filterCount('settled')})
                              </option>
                              {proposedChangeGroups.map((group) => (
                                <option value={group.key} key={group.key}>
                                  {group.label} ({filterCount(group.key)})
                                </option>
                              ))}
                              {missingAssignments.length > 0 && (
                                <option value="missing">Missing ({filterCount('missing')})</option>
                              )}
                              {pairedConflictCount > 0 && (
                                <option value="version_conflict">
                                  Version conflicts ({filterCount('version_conflict')})
                                </option>
                              )}
                            </Select>
                          </label>
                          {trackFilter && (
                            <Button
                              type="button"
                              className={styles.secondaryButton}
                              onClick={() => setTrackFilter('')}
                            >
                              Clear filter
                            </Button>
                          )}
                        </div>
                        {missingAssignments.length > 0 &&
                          !releaseChoicePending &&
                          (reviewCase.items.some(
                            (item) => item.finding_type === 'reconciliation_intent',
                          ) ? (
                            <section
                              className={styles.missingChoices}
                              aria-label="Choose album completeness"
                              tabIndex={-1}
                            >
                              <h4>Choose which missing tracks you want</h4>
                              <CompletenessChoice
                                key={reviewCase.case_id}
                                finding={
                                  reviewCase.items.find(
                                    (item) => item.finding_type === 'reconciliation_intent',
                                  )!
                                }
                                busy={mutationBusy}
                                onChoose={(choice) =>
                                  void act(
                                    reviewCase.items.find(
                                      (item) => item.finding_type === 'reconciliation_intent',
                                    )!,
                                    'fix',
                                    choice,
                                  )
                                }
                              />
                            </section>
                          ) : reviewCase.items.some(
                              (item) => item.finding_type === 'reconciliation_missing',
                            ) ? (
                            <MissingTracksChoice
                              key={reviewCase.case_id}
                              findings={reviewCase.items.filter(
                                (item) => item.finding_type === 'reconciliation_missing',
                              )}
                              busy={mutationBusy}
                              onRequest={(ids) => void requestAllMissing(ids.length, ids)}
                              onChangeScope={() => void reconsiderScope()}
                            />
                          ) : null)}
                        <div
                          className={styles.trackTable}
                          role="group"
                          aria-label="Track assignments"
                        >
                          <div className={styles.trackTableHead} aria-hidden="true">
                            <span>Position</span>
                            <span>Local file</span>
                            <span>Selected release track</span>
                            <span>Status</span>
                          </div>
                          {trackView === 'release' ? (
                            releaseOrderedAssignments
                              .filter(matchingFilter)
                              .map(renderAssignmentRow)
                          ) : (
                            <>
                              {attentionAssignments.filter(matchingFilter).length > 0 && (
                                <div className={styles.assignmentGroup} data-tone="warning">
                                  <Button
                                    type="button"
                                    aria-expanded={openGroups.includes('attention')}
                                    onClick={() => toggleGroup('attention')}
                                  >
                                    {openGroups.includes('attention') ? '▾' : '▸'}{' '}
                                    {attentionAssignments.filter(matchingFilter).length} tracks
                                    needing review
                                  </Button>
                                  {openGroups.includes('attention') &&
                                    attentionAssignments
                                      .filter(matchingFilter)
                                      .map(renderAssignmentRow)}
                                </div>
                              )}
                              {changeAssignments.filter(matchingFilter).length > 0 && (
                                <div className={styles.assignmentGroup} data-tone="info">
                                  <Button
                                    type="button"
                                    aria-expanded={openGroups.includes('changes')}
                                    onClick={() => toggleGroup('changes')}
                                  >
                                    {openGroups.includes('changes') ? '▾' : '▸'} ↻{' '}
                                    {changeAssignments.filter(matchingFilter).length} proposed track
                                    changes
                                  </Button>
                                  {openGroups.includes('changes') &&
                                    changeAssignments
                                      .filter(matchingFilter)
                                      .map(renderAssignmentRow)}
                                </div>
                              )}
                              {missingAssignments.filter(matchingFilter).length > 0 && (
                                <div className={styles.assignmentGroup} data-tone="danger">
                                  <Button
                                    type="button"
                                    aria-expanded={openGroups.includes('missing')}
                                    onClick={() => toggleGroup('missing')}
                                  >
                                    {openGroups.includes('missing') ? '▾' : '▸'}{' '}
                                    {missingAssignments.filter(matchingFilter).length} absent
                                    release tracks
                                  </Button>
                                  {openGroups.includes('missing') && (
                                    <>
                                      {hasIntentDecision && (
                                        <p>
                                          Choose missing tracks above, or review album completeness
                                          on Plan.
                                        </p>
                                      )}
                                      {missingAssignments
                                        .filter(matchingFilter)
                                        .map(renderAssignmentRow)}
                                    </>
                                  )}
                                </div>
                              )}
                              {settledAssignments.filter(matchingFilter).length > 0 && (
                                <div className={styles.assignmentGroup} data-tone="good">
                                  <Button
                                    type="button"
                                    aria-expanded={openGroups.includes('settled')}
                                    onClick={() => toggleGroup('settled')}
                                  >
                                    {openGroups.includes('settled') ? '▾' : '▸'} ✓{' '}
                                    {settledAssignments.filter(matchingFilter).length} settled
                                    release slots
                                  </Button>
                                  {openGroups.includes('settled') &&
                                    settledAssignments
                                      .filter(matchingFilter)
                                      .map(renderAssignmentRow)}
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      </section>
                      <aside
                        className={styles.trackInspector}
                        ref={inspectorRef}
                        tabIndex={-1}
                        aria-label="Selected track details"
                        data-open={inspectorOpen || undefined}
                      >
                        <Button
                          type="button"
                          variant="ghost"
                          className={styles.inspectorClose}
                          onClick={() => {
                            setInspectorOpen(false);
                            requestAnimationFrame(() => {
                              document
                                .querySelector<HTMLElement>(
                                  `[data-review-assignment][data-selected="true"]`,
                                )
                                ?.focus({ preventScroll: true });
                              trackListScroll.current.forEach(({ element, top }) => {
                                element.scrollTop = top;
                              });
                            });
                          }}
                        >
                          ← Back to tracks
                        </Button>
                        {activeRowKey &&
                        assignmentsByKey.get(activeRowKey)?.status === 'missing' ? (
                          <>
                            <AssignmentEvidence
                              row={assignmentsByKey.get(activeRowKey)!}
                              album={reviewCase}
                              controls={
                                <div className={styles.inlineActions}>
                                  {!releaseChoicePending &&
                                    active?.finding_type === 'reconciliation_missing' &&
                                    !['wishlisted', 'downloading', 'downloaded'].includes(
                                      textValue(active.details?.fulfillment_state),
                                    ) && (
                                      <Button
                                        variant="primary"
                                        disabled={mutationBusy}
                                        onClick={() => void requestAllMissing(1, [active.id])}
                                      >
                                        Request this track
                                      </Button>
                                    )}
                                  {!releaseChoicePending &&
                                  reviewCase.items.some((item) =>
                                    ['reconciliation_missing', 'reconciliation_intent'].includes(
                                      item.finding_type,
                                    ),
                                  ) ? (
                                    <Button
                                      onClick={() => {
                                        setInspectorOpen(false);
                                        requestAnimationFrame(() => {
                                          const choices = document.querySelector<HTMLElement>(
                                            '[aria-label="Choose missing tracks"], [aria-label="Choose album completeness"]',
                                          );
                                          choices?.focus({ preventScroll: true });
                                          choices?.scrollIntoView({ block: 'center' });
                                        });
                                      }}
                                    >
                                      Choose missing tracks →
                                    </Button>
                                  ) : (
                                    <Button onClick={() => setAlbumView('plan')}>
                                      Review album completeness →
                                    </Button>
                                  )}
                                </div>
                              }
                            />
                          </>
                        ) : activeRowKey && active ? (
                          compactSelectedActionDetail
                        ) : activeRowKey &&
                          assignments.some((row) => assignmentKey(row) === activeRowKey) ? (
                          <AssignmentEvidence
                            row={assignmentsByKey.get(activeRowKey)!}
                            album={reviewCase}
                            busy={mutationBusy}
                            onReviewAgain={(findingId) => {
                              const requestedCase = caseId;
                              setBusy(true);
                              setError('');
                              void reopenReviewAssignment(requestedCase, findingId)
                                .then(async (result) => {
                                  if (!result.success)
                                    throw new Error(result.error || 'Could not reopen decision');
                                  if (selectedCaseRef.current !== requestedCase) return;
                                  setNotice(result.message || 'Decision reopened.');
                                  await refresh();
                                })
                                .catch((error: unknown) =>
                                  setError(
                                    error instanceof Error
                                      ? error.message
                                      : 'Could not reopen decision',
                                  ),
                                )
                                .finally(() => setBusy(false));
                            }}
                          />
                        ) : (
                          <p>Select a track to inspect its evidence and decisions.</p>
                        )}
                      </aside>
                    </div>
                  )}

                  {albumView === 'tracks' && assignments.length === 0 && (
                    <>
                      <div className={styles.empty}>
                        No track assignment data is available for this album.
                      </div>
                      {active && selectedActionDetail}
                    </>
                  )}

                  {albumView === 'release' && (
                    <section className={styles.evidenceView} aria-label="Release comparison">
                      <div className={styles.sectionHeading}>
                        <div>
                          <div className={styles.eyebrow}>Release selection</div>
                          <h3>Choose the edition for this album</h3>
                          <p>
                            The chosen release sets the track list. You can change it after
                            reviewing individual tracks.
                          </p>
                        </div>
                      </div>
                      {Boolean(active?.details?.release_disambiguation) && (
                        <p>Edition note: {textValue(active?.details?.release_disambiguation)}</p>
                      )}
                      <ReleaseComparison
                        candidates={releaseCandidates}
                        view="list"
                        selected={previewCandidate}
                        onSelect={setPreviewCandidate}
                        currentRelease={{
                          source: reviewCase.album_plan?.source,
                          release_id: reviewCase.album_plan?.release_id,
                        }}
                        numberingMode={reviewCase.album_plan?.numbering_mode}
                        onPin={(index) =>
                          !reviewCase.album_plan &&
                          active?.finding_type === 'reconciliation_release'
                            ? void act(active, 'fix', String(index))
                            : void pinCandidate(index)
                        }
                        busy={mutationBusy}
                      />
                      <div className={styles.manualRelease}>
                        <h4>Have a different MusicBrainz edition?</h4>
                        <p>
                          Enter its release MBID or URL to inspect the track list before choosing
                          it.
                        </p>
                        <div>
                          <TextInput
                            aria-label="MusicBrainz release MBID or URL"
                            value={releaseIdInput}
                            onChange={(event) => {
                              setReleaseIdInput(event.target.value);
                              setManualRelease(null);
                            }}
                            placeholder="Release MBID or URL"
                          />
                          <Button
                            type="button"
                            className={styles.secondaryButton}
                            disabled={busy}
                            onClick={() => void inspectReleaseId()}
                          >
                            Load release
                          </Button>
                        </div>
                      </div>
                      {active?.finding_type === 'reconciliation_release' && (
                        <Button
                          type="button"
                          disabled={busy}
                          onClick={() => void act(active, 'dismiss')}
                        >
                          Keep current release
                        </Button>
                      )}
                    </section>
                  )}

                  {albumView === 'plan' &&
                    active &&
                    !activeAssignment &&
                    !activeDecision &&
                    selectedActionDetail}
                </div>
                {albumView !== 'tracks' && (
                  <aside className={styles.tabContext} aria-label={`${albumView} context`}>
                    {albumView === 'plan' && reviewCase.album_plan && (
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
                            <dt>Album scope</dt>
                            <dd>
                              {reviewCase.album_plan.intent_scope ||
                                (hasIntentDecision
                                  ? 'Choice pending'
                                  : reviewCase.album_plan.counts.missing
                                    ? plan.can_apply_ready
                                      ? 'Missing tracks have planned resolutions'
                                      : 'Resolve track assignments first'
                                    : 'Full release coverage')}
                            </dd>
                          </div>
                          {wishlistedCount > 0 && (
                            <div>
                              <dt>Acquisition</dt>
                              <dd>
                                {wishlistedCount} track{wishlistedCount === 1 ? '' : 's'} requested;
                                awaiting files
                              </dd>
                            </div>
                          )}
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
                              {numberingSchemeLabel(
                                releaseHasMultipleDiscs,
                                reviewCase.album_plan.numbering_mode,
                              )}
                            </dd>
                          </div>
                        </dl>
                        {sourceFolders.length > 1 && (
                          <section
                            className={styles.folderContext}
                            aria-label="Combined album folders"
                          >
                            <h4>{sourceFolders.length} folders reviewed together</h4>
                            <p>
                              Track coverage and duplicate checks include all these files. Folder
                              organization is a separate action.
                            </p>
                            <ul>
                              {sourceFolders.map((folder) => (
                                <li key={folder}>
                                  <code>{folder}</code>
                                </li>
                              ))}
                            </ul>
                          </section>
                        )}
                        <div className={`${styles.actions} ${styles.planContextActions}`}>
                          <Button type="button" onClick={() => setAlbumView('tracks')}>
                            Review track assignments
                          </Button>
                          {reviewCase.album_plan.intent_scope && (
                            <Button
                              type="button"
                              disabled={mutationBusy}
                              onClick={() => void reconsiderScope()}
                            >
                              Review album scope
                            </Button>
                          )}
                        </div>
                      </section>
                    )}

                    {albumView === 'plan' && proposedChangeGroups.length > 0 && (
                      <section
                        className={styles.planContext}
                        aria-label="Proposed metadata by field"
                      >
                        <div>
                          <div className={styles.eyebrow}>Proposed metadata</div>
                          <h4>Changes by field</h4>
                          <p>Choose a field to see the affected tracks and exact values.</p>
                        </div>
                        <div className={styles.readySummaryList}>
                          {proposedChangeGroups.map((group) => (
                            <Button
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
                            </Button>
                          ))}
                        </div>
                      </section>
                    )}

                    {albumView === 'release' && (
                      <ReleaseComparison
                        candidates={releaseCandidates}
                        selected={previewCandidate}
                        onSelect={setPreviewCandidate}
                        view="detail"
                        numberingMode={reviewCase.album_plan?.numbering_mode}
                      />
                    )}
                  </aside>
                )}
              </div>
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
