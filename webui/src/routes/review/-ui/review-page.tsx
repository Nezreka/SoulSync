import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ActionMenu } from '@/components/action-menu/action-menu';
import { useConfirmDialog } from '@/components/dialog/use-confirm-dialog';
import { Button, Select, TextInput } from '@/components/form/form';
import { MetadataComparison } from '@/components/metadata-comparison/metadata-comparison';
import { PageHeader } from '@/components/page-header';
import { Notice } from '@/components/primitives';
import { ProviderReleaseId } from '@/components/provider-reference/provider-reference';
import { HelpHint, ReleaseBadges } from '@/components/release-metadata/release-metadata';

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
  stopCollectingAlbum,
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
import { usePolledStatus } from '../../tools/-tools.polling';
import { ApplyPlanPreview, type ReadyPreview } from './apply-plan-preview';
import { AssignmentRow } from './assignment-row';
import { CompletenessChoice } from './completeness-choice';
import { TrackRequests, FileDecisions } from './file-decisions';
import { MissingTracksChoice } from './missing-tracks-choice';
import { NumberingChoice } from './numbering-choice';
import { AssignmentEvidence, ReleaseComparison, StatusChip } from './reconciliation-evidence';
import {
  buildAlbumModel,
  decisionOrder,
  groupReadyChanges,
  readyFieldGroup,
} from './review-album-model';
import { ReviewArtwork } from './review-artwork';
import styles from './review-page.module.css';
import { libraryRelativePath } from './review-presentation';
import { assignmentKey, record, records, textValue } from './review-presentation';
import { SelectedActionDetail } from './selected-action-detail';
import { UnidentifiedAlbum } from './unidentified-album';

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
  const primary =
    ordered.find(
      (decision) => decision.risk !== 'ready' && decision.category !== 'pending_fulfillment',
    ) ||
    ordered.find((decision) => decision.category !== 'pending_fulfillment') ||
    ordered[0];
  const status =
    primary?.category === 'apply_blockers'
      ? 'Needs attention'
      : primary?.category === 'pending_fulfillment'
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
      return decision.label === 'Submit track requests'
        ? 'Request the wanted tracks before applying changes'
        : 'Choose whether this folder represents the complete album';
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
      apply_blockers: 'Saved actions need attention',
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

export function ReviewPage() {
  const { confirm, dialog } = useConfirmDialog();
  const initialParams = useMemo(() => new URLSearchParams(window.location.search), []);
  const initialMode = initialParams.get('mode');
  const [queue, setQueue] = useState<ReviewQueuePage | null>(null);
  const [queueExpanded, setQueueExpanded] = useState(false);
  const queueToggleRef = useRef<HTMLButtonElement | null>(null);
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
  const [openGroups, setOpenGroups] = useState<string[]>(['attention', 'changes', 'files']);
  const [previewCandidate, setPreviewCandidate] = useState(0);
  const [releaseIdInput, setReleaseIdInput] = useState('');
  const releaseLookupVersion = useRef(0);
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
  const [operationCases, setOperationCases] = useState<string[]>([]);
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
  const advanceAfterSave = useRef<{ caseId: string; from: number; ids: number[] } | null>(null);
  const [globalNotice, setGlobalNotice] = useState('');
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

  const queueRequestVersion = useRef(0);
  const queueScope = JSON.stringify([page, query, category, risk]);
  const currentQueueScope = useRef(queueScope);
  currentQueueScope.current = queueScope;
  const refreshQueue = useCallback(async () => {
    const version = ++queueRequestVersion.current;
    const current = () =>
      version === queueRequestVersion.current && queueScope === currentQueueScope.current;
    void fetchReviewQueueModeTotals(query, category)
      .then((totals) => {
        if (current()) setQueueTotals(totals);
      })
      .catch(() => undefined);
    try {
      const result = await fetchReviewQueue(page, query, category, risk);
      if (!current()) return null;
      setQueue(result);
      if (result.page !== page) setPage(result.page);
      return result;
    } catch (cause) {
      if (!current()) return null;
      throw cause;
    }
  }, [page, query, category, risk, queueScope]);

  useEffect(() => {
    let live = true;
    void refreshQueue()
      .then((result) => {
        if (live && result && !selectedCaseRef.current && result.items[0])
          setCaseId(result.items[0].case_id);
      })
      .catch((cause: unknown) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
      ++queueRequestVersion.current;
    };
  }, [refreshQueue, setError]);

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
    releaseLookupVersion.current += 1;
    setReleaseIdInput('');
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
          result.refresh?.state !== 'error' &&
          !result.last_operation?.error
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
  const orderedCaseIds = useMemo(
    () => queue?.case_ids || queue?.items.map((item) => item.case_id) || [],
    [queue],
  );
  const caseIndex = orderedCaseIds.indexOf(caseId);
  const navigationAnchor = useRef({ caseId: '', index: 0 });
  if (caseIndex >= 0) navigationAnchor.current = { caseId, index: caseIndex };
  const detachedIndex =
    navigationAnchor.current.caseId === caseId ? navigationAnchor.current.index : 0;
  const previousCaseId =
    orderedCaseIds[
      caseIndex >= 0 ? caseIndex - 1 : Math.min(detachedIndex - 1, orderedCaseIds.length - 1)
    ];
  const nextCaseId = orderedCaseIds[caseIndex >= 0 ? caseIndex + 1 : detachedIndex];
  useEffect(() => {
    if (!queue?.case_ids || !caseId) return;
    const index = queue.case_ids.indexOf(caseId);
    const target = index >= 0 ? Math.floor(index / queue.limit) : queue.page;
    if (target !== page) setPage(target);
  }, [queue, caseId, page]);
  const pageCount = queue ? Math.max(1, Math.ceil(queue.total / queue.limit)) : 1;
  const albumNumber =
    queue && caseIndex >= 0 ? (queue.case_ids ? 0 : page * queue.limit) + caseIndex + 1 : null;
  const typeInfo = active ? catalog[active.finding_type] : undefined;
  const applyingThisAlbum =
    applyBusy && (operationCase === caseId || operationCases.includes(caseId));
  const mutationBusy =
    busy || reanalyzing || applyingThisAlbum || reviewCase?.refresh?.state === 'running';
  const plan = reviewCase?.plan || emptyPlan;
  const folderFinding = !reviewCase?.album_plan
    ? reviewCase?.items.find((item) => item.details?.folder_issue)
    : undefined;
  const unidentified =
    reviewCase?.album_plan?.state === 'unidentified' ||
    Boolean(reviewCase?.items.some((item) => !item.file_path && item.details?.attempted_releases));
  const unidentifiedFinding = reviewCase?.items.find(
    (item) => !item.file_path && item.finding_type === 'reconciliation_unresolved',
  );
  const albumTitleChange = reviewCase?.items
    .filter((item) => !item.details?.plan_keep)
    .map((item) => record(record(item.details?.changes).library_album_title))
    .find((change) => change.new && change.new !== change.old);
  const albumModel = useMemo(() => buildAlbumModel(reviewCase), [reviewCase]);
  const {
    decisions,
    assignments,
    attentionAssignments,
    changeAssignments,
    metadataDecisionCount,
    proposedChangeCount,
    missingAssignments,
    pairedConflictCount,
    settledAssignments,
    reviewDecisions,
    pendingDecisions,
    wishlistedCount,
    releaseChoicePending,
    releaseHasMultipleDiscs,
    hasIntentDecision,
    reviewMetadataCount,
    readyIncludedInGroup,
    assignmentsByFinding,
    assignmentsByKey,
    assignmentsByPath,
    decisionsByFinding,
    decisionMembers,
    releaseOrderedAssignments,
  } = albumModel;
  const blockedActionCount = reviewDecisions
    .filter((decision) => decision.category === 'apply_blockers')
    .reduce((total, decision) => total + decision.action_count, 0);
  const choiceCount = reviewDecisions.filter(
    (decision) => decision.category !== 'apply_blockers',
  ).length;
  const albumReady = plan.can_apply_ready && reviewDecisions.length === 0;
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
  useEffect(() => {
    if (trackFilter.startsWith('decision:') && !decisionMembers.get(trackFilter.slice(9))?.size) {
      setTrackFilter('');
      setActiveRowKey(null);
    }
  }, [trackFilter, decisionMembers]);
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
  const visibleAttention = attentionAssignments.filter(matchingFilter);
  const fileAction = (row: AlbumAssignment) =>
    ['quarantine', 'replace', 'request_missing'].includes(row.planned_choice?.fix_action || '') ||
    (row.planned_choice?.fix_action || '').startsWith('existing:');
  const visibleFiles = changeAssignments.filter(matchingFilter).filter(fileAction);
  const visibleChanges = changeAssignments.filter(matchingFilter).filter((row) => !fileAction(row));
  const visibleMissing = missingAssignments.filter(matchingFilter);
  const visibleSettled = settledAssignments
    .filter(matchingFilter)
    .filter((row) => row.status !== 'not_owned');
  const visibleUncollected = settledAssignments
    .filter(matchingFilter)
    .filter((row) => row.status === 'not_owned');
  const intentFinding = reviewCase?.items.find(
    (item) => item.finding_type === 'reconciliation_intent',
  );
  const missingFindings =
    reviewCase?.items.filter((item) => item.finding_type === 'reconciliation_missing') || [];
  const activeDecision = active ? decisionsByFinding.get(active.id) : undefined;
  const activeAssignment = active ? assignmentsByFinding.get(active.id) : undefined;
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
    if (albumView !== 'tracks' || !reviewCase || !assignments.length) return;
    const visible = filterRows.filter(matchingFilter);
    if (activeRowKey && visible.some((row) => assignmentKey(row) === activeRowKey)) return;
    const first =
      visible.find((row) => row.finding_id === activeId) ||
      visible.find((row) => row.finding_id && row.action_state !== 'waiting') ||
      visible[0];
    setActiveId(first?.finding_id ?? null);
    setActiveRowKey(first ? assignmentKey(first) : null);
  }, [albumView, reviewCase, activeRowKey, activeId, assignments, trackFilter, trackView]);

  const showTrackList = (filter = trackFilter, focus = true) => {
    setTrackFilter(filter);
    setAlbumView('tracks');
    setInspectorOpen(false);
    setOpenGroups((current) => [
      ...new Set([
        ...current,
        ...(filter === 'missing'
          ? ['missing']
          : filter === 'settled'
            ? ['settled']
            : ['attention', 'changes']),
      ]),
    ]);
    if (focus)
      requestAnimationFrame(() => {
        const list = document.querySelector<HTMLElement>(
          '[aria-label="Complete album track assignment"]',
        );
        list?.focus({ preventScroll: true });
        list?.scrollIntoView({ block: 'start' });
      });
  };

  const focusTrackInspector = () => {
    requestAnimationFrame(() => {
      const inspector = inspectorRef.current;
      if (inspector && getComputedStyle(inspector).position === 'static') {
        inspector.focus();
        inspector.scrollIntoView({ block: 'start' });
      }
    });
  };

  const selectFinding = (findingId: number | null, rowKey?: string) => {
    setActiveId((current) => (current === findingId ? null : findingId));
    const target = assignmentsByFinding.get(findingId);
    setActiveRowKey(rowKey || (target ? assignmentKey(target) : null));
    if (target?.status === 'change')
      setOpenGroups((current) => (current.includes('changes') ? current : [...current, 'changes']));
  };

  const selectCase = (next: string) => {
    setQueueExpanded(false);
    // Keep focus on a visible control when the compact browser closes.
    if (
      caseListRef.current?.contains(document.activeElement) &&
      queueToggleRef.current?.offsetParent
    )
      queueToggleRef.current.focus();
    setNotice('');
    setInspectorOpen(false);
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
      trackListScroll.current = [];
      setInspectorOpen(true);
      focusTrackInspector();
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
        const result = await refreshQueue();
        if (result?.items[0] && !selectedCaseRef.current) setCaseId(result.items[0].case_id);
        return true;
      }
      const updated = await fetchReviewCase(requestedId);
      if (selectedCaseRef.current !== requestedId || requestVersion !== caseRequestVersion.current)
        return true;
      if (updated.case_id !== requestedId) {
        setCaseId(updated.case_id);
        await refreshQueue();
        return true;
      }
      // Poll the active album while it is being analyzed. Recounting the whole
      // library every 1.2 seconds adds latency without exposing a final result.
      if (updated.refresh?.state === 'running') {
        setReviewCase(updated);
        return true;
      }
      setReviewCase(updated);
      setNotice((value) => (value.includes('Updating this album plan') ? '' : value));
      const updatedQueue = await refreshQueue();
      if (!updatedQueue) return true;
      if (selectedCaseRef.current !== requestedId || requestVersion !== caseRequestVersion.current)
        return true;
      if (refreshError.current) {
        const previous = refreshError.current;
        setErrorValue((current) => (current === previous ? '' : current));
        refreshError.current = null;
      }
      if (
        updated.status === 'completed' &&
        updated.refresh?.state !== 'error' &&
        !updated.last_operation?.error
      ) {
        const previousIds = queue?.case_ids || queue?.items.map((item) => item.case_id) || [];
        const currentIds = updatedQueue.case_ids || updatedQueue.items.map((item) => item.case_id);
        const previousIndex = Math.max(0, previousIds.indexOf(requestedId));
        const following = previousIds.slice(previousIndex + 1);
        const next =
          following.find((id) => currentIds.includes(id)) ||
          currentIds[Math.min(previousIndex, currentIds.length - 1)];
        setReviewCase(null);
        selectCase(next || '');
        setNotice('Album review completed.');
      } else {
        setReviewCase(updated);
        if (updated.refresh?.state === 'error')
          setError(updated.refresh.error || 'Album refresh failed');
      }
      return true;
    } catch (cause) {
      if (
        selectedCaseRef.current === requestedId &&
        requestVersion === caseRequestVersion.current
      ) {
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

  useEffect(() => {
    let pending = false;
    const reload = () => {
      if (document.hidden || pending || busy || applyBusy || reanalyzing) return;
      pending = true;
      void refreshRef.current().finally(() => {
        pending = false;
      });
    };
    window.addEventListener('focus', reload);
    window.addEventListener('online', reload);
    const timer = window.setInterval(reload, 30000);
    return () => {
      window.removeEventListener('focus', reload);
      window.removeEventListener('online', reload);
      window.clearInterval(timer);
    };
  }, [busy, applyBusy, reanalyzing]);

  const operationRef = useRef<string | null>(window.sessionStorage.getItem('review-operation'));
  const rememberOperation = (id?: string, albumCase?: string) => {
    setOperationCases(albumCase ? [albumCase] : []);
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
        setOperationCases(status.review_case_ids || []);
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
            status.phase === 'requests'
              ? 'Adding requested tracks to the wishlist…'
              : status.phase === 'file_actions'
                ? 'Saving replacement requests and moving selected files to quarantine…'
                : status.phase === 'waiting_for_scan'
                  ? 'Waiting for reconciliation to finish before applying…'
                  : status.phase === 'queueing_organization'
                    ? 'Queueing folder organization…'
                    : status.phase === 'organizing'
                      ? 'Organizing album files…'
                      : status.phase === 'reanalyzing' || status.phase === 'queueing_reanalysis'
                        ? 'Checking the completed album and refreshing its plan…'
                        : `Processing ${status.done || 0} of ${status.total || 0} album actions…`,
          );
          return;
        } else if (
          status.error ||
          status.failed ||
          status.blocked ||
          status.not_attempted ||
          status.stopped
        ) {
          setError(
            `${status.fixed || 0} actions applied${status.blocked ? ` · ${status.blocked} blocked` : ''}${status.failed ? ` · ${status.failed} failed` : ''}${status.not_attempted ? ` · ${status.not_attempted} not attempted` : ''}. ${status.error || status.errors?.[0]?.error || 'The operation stopped before completing.'}`,
            reportCase,
          );
        } else {
          const message = `${status.fixed || 0} album action${status.fixed === 1 ? '' : 's'} completed.${status.organize_queued ? ' Album files organized.' : ''}${status.reconciliation_queued ? ' Album plan refreshed.' : ''}`;
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

  const [organizationMoves, setOrganizationMoves] = useState<
    Array<{ current: string; planned: string }>
  >([]);
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
        body: JSON.stringify({
          mode: 'tags',
          review_case_id: reviewCase.case_id,
          plan_signature: reviewCase.plan_signature,
        }),
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
            unchanged?: boolean;
            current_path?: string;
            new_path?: string;
          }) =>
            track.file_exists &&
            !track.unchanged &&
            !track.collision &&
            track.matched !== false &&
            track.new_path &&
            track.current_path?.split('/').slice(0, -1).join('/') !==
              track.new_path.split('/').slice(0, -1).join('/'),
        );
        setOrganizationMoves(
          moves.map((track: { current_path: string; new_path: string }) => ({
            current: track.current_path.split('/').slice(0, -1).join('/'),
            planned: track.new_path.split('/').slice(0, -1).join('/'),
          })),
        );
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
      if (
        selectedCaseRef.current === requestedId &&
        requestVersion === caseRequestVersion.current
      ) {
        await refreshRef.current();
      }
    } catch (cause) {
      if (selectedCaseRef.current === requestedId) setError((cause as Error).message);
    } finally {
      setReanalyzingCase((current) => (current === requestedId ? null : current));
    }
  };

  const retryOrganization = async () => {
    if (!reviewCase?.organization_retry_signature) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/library/album/${encodeURIComponent(String(reviewCase.album_id))}/reorganize/preview`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mode: 'tags',
            review_case_id: reviewCase.case_id,
            organization_retry_signature: reviewCase.organization_retry_signature,
          }),
        },
      );
      const result = await response.json();
      if (!response.ok || !result.success)
        throw new Error(result.error || 'Could not preview the remaining folder changes');
      const tracks = (result.tracks || []) as Array<{
        title: string;
        current_path: string;
        new_path: string;
        unchanged?: boolean;
        matched?: boolean;
        collision?: boolean;
      }>;
      if (tracks.some((track) => track.collision || track.matched === false))
        throw new Error(
          'Some files still need attention before folder organization can be retried.',
        );
      const moves = tracks.filter((track) => !track.unchanged);
      const accepted = await confirm({
        title: 'Retry folder organization',
        confirmText: moves.length ? 'Organize remaining files' : 'Confirm files already organized',
        message: (
          <>
            <p>
              Completed metadata changes will be kept.{' '}
              {moves.length
                ? 'Only the following file moves remain.'
                : 'All files are already in their planned locations.'}
            </p>
            {moves.length > 0 && (
              <MetadataComparison
                rows={moves.map((track) => ({
                  field: track.title,
                  current: track.current_path,
                  planned: track.new_path,
                }))}
              />
            )}
          </>
        ),
      });
      if (!accepted) return;
      const start = await fetch(
        `/api/repair/review-queue/${encodeURIComponent(reviewCase.case_id)}/retry-organization`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ signature: reviewCase.organization_retry_signature }),
        },
      );
      const operation = await start.json();
      if (!start.ok || !operation.started)
        throw new Error(operation.error || 'Could not retry folder organization');
      rememberOperation(operation.operation_id, reviewCase.case_id);
      setApplyBusy(true);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const leaveAlbum = async () => {
    if (!reviewCase) return;
    const accepted = await confirm({
      title: 'Leave this album entirely as-is?',
      message:
        'Keep the current tags, filenames, library album membership, and folders. This closes the current review without applying its planned changes. Existing download requests stay on the wishlist.',
      confirmText: 'Leave album entirely as-is',
    });
    if (!accepted) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/repair/review-queue/${encodeURIComponent(reviewCase.case_id)}/leave-as-is`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ signature: reviewCase.plan_signature }),
        },
      );
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not save this choice');
      setNotice(result.message);
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const applyReady = async (scopeCaseId?: string, organize = false) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const response = await fetch(
        `/api/repair/review-queue/apply-ready${scopeCaseId ? `?case_id=${encodeURIComponent(scopeCaseId)}` : ''}`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error('Could not load the ready album plans.');
      const preview = (await response.json()) as ReadyPreview;
      if (!preview.action_count)
        throw new Error('No fully decided album plans are ready to apply.');
      let selection = {
        case_ids: preview.albums.map((album) => album.case_id),
        organize_case_ids: organize && scopeCaseId ? [scopeCaseId] : [],
      };
      const message = (
        <ApplyPlanPreview
          onSelectionChange={(value) => {
            selection = value;
          }}
          preview={preview}
          expanded={Boolean(scopeCaseId)}
          destinations={organize ? organizationMoves : []}
        />
      );
      const confirmed = await confirm({
        title: 'Apply ready album plans',
        message,
        destructive: preview.file_actions > 0,
        confirmText: 'Apply selected plans',
      });
      if (!confirmed) return;
      setApplyBusy(true);
      const started = await startReadyChanges(preview.signature, scopeCaseId, organize, selection);
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
      message: (
        <>
          <p>
            These tracks will be added to the wishlist for the selected release. File changes are
            applied separately.
          </p>
          <TrackRequests
            items={reviewCase.items.filter(
              (item) =>
                item.finding_type === 'reconciliation_missing' &&
                (!findingIds || findingIds.includes(item.id)) &&
                !['wishlisted', 'downloading', 'downloaded'].includes(
                  textValue(item.details?.fulfillment_state),
                ),
            )}
          />
        </>
      ),
      confirmText: `Request ${missingCount} track${missingCount === 1 ? '' : 's'}`,
    });
    if (!confirmed) return;
    setApplyBusy(true);
    setError('');
    setApplyProgress(`Requesting 0 of ${missingCount} missing tracks…`);
    try {
      const started = await startMissingTrackRequests(reviewCase.case_id, findingIds);
      if (started.success) {
        setApplyBusy(false);
        setApplyProgress('');
        setNotice(
          `${started.requested || missingCount} tracks requested. Approved file changes can now be applied.`,
        );
        await refresh();
      } else if (started.started) {
        rememberOperation(started.operation_id, reviewCase.case_id);
      } else {
        await refresh();
        throw new Error(started.error || 'Could not request missing tracks');
      }
    } catch (cause) {
      setApplyBusy(false);
      setApplyProgress('');
      setError((cause as Error).message);
    }
  };

  const stopCollecting = async () => {
    if (!reviewCase?.plan_signature) return;
    const accepted = await confirm({
      title: 'Stop collecting this album?',
      message: `All ${assignments.filter((row) => row.file_path).length} remaining local files have removal planned. Stop requesting this release and withdraw its album wishlist requests? Files will move to recoverable quarantine when you apply the plan.`,
      confirmText: 'Stop collecting this album',
      destructive: true,
    });
    if (!accepted) return;
    setBusy(true);
    try {
      const result = await stopCollectingAlbum(reviewCase.case_id, reviewCase.plan_signature);
      if (!result.success) throw new Error(result.error || 'Could not update this album');
      setNotice(result.message || 'Album collection stopped.');
      await refresh();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const undoChoice = async (findingId: number) => {
    if (!reviewCase) return;
    setBusy(true);
    setNotice('');
    try {
      const result = await reopenReviewAssignment(reviewCase.case_id, findingId);
      if (!result.success) throw new Error(result.error || 'Could not undo this choice');
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const pending = advanceAfterSave.current;
    if (
      !pending ||
      !reviewCase ||
      reviewCase.case_id !== pending.caseId ||
      reviewCase.refresh?.state === 'running'
    )
      return;
    const current = reviewCase.items.find((item) => item.id === pending.from);
    if (current && !current.details?.planned_choice && !current.details?.saved_assignment) return;
    advanceAfterSave.current = null;
    setNotice('');
    const next = pending.ids.find((id) =>
      reviewCase.items.some(
        (item) =>
          item.id === id && !item.details?.planned_choice && !item.details?.saved_assignment,
      ),
    );
    if (next != null) {
      setTrackFilter('');
      setActiveId(next);
      const row = reviewCase.album_plan?.assignments.find((entry) => entry.finding_id === next);
      setActiveRowKey(row ? assignmentKey(row) : null);
    } else {
      setInspectorOpen(false);
      setActiveId(null);
      setActiveRowKey(null);
      setAlbumView('plan');
    }
  }, [reviewCase]);

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
      if (!result.partial && finding.file_path) {
        const ids = (reviewCase?.items || [])
          .filter((item) => item.file_path)
          .map((item) => item.id);
        const index = ids.indexOf(finding.id);
        advanceAfterSave.current = {
          caseId,
          from: finding.id,
          ids: [...ids.slice(index + 1), ...ids.slice(0, index)],
        };
      }
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
    const requestedCase = reviewCase.case_id;
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
      if (selectedCaseRef.current !== requestedCase) return;
      setNotice(result.message || 'Release selected; updating this album plan');
      const updated = await fetchReviewCase(reviewCase.case_id);
      if (selectedCaseRef.current !== requestedCase) return;
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
      if (selectedCaseRef.current === requestedCase) setError((cause as Error).message);
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
    const requestedCase = reviewCase.case_id;
    const version = ++releaseLookupVersion.current;
    const isCurrent = () =>
      selectedCaseRef.current === requestedCase && version === releaseLookupVersion.current;
    try {
      const result = await pinReviewRelease(
        reviewCase.case_id,
        { source: 'musicbrainz', release_id: id },
        reviewCase.album_plan,
        { preview: true },
      );
      if (!isCurrent()) return;
      if (!result.success) throw new Error(result.error || 'Could not load this release');
      if (!result.candidate) throw new Error('The release comparison could not be loaded');
      setManualRelease(result.candidate);
      setPreviewCandidate(
        (reviewCase.album_plan.candidates || []).filter((candidate) => candidate.release_id !== id)
          .length,
      );
    } catch (cause) {
      if (isCurrent()) setError((cause as Error).message);
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
    reviewCase && albumReady ? (
      <div className={styles.inlineActions}>
        <Button
          type="button"
          variant="primary"
          disabled={mutationBusy || applyBusy}
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
              disabled={mutationBusy || applyBusy}
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
              if (group.key === 'library_album_title') {
                setAlbumView('plan');
                document
                  .getElementById('review-shared-album-title')
                  ?.scrollIntoView?.({ block: 'nearest' });
              } else showTrackList(group.key);
            }}
          >
            <span>
              <strong>{group.label}</strong>
              <small>{group.valueSummary}</small>
            </span>
            <b>
              {group.key === 'library_album_title'
                ? 'Whole album'
                : `${group.count} track${group.count === 1 ? '' : 's'}`}
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

  const actionDetail = (compact = false) => {
    if (!active || !reviewCase) return null;
    const navigationIds = compact
      ? [
          ...new Set(
            filterRows
              .filter(matchingFilter)
              .flatMap((row) =>
                row.finding_id != null &&
                reviewCase.items.some((item) => item.id === row.finding_id)
                  ? [row.finding_id]
                  : [],
              ),
          ),
        ]
      : reviewCase.items.map((item) => item.id);
    const navigationIndex = navigationIds.indexOf(active.id);
    return (
      <SelectedActionDetail
        key={active.id}
        finding={active}
        caseId={reviewCase?.case_id}
        release={primaryCandidate}
        itemIndex={navigationIndex}
        itemCount={navigationIds.length}
        typeInfo={typeInfo}
        busy={mutationBusy}
        onAct={(action, choice) => void act(active, action, choice)}
        onSelect={selectFinding}
        onReset={() => void undoChoice(active.id)}
        onRecheck={(cleared) => {
          if (cleared) setError('');
          void refreshRef.current();
        }}
        previousId={navigationIds[navigationIndex - 1]}
        nextId={navigationIds[navigationIndex + 1]}
        comparisonPath={comparisonPath || undefined}
        comparisonDurationMs={comparisonDurationMs}
        compact={compact}
        numberingPreview={activeAssignment?.numbering_preview}
      />
    );
  };
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
          focusTrackInspector();
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
        className={styles.queueHeader}
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
                  ? `${queueTotals?.ready_total ?? queue.ready_total ?? 0} ready change${(queueTotals?.ready_total ?? queue.ready_total ?? 0) === 1 ? '' : 's'} across the library`
                  : 'Loading ready changes…'}
              </span>
            </div>
            <Button
              type="button"
              variant="primary"
              className={styles.safeAction}
              disabled={
                mutationBusy || applyBusy || !(queueTotals?.ready_total ?? queue?.ready_total)
              }
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
                title="Recheck files and matching evidence, then rebuild album plans"
                disabled={scanBusy || scanStatus?.current_job?.job_id === 'library_reconciliation'}
                onClick={() => {
                  void runScan();
                }}
              >
                {scanBusy
                  ? 'Queuing…'
                  : scanStatus?.current_job?.job_id === 'library_reconciliation'
                    ? 'Scanning…'
                    : 'Reanalyze library'}
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
        <Notice tone="info" role="status">
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
      {scanStatus?.current_job?.job_id === 'library_reconciliation' && (
        <div className={styles.scanStatus} role="status" data-running>
          <span className={styles.loadingSpinner} aria-hidden="true" />
          <progress
            aria-label="Library reconciliation progress"
            max={100}
            value={
              scanStatus.current_job.progress?.total
                ? scanStatus.current_job.progress.percent || 0
                : undefined
            }
          />
          {scanStatus.current_job.progress?.total
            ? `Reconciliation running: ${scanStatus.current_job.progress.scanned} of ${scanStatus.current_job.progress.total} folders.`
            : 'Discovering album folders…'}
        </div>
      )}
      {lastRun &&
        lastRun.status !== 'completed' &&
        scanStatus?.current_job?.job_id !== 'library_reconciliation' && (
          <details className={styles.scanStatus}>
            <summary>Previous library scan: {lastRun.status}</summary>
            <p>
              {lastRun.error_text || 'No reason recorded'}. {lastRun.items_scanned} files checked; a
              rerun reuses cached fingerprints.
            </p>
          </details>
        )}

      <div className={styles.workspace}>
        <aside className={styles.sidebar} aria-label="Reconciliation albums">
          <Button
            ref={queueToggleRef}
            className={styles.queueToggle}
            aria-expanded={queueExpanded}
            aria-controls="review-album-browser"
            onClick={() => setQueueExpanded((expanded) => !expanded)}
          >
            <span>{queueExpanded ? 'Hide album browser' : 'Browse albums'}</span>
            <span>
              {queueModes.find((mode) => mode.value === queueMode)?.label} · {queue?.total ?? '…'}
              {query || category ? ' · Filtered' : ''}
            </span>
          </Button>
          <div
            id="review-album-browser"
            className={styles.queueBrowser}
            data-expanded={queueExpanded}
          >
            <nav className={styles.queueModes} aria-label="Queue view">
              {queueModes.map((mode) => (
                <Button
                  key={mode.value}
                  type="button"
                  aria-current={queueMode === mode.value ? 'page' : undefined}
                  onClick={() => {
                    if (queueMode === mode.value) return;
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
            {(searchInput || query || category) && (
              <Button
                type="button"
                className={styles.clearFilters}
                onClick={() => {
                  setSearchInput('');
                  setQuery('');
                  setCategory('');
                  setPage(0);
                }}
              >
                Clear filters
              </Button>
            )}
            <div className={styles.caseList} ref={caseListRef} aria-busy={!queue}>
              {!queue && <p role="status">Loading albums…</p>}
              {queue?.items.map((item) => (
                <Button
                  key={item.case_id}
                  type="button"
                  className={item.case_id === caseId ? styles.selectedCase : styles.caseButton}
                  aria-current={item.case_id === caseId ? 'true' : undefined}
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
                      {item.error_count
                        ? ` · ${item.error_count} apply issue${item.error_count === 1 ? '' : 's'}`
                        : ''}
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
                      {Math.max(0, reviewCase.count - wishlistedCount)} pending change
                      {reviewCase.count - wishlistedCount === 1 ? '' : 's'}
                      {wishlistedCount > 0 &&
                        ` · ${wishlistedCount} requested track${wishlistedCount === 1 ? '' : 's'}`}
                    </div>
                    <h2>
                      {reviewCase.current_album_title || reviewCase.album || 'Unassigned files'}
                    </h2>
                    <p>{reviewCase.artist || 'Artist not established'}</p>
                    {albumTitleChange && (
                      <p className={styles.plannedAlbumTitle}>
                        Library album title after apply:{' '}
                        <strong>{textValue(albumTitleChange.new)}</strong>
                      </p>
                    )}
                  </div>
                </div>
                <nav className={styles.nav} aria-label="Album navigation">
                  <Button
                    type="button"
                    disabled={!previousCaseId}
                    onClick={() => previousCaseId && selectCase(previousCaseId)}
                  >
                    ← Previous album
                  </Button>
                  <Button
                    type="button"
                    disabled={!nextCaseId}
                    onClick={() => nextCaseId && selectCase(nextCaseId)}
                  >
                    Next album →
                  </Button>
                </nav>
                <div className={styles.albumMaintenance}>
                  <Button
                    type="button"
                    disabled={mutationBusy}
                    title="Recheck this album and rebuild its plan using valid cached audio and catalog evidence"
                    onClick={() => void reanalyzeAlbum()}
                  >
                    Reanalyze album
                  </Button>
                  <ActionMenu
                    trigger={(props) => (
                      <Button {...props} variant="ghost" aria-label="Album maintenance options">
                        More ▾
                      </Button>
                    )}
                    items={[
                      {
                        key: 'leave',
                        label: 'Leave album entirely as-is…',
                        disabled: mutationBusy || !reviewCase.items.length,
                        onSelect: () => void leaveAlbum(),
                      },
                      {
                        key: 'catalogue',
                        label: 'Refresh catalog evidence',
                        title:
                          'Fetch fresh catalog records and reanalyze this album. Cached audio fingerprints are reused.',
                        disabled: mutationBusy,
                        onSelect: () => void reanalyzeAlbum(true),
                      },
                    ]}
                  />
                </div>
              </div>

              {reviewCase.last_operation?.error && (
                <Notice tone="danger" role="alert">
                  <strong>
                    {reviewCase.last_operation.fixed || 0} actions applied ·{' '}
                    {reviewCase.last_operation.organize_failed
                      ? 'Folder organization incomplete'
                      : 'Operation needs attention'}
                  </strong>
                  <p>{reviewCase.last_operation.error}</p>
                  <p>Completed metadata changes are retained.</p>
                  {reviewCase.organization_retry_signature && (
                    <Button disabled={mutationBusy} onClick={() => void retryOrganization()}>
                      Review and retry folder organization
                    </Button>
                  )}
                </Notice>
              )}
              {error && errorCase === reviewCase.case_id && !reviewCase.last_operation?.error && (
                <Notice tone="danger" role="alert">
                  {error}
                </Notice>
              )}
              {!folderFinding && (
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
                      if (next === 'tracks') showTrackList(trackFilter, false);
                      else setAlbumView(next);
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
                    <span>
                      {reviewDecisions.length
                        ? `${reviewDecisions.length} decision${reviewDecisions.length === 1 ? '' : 's'}`
                        : `${plan.ready} action${plan.ready === 1 ? '' : 's'}`}
                    </span>
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
                    onClick={() => showTrackList(trackFilter, false)}
                  >
                    Tracks
                    <span>{assignments.length}</span>
                  </Button>
                </nav>
              )}

              {applyBusy && operationCase === reviewCase.case_id && (
                <Notice tone="info" className={styles.applyBanner} role="status" aria-live="polite">
                  <span className={styles.loadingSpinner} aria-hidden="true" />
                  <strong>{applyProgress || 'Applying this album plan…'}</strong>
                  <span>This continues on the server. You can leave this page and return.</span>
                </Notice>
              )}
              {notice && !reanalyzing && reviewCase.refresh?.state !== 'running' && (
                <Notice
                  tone="info"
                  className={styles.dismissibleNotice}
                  role="status"
                  aria-live="polite"
                >
                  <span>{notice}</span>
                  <Button type="button" aria-label="Dismiss notice" onClick={() => setNotice('')}>
                    ×
                  </Button>
                </Notice>
              )}
              {!(applyBusy && operationCase === reviewCase.case_id) &&
                (reanalyzing || reviewCase.refresh?.state === 'running') && (
                  <Notice tone="info" className={styles.refreshBanner} role="status">
                    <span className={styles.loadingSpinner} aria-hidden="true" />
                    {reviewCase.refresh?.scope === 'plan'
                      ? 'Updating the album plan from the evidence already reviewed. Your saved choices remain visible.'
                      : 'Reanalyzing this album. The previous decisions remain visible while dependent track actions are recalculated.'}
                  </Notice>
                )}

              {albumView === 'plan' &&
                !releaseChoicePending &&
                reviewCase.album_plan?.assignments.some((row) =>
                  ['missing', 'not_owned'].includes(row.status),
                ) && (
                  <section className={styles.completenessSummary} aria-label="Album completeness">
                    <div>
                      <h4>Album completeness</h4>
                      <p>
                        {intentFinding
                          ? 'Not decided'
                          : reviewCase.album_plan.intent_scope === 'discard'
                            ? 'Collection stopped · file removals planned'
                            : reviewCase.album_plan.intent_scope === 'complete'
                              ? 'Collect the complete album'
                              : reviewCase.album_plan.intent_scope === 'partial'
                                ? 'Keep a partial album'
                                : 'Not decided'}
                      </p>
                    </div>
                    {intentFinding ? (
                      <Button
                        disabled={mutationBusy}
                        onClick={() => {
                          setAlbumView('tracks');
                          setTrackFilter('missing');
                          setInspectorOpen(false);
                        }}
                      >
                        Choose missing tracks
                      </Button>
                    ) : (
                      <Button disabled={mutationBusy} onClick={() => void reconsiderScope()}>
                        Change completeness…
                      </Button>
                    )}
                  </section>
                )}

              {albumView === 'tracks' && albumReady && (
                <Notice tone="info" className={styles.albumNotice} role="status">
                  <span>All decisions are planned. This album is ready to apply.</span>
                  <Button type="button" onClick={() => setAlbumView('plan')}>
                    Return to plan →
                  </Button>
                </Notice>
              )}
              <div
                className={styles.tabWorkspace}
                data-view={albumView}
                data-unidentified={unidentified || undefined}
              >
                <div
                  className={styles.tabMain}
                  id="review-tab-panel"
                  role="tabpanel"
                  aria-labelledby={`review-tab-${albumView}`}
                >
                  {unidentified && albumView !== 'release' && (
                    <UnidentifiedAlbum
                      reviewCase={reviewCase}
                      busy={mutationBusy}
                      onIdentify={() => setAlbumView('release')}
                      onLeave={
                        unidentifiedFinding
                          ? () => void act(unidentifiedFinding, 'dismiss')
                          : undefined
                      }
                    />
                  )}
                  {albumView === 'plan' && reviewCase.can_stop_collecting && (
                    <section className={styles.abandonAlbum}>
                      <div>
                        <h3>All remaining files have removal planned</h3>
                        <p>
                          If you do not want this album, stop collecting it before applying the file
                          removals.
                        </p>
                      </div>
                      <Button disabled={mutationBusy} onClick={() => void stopCollecting()}>
                        I don’t want this album
                      </Button>
                    </section>
                  )}
                  {folderFinding && (
                    <section
                      className={styles.folderConflict}
                      aria-label="Folder membership review"
                    >
                      <StatusChip tone="warning" icon="warning">
                        Album ownership needs review
                      </StatusChip>
                      <h3>{folderFinding.title}</h3>
                      <p>{folderFinding.description}</p>
                      <p>
                        {textValue(folderFinding.details?.file_count)} local files were found. A
                        release plan cannot be built until their album ownership is consistent.
                      </p>
                      <p>
                        Check the album tags and folder placement below. Correct the conflicting
                        membership, then reanalyze this album.
                      </p>
                      <p className={styles.muted}>
                        {libraryRelativePath(
                          reviewCase.folder || '',
                          reviewCase.album_plan?.library_root,
                        )}
                      </p>
                      <ul className={styles.folderFiles}>
                        {records(folderFinding.details?.checked_files).map((file) => (
                          <li key={textValue(file.file_path)}>
                            <strong>{textValue(file.title)}</strong>
                            <span>
                              {[file.album, file.artist].map(textValue).filter(Boolean).join(' · ')}
                            </span>
                            <small>{textValue(file.file_path)}</small>
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                  {albumView === 'plan' && albumReady && (
                    <section className={styles.albumApplyAll} aria-label="Apply album changes">
                      <div>
                        <strong>
                          Plan ready to apply · {plan.ready} action{plan.ready === 1 ? '' : 's'}
                        </strong>
                        <p>
                          All required decisions are planned and saved. Apply now or leave this
                          album for the next ready batch.
                        </p>
                      </div>
                      {readyActionControls}
                      <div className={styles.readyChanges}>
                        <h4>Planned changes</h4>
                        {readyChangeGroups.length > 0 && (
                          <p>Choose a field to inspect the affected tracks and exact values.</p>
                        )}
                        {readySummaryList}
                        <TrackRequests items={reviewCase.items} />
                        <FileDecisions
                          items={reviewCase.items}
                          onReview={(id) => {
                            const decision = decisionsByFinding.get(id);
                            if (decision) openDecision({ ...decision, finding_ids: [id] });
                          }}
                        />
                        {active &&
                          activeDecision?.risk === 'ready' &&
                          !activeAssignment &&
                          selectedActionDetail}
                      </div>
                    </section>
                  )}

                  {albumView === 'plan' &&
                    !albumReady &&
                    !folderFinding &&
                    !unidentified &&
                    (reviewDecisions.length > 0 || pendingDecisions.length === 0) &&
                    (reviewCase.album_plan || decisions.length > 0) && (
                      <section className={styles.decisionBoard} aria-label="Album decisions">
                        <div className={styles.sectionHeading}>
                          <div>
                            <div className={styles.eyebrow}>Album review</div>
                            <h3>
                              {blockedActionCount && !choiceCount
                                ? `${blockedActionCount} saved action${blockedActionCount === 1 ? '' : 's'} need${blockedActionCount === 1 ? 's' : ''} attention`
                                : reviewDecisions.length
                                  ? `${choiceCount} choice${choiceCount === 1 ? '' : 's'} need${choiceCount === 1 ? 's' : ''} your input${blockedActionCount ? ` · ${blockedActionCount} blocked action${blockedActionCount === 1 ? '' : 's'}` : ''}`
                                  : plan.status === 'waiting'
                                    ? 'Waiting for requested tracks'
                                    : 'Decisions complete'}
                            </h3>
                            <p>
                              {blockedActionCount && !choiceCount
                                ? 'Your choices are saved. Resolve the apply issues below before retrying this album plan.'
                                : releaseChoicePending
                                  ? 'Choose the release first. Track and numbering proposals will be recalculated for that edition.'
                                  : plan.status === 'waiting'
                                    ? 'Your requests are saved. The album stays here until the downloaded files are assigned. Other approved changes can be applied now.'
                                    : reviewDecisions.length
                                      ? 'Resolve these choices before applying the album. Inspect affected tracks to compare exact values.'
                                      : 'All decisions are complete. There are no remaining changes to apply.'}
                            </p>
                          </div>
                        </div>
                        <div className={styles.decisionGrid}>
                          {reviewDecisions.map((decision, decisionIndex) => {
                            const instruction = decisionInstruction(
                              decision,
                              reviewCase.album_plan?.numbering_mode,
                              releaseHasMultipleDiscs,
                            );
                            const hasGroupActions = [
                              'numbering',
                              'title_conventions',
                              'metadata',
                            ].includes(decision.category);
                            const selected = active
                              ? decision.finding_ids.includes(active.id)
                              : false;
                            return (
                              <Fragment key={decision.id}>
                                <article
                                  data-risk={decision.risk}
                                  data-state={
                                    releaseChoicePending &&
                                    decision.category !== 'release_conflicts'
                                      ? 'pending'
                                      : 'needs-review'
                                  }
                                  data-selected={selected || undefined}
                                >
                                  <Button
                                    type="button"
                                    className={styles.decisionSelect}
                                    onClick={() => {
                                      if (hasGroupActions) {
                                        showTrackList(`decision:${decision.id}`);
                                      } else if (decision.category === 'release_conflicts') {
                                        setAlbumView('release');
                                      } else {
                                        openDecision(decision);
                                      }
                                    }}
                                  >
                                    <span className={styles.decisionStep} aria-hidden="true">
                                      {decisionIndex + 1}
                                    </span>
                                    <span>
                                      <small>{decisionCategoryLabel(decision.category)}</small>
                                      <strong>{instruction}</strong>
                                      {decision.summary && decision.summary !== instruction && (
                                        <small>{decision.summary}</small>
                                      )}
                                      {decision.additional_metadata_summary && (
                                        <small>{decision.additional_metadata_summary}</small>
                                      )}
                                      {hasGroupActions && <small>Inspect affected tracks →</small>}
                                    </span>
                                    {reviewDecisions.length > 1 && (
                                      <b>
                                        {decision.action_count} action
                                        {decision.action_count === 1 ? '' : 's'}
                                        {' →'}
                                      </b>
                                    )}
                                  </Button>
                                  {decision.category === 'audio_conflicts' &&
                                    decision.finding_ids.length > 0 &&
                                    decision.finding_ids.every((id) =>
                                      reviewCase.items.some(
                                        (item) =>
                                          item.id === id && item.details?.catalogue_disagreement,
                                      ),
                                    ) && (
                                      <div className={styles.decisionActions}>
                                        <p>
                                          File tags and the selected release agree. AcoustID credits
                                          these recordings differently. Inspect the tracks, or
                                          confirm these proposed assignments together.
                                        </p>
                                        <Button
                                          type="button"
                                          variant="primary"
                                          disabled={mutationBusy || releaseChoicePending}
                                          onClick={() =>
                                            void actGroup(
                                              { ...decision, category: 'catalogue_assignments' },
                                              'fix',
                                            )
                                          }
                                        >
                                          Use these {decision.finding_ids.length} assignments
                                        </Button>
                                      </div>
                                    )}
                                  {hasGroupActions && (
                                    <div className={styles.decisionActions}>
                                      <Button
                                        type="button"
                                        disabled={
                                          busy ||
                                          applyingThisAlbum ||
                                          releaseChoicePending ||
                                          reviewCase.refresh?.state === 'running'
                                        }
                                        variant="primary"
                                        onClick={() => void actGroup(decision, 'fix')}
                                      >
                                        {busyDecision === decision.category
                                          ? `Saving ${decision.action_count} choices…`
                                          : decision.additional_metadata_summary &&
                                              decision.category !== 'numbering'
                                            ? `Plan title changes and album metadata for ${decision.action_count} track${decision.action_count === 1 ? '' : 's'}`
                                            : decision.category === 'numbering'
                                              ? !releaseHasMultipleDiscs
                                                ? `Plan release track numbers for ${decision.action_count} track${decision.action_count === 1 ? '' : 's'}`
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
                                          applyingThisAlbum ||
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
                                        <div className={styles.requestStep}>
                                          <TrackRequests
                                            items={reviewCase.items.filter((item) =>
                                              decision.finding_ids.includes(item.id),
                                            )}
                                          />
                                          <div className={styles.decisionActions}>
                                            <Button
                                              type="button"
                                              disabled={
                                                applyingThisAlbum ||
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
                                                showTrackList('missing');
                                              }}
                                            >
                                              Choose individual tracks →
                                            </Button>
                                          </div>
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
                            <div className={styles.eyebrow}>Planned changes</div>
                            <strong>
                              {plan.ready} supported change{plan.ready === 1 ? '' : 's'}
                            </strong>
                            <p>
                              {plan.status === 'waiting'
                                ? 'These edits stay in your plan while the requested files are received and assigned.'
                                : 'Resolve the remaining album decisions before applying these edits.'}
                            </p>
                          </div>
                          <div className={styles.planBadges}>
                            <StatusChip tone="good" icon="check">
                              {plan.ready} planned
                            </StatusChip>
                          </div>
                        </div>
                        {plan.status === 'waiting' && readySummaryList}
                        <Button
                          type="button"
                          className={styles.secondaryButton}
                          onClick={() => {
                            showTrackList('change');
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
                          already requested. Published downloads are checked automatically; the
                          album stays here until those files are assigned.
                        </p>
                        <Button
                          nativeButton={false}
                          role="link"
                          render={
                            <a
                              href={`/wishlist?q=${encodeURIComponent(reviewCase?.album || '')}`}
                            />
                          }
                        >
                          View album in Wishlist →
                        </Button>
                      </div>
                    </section>
                  )}

                  {albumView === 'plan' &&
                    reviewCase.items.some((item) => item.details?.planned_choice) && (
                      <section className={styles.editPlan} aria-label="Edit saved choices">
                        <small>Saved choices</small>
                        <div className={styles.inlineActions}>
                          {[
                            ...new Set(
                              reviewCase.items.flatMap((item) => {
                                const choice = item.details?.planned_choice as
                                  | { category?: string }
                                  | undefined;
                                return [
                                  ...(choice?.category ? [choice.category] : []),
                                  ...(item.details?.numbering_choice ? ['numbering'] : []),
                                ];
                              }),
                            ),
                          ].map((category) => (
                            <Button
                              variant="ghost"
                              key={category}
                              type="button"
                              disabled={mutationBusy}
                              onClick={() => void actGroup({ category } as ReviewDecision, 'reset')}
                            >
                              Review {category.replaceAll('_', ' ')} again
                            </Button>
                          ))}
                        </div>
                      </section>
                    )}

                  {albumView === 'tracks' && !unidentified && assignments.length > 0 && (
                    <div className={styles.trackWorkspace} data-detail={inspectorOpen || undefined}>
                      <section
                        className={styles.trackMatrix}
                        aria-label="Complete album track assignment"
                        tabIndex={-1}
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
                            {proposedChangeCount > 0 && (
                              <StatusChip tone="info" icon="arrow">
                                {proposedChangeCount} proposed change
                                {proposedChangeCount === 1 ? '' : 's'}
                              </StatusChip>
                            )}
                            {metadataDecisionCount > 0 && (
                              <StatusChip tone="warning" icon="warning">
                                {metadataDecisionCount} metadata decision
                                {metadataDecisionCount === 1 ? '' : 's'}
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
                          <div className={styles.releaseReference}>
                            <span>{primaryCandidate?.release_title || 'Selected release'}</span>
                            <Button variant="ghost" onClick={() => setAlbumView('release')}>
                              Compare editions →
                            </Button>
                          </div>
                          {reviewCase.album_plan?.intent_scope && (
                            <span>
                              {reviewCase.album_plan.intent_inferred
                                ? 'One playlist track owned; other tracks are uncollected'
                                : `Intent: ${reviewCase.album_plan.intent_scope} album`}
                            </span>
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
                            {pairedConflictCount === 1 ? ' pairs' : 's pair'} a local file with an
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
                          (intentFinding ? (
                            <section
                              className={styles.missingChoices}
                              aria-label="Choose album completeness"
                              tabIndex={-1}
                            >
                              <h4>Choose which missing tracks you want</h4>
                              <CompletenessChoice
                                key={reviewCase.case_id}
                                finding={intentFinding}
                                busy={mutationBusy}
                                onChoose={(choice) => void act(intentFinding, 'fix', choice)}
                              />
                            </section>
                          ) : missingFindings.length > 0 ? (
                            <details
                              className={styles.missingRequests}
                              open={
                                trackFilter === 'missing' ||
                                assignmentsByKey.get(activeRowKey || '')?.status === 'missing'
                              }
                            >
                              <summary>
                                {missingAssignments.length} missing track
                                {missingAssignments.length === 1 ? '' : 's'} · Choose tracks to
                                request
                              </summary>
                              <MissingTracksChoice
                                key={reviewCase.case_id}
                                scope={reviewCase.album_plan?.intent_scope}
                                findings={missingFindings}
                                busy={mutationBusy}
                                onRequest={(ids) => void requestAllMissing(ids.length, ids)}
                                onChangeScope={() => void reconsiderScope()}
                              />
                            </details>
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
                          {trackView === 'release'
                            ? releaseOrderedAssignments
                                .filter(matchingFilter)
                                .map(renderAssignmentRow)
                            : [
                                {
                                  key: 'attention',
                                  tone: 'warning',
                                  rows: visibleAttention,
                                  label: `${visibleAttention.length} track${visibleAttention.length === 1 ? '' : 's'} needing review`,
                                },
                                {
                                  key: 'files',
                                  tone: 'danger',
                                  rows: visibleFiles,
                                  label: `${visibleFiles.length} saved file action${visibleFiles.length === 1 ? '' : 's'}`,
                                },
                                {
                                  key: 'changes',
                                  tone: 'info',
                                  rows: visibleChanges,
                                  label: `↻ ${visibleChanges.length} proposed track change${visibleChanges.length === 1 ? '' : 's'}`,
                                },
                                {
                                  key: 'missing',
                                  tone: 'danger',
                                  rows: visibleMissing,
                                  label: `${visibleMissing.length} absent release track${visibleMissing.length === 1 ? '' : 's'}`,
                                },
                                {
                                  key: 'uncollected',
                                  tone: 'neutral',
                                  rows: visibleUncollected,
                                  label: `${visibleUncollected.length} tracks outside your collection`,
                                },
                                {
                                  key: 'settled',
                                  tone: 'good',
                                  rows: visibleSettled,
                                  label: `✓ ${visibleSettled.length} settled release slot${visibleSettled.length === 1 ? '' : 's'}`,
                                },
                              ]
                                .filter((group) => group.rows.length > 0)
                                .map((group) => (
                                  <div
                                    key={group.key}
                                    className={styles.assignmentGroup}
                                    data-tone={group.tone}
                                  >
                                    <Button
                                      type="button"
                                      aria-expanded={openGroups.includes(group.key)}
                                      onClick={() => toggleGroup(group.key)}
                                    >
                                      {openGroups.includes(group.key) ? '▾' : '▸'} {group.label}
                                    </Button>
                                    {openGroups.includes(group.key) && (
                                      <>
                                        {group.key === 'missing' && hasIntentDecision && (
                                          <p>
                                            Choose missing tracks above, or review album
                                            completeness on Plan.
                                          </p>
                                        )}
                                        {group.rows.map(renderAssignmentRow)}
                                      </>
                                    )}
                                  </div>
                                ))}
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
                          (() => {
                            const rows = assignments.filter(
                              (row) =>
                                row.finding_id != null ||
                                row.historical_finding_id != null ||
                                row.planned_choice,
                            );
                            const index = rows.findIndex(
                              (row) => assignmentKey(row) === activeRowKey,
                            );
                            const go = (row: AlbumAssignment | undefined) => {
                              if (!row) return;
                              setTrackFilter('');
                              setActiveId(row.finding_id ?? null);
                              setActiveRowKey(assignmentKey(row));
                              setInspectorOpen(true);
                            };
                            return (
                              <nav
                                className={styles.decisionNavigation}
                                aria-label="Track decision navigation"
                              >
                                <Button disabled={index <= 0} onClick={() => go(rows[index - 1])}>
                                  ← Previous decision
                                </Button>
                                <Button
                                  disabled={index < 0 || index >= rows.length - 1}
                                  onClick={() => go(rows[index + 1])}
                                >
                                  Next decision →
                                </Button>
                              </nav>
                            );
                          })()}
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
                                  (intentFinding || missingFindings.length > 0) ? (
                                    <Button
                                      onClick={() => {
                                        setInspectorOpen(false);
                                        requestAnimationFrame(() => {
                                          const choices = document.querySelector<HTMLElement>(
                                            '[aria-label="Choose missing tracks"], [aria-label="Choose album completeness"]',
                                          );
                                          const disclosure = choices?.closest('details');
                                          if (disclosure) disclosure.open = true;
                                          choices?.focus({ preventScroll: true });
                                          choices?.scrollIntoView({ block: 'center' });
                                        });
                                      }}
                                    >
                                      Choose missing tracks →
                                    </Button>
                                  ) : (
                                    <Button
                                      onClick={() => {
                                        setAlbumView('plan');
                                        void reconsiderScope();
                                      }}
                                    >
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

                  {albumView === 'tracks' && !unidentified && assignments.length === 0 && (
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
                        decisionReason={reviewCase.album_plan?.release_decision_reason}
                        libraryRelease={reviewCase.album_plan?.current_release}
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
                              releaseLookupVersion.current += 1;
                              setReleaseIdInput(event.target.value);
                              setManualRelease(null);
                              setPreviewCandidate(0);
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
                    !unidentified &&
                    active &&
                    !activeAssignment &&
                    !activeDecision &&
                    selectedActionDetail}
                </div>
                {albumView !== 'tracks' && (
                  <aside className={styles.tabContext} aria-label={`${albumView} context`}>
                    {albumView === 'plan' && !unidentified && reviewCase.album_plan && (
                      <section className={styles.planContext} aria-label="Album plan context">
                        <div>
                          <div className={styles.eyebrow}>Album context</div>
                          <h4>What this decision affects</h4>
                        </div>
                        <dl>
                          <div>
                            <dt>Selected release</dt>
                            <dd>
                              {primaryCandidate?.release_title || 'Release title unavailable'}
                              {primaryCandidate?.release_artist && (
                                <span className={styles.releaseArtist}>
                                  {primaryCandidate.release_artist}
                                </span>
                              )}
                              {primaryCandidate && (
                                <ReleaseBadges
                                  format={primaryCandidate.format}
                                  country={primaryCandidate.country}
                                  disambiguation={primaryCandidate.disambiguation}
                                />
                              )}
                              <div className={styles.releaseReference}>
                                <ProviderReleaseId
                                  source={reviewCase.album_plan.source}
                                  releaseId={reviewCase.album_plan.release_id}
                                />
                              </div>
                            </dd>
                          </div>
                          <div>
                            <dt>
                              Track coverage
                              <HelpHint label="track coverage">
                                Assigned slots have a proposed local file. A match can still need
                                review; coverage does not mean that every file is verified or will
                                be changed.
                              </HelpHint>
                            </dt>
                            <dd>
                              {mappedSlots} of {totalSlots || '?'} release slots assigned
                            </dd>
                          </div>
                          <div>
                            <dt>Album scope</dt>
                            <dd>
                              {reviewCase.album_plan.intent_inferred
                                ? 'One playlist track owned; other tracks are uncollected'
                                : reviewCase.album_plan.intent_scope ||
                                  (hasIntentDecision
                                    ? 'Choice pending'
                                    : reviewCase.album_plan.counts.missing
                                      ? albumReady
                                        ? 'Missing tracks have planned resolutions'
                                        : 'Resolve track assignments first'
                                      : 'Full release coverage')}
                            </dd>
                          </div>
                          {albumTitleChange && (
                            <div>
                              <dt id="review-shared-album-title">Shared album title</dt>
                              <dd>
                                <span className={styles.muted}>
                                  {textValue(albumTitleChange.old)}
                                </span>
                                {' → '}
                                <strong>{textValue(albumTitleChange.new)}</strong>
                                <p className={styles.muted}>
                                  Updates the album’s shared library title once.
                                  {assignments
                                    .filter((row) => row.file_path)
                                    .every(
                                      (row) =>
                                        textValue(row.current_tags?.album) ===
                                        textValue(albumTitleChange.new),
                                    )
                                    ? ' The file album tags already use this title.'
                                    : ' Changes to individual file album tags are listed with their tracks.'}
                                </p>
                              </dd>
                            </div>
                          )}
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
                            <dd className={styles.numberingSummary}>
                              {numberingSchemeLabel(
                                releaseHasMultipleDiscs,
                                reviewCase.album_plan.numbering_mode,
                              )}
                              {!reviewDecisions.some(
                                (decision) => decision.category === 'numbering',
                              ) &&
                                reviewCase.items.some(
                                  (item) =>
                                    item.details?.routine_numbering &&
                                    [
                                      'track_number',
                                      'disc_number',
                                      'library_track_number',
                                      'library_disc_number',
                                    ].some((field) => field in record(item.details?.changes)),
                                ) && (
                                  <NumberingChoice
                                    key={caseId}
                                    assignments={assignments}
                                    scheme={numberingSchemeLabel(
                                      releaseHasMultipleDiscs,
                                      reviewCase.album_plan.numbering_mode,
                                    )}
                                    busy={mutationBusy || releaseChoicePending}
                                    onChoose={(choice) =>
                                      void actGroup(
                                        { category: 'numbering' } as ReviewDecision,
                                        choice,
                                      )
                                    }
                                  />
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
                                  <code title={folder}>
                                    {libraryRelativePath(
                                      folder,
                                      reviewCase.album_plan?.library_root,
                                    )}
                                  </code>
                                </li>
                              ))}
                            </ul>
                          </section>
                        )}
                        <div className={`${styles.actions} ${styles.planContextActions}`}>
                          <Button type="button" onClick={() => showTrackList()}>
                            Review track assignments
                          </Button>
                          {reviewCase.album_plan.intent_scope && (
                            <Button
                              type="button"
                              disabled={mutationBusy}
                              onClick={() => void reconsiderScope()}
                            >
                              {reviewCase.album_plan.intent_inferred
                                ? 'Choose tracks to collect'
                                : 'Review album scope'}
                            </Button>
                          )}
                        </div>
                      </section>
                    )}

                    {albumView === 'plan' &&
                      !albumReady &&
                      plan.status !== 'waiting' &&
                      proposedChangeGroups.length > 0 && (
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
                                  if (group.key === 'library_album_title') setAlbumView('plan');
                                  else showTrackList(group.key);
                                }}
                              >
                                <span>
                                  <strong>{group.label}</strong>
                                  <small>{group.valueSummary}</small>
                                </span>
                                <b>
                                  {group.key === 'library_album_title'
                                    ? 'Whole album'
                                    : `${group.count} track${group.count === 1 ? '' : 's'}`}{' '}
                                  →
                                </b>
                              </Button>
                            ))}
                          </div>
                        </section>
                      )}

                    {albumView === 'release' && (
                      <ReleaseComparison
                        decisionReason={reviewCase.album_plan?.release_decision_reason}
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
