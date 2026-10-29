import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useConfirmDialog } from '@/components/dialog/use-confirm-dialog';
import { Button } from '@/components/form/form';
import { MetadataComparison } from '@/components/metadata-comparison/metadata-comparison';

import type { FindingTypeInfo } from '../../tools/-tools.groups';

import {
  resetAlbumScope,
  applyReviewDecision,
  reopenReviewAssignment,
  applyReviewDecisionGroup,
  fetchReviewCase,
  pinReviewRelease,
  reanalyzeReviewCase,
  startMissingTrackRequests,
  stopCollectingAlbum,
  startReadyChanges,
  type AlbumAssignment,
  type ReviewCase,
  type ReviewDecision,
  type ReviewFinding,
} from '../-review.api';
import { fetchFindingTypes } from '../../tools/-tools.api';
import { ApplyPlanPreview, type ReadyPreview } from './apply-plan-preview';
import { AssignmentRow } from './assignment-row';
import { TrackRequests } from './file-decisions';
import { buildAlbumModel, groupReadyChanges, readyFieldGroup } from './review-album-model';
import { requireChoice, emptyPlan, AlbumView, TrackView } from './review-labels';
import styles from './review-page.module.css';
import { assignmentKey, record, records, textValue } from './review-presentation';
import { SelectedActionDetail } from './selected-action-detail';
import { useReconciliationStatus } from './use-reconciliation-status';
import { useRequestScope } from './use-request-scope';
import { useReviewOperation } from './use-review-operation';
import { useReviewQueue } from './use-review-queue';

export function useReviewController() {
  const { confirm, dialog } = useConfirmDialog();
  const initialParams = useMemo(() => new URLSearchParams(window.location.search), []);
  const initialMode = initialParams.get('mode');
  const [queueExpanded, setQueueExpanded] = useState(false);
  const queueToggleRef = useRef<HTMLButtonElement | null>(null);
  const caseListRef = useRef<HTMLDivElement | null>(null);
  const [caseId, setCaseId] = useState(() => initialParams.get('case') || '');
  const caseRequests = useRequestScope(caseId);
  const selectedCaseRef = useRef(caseId);
  selectedCaseRef.current = caseId;
  const refreshRef = useRef<() => Promise<boolean>>(async () => true);
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
  const [catalog, setCatalog] = useState<Record<string, FindingTypeInfo>>({});
  const [busy, setBusy] = useState(false);
  const [reanalyzingCase, setReanalyzingCase] = useState<string | null>(null);
  const reanalyzing = reanalyzingCase === caseId;
  const [busyDecision, setBusyDecision] = useState('');
  const [error, setErrorValue] = useState('');
  const [errorCase, setErrorCase] = useState<string | null>(null);
  const setError = useCallback((value: string, scope?: string | null) => {
    setErrorValue(value);
    setErrorCase(value ? (scope === undefined ? selectedCaseRef.current || null : scope) : null);
  }, []);
  const [notice, setNotice] = useState('');
  const advanceAfterSave = useRef<{ caseId: string; from: number; ids: number[] } | null>(null);

  const {
    queue,
    queueTotals,
    page,
    setPage,
    query,
    setQuery,
    category,
    setCategory,
    queueMode,
    setQueueMode,
    searchInput,
    setSearchInput,
    queueLoading,
    queueError,
    refreshQueue,
  } = useReviewQueue({ initialMode, selectedCaseRef, setCaseId, setError });
  const { scanBusy, globalNotice, setGlobalNotice, scanStatus, lastRun, runScan } =
    useReconciliationStatus({ setError });
  const {
    applyBusy,
    setApplyBusy,
    operationCases,
    applyProgress,
    setApplyProgress,
    operationCase,
    rememberOperation,
  } = useReviewOperation({ setError, setNotice, setGlobalNotice, selectedCaseRef, refreshRef });
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(''), 6000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (caseListRef.current) caseListRef.current.scrollTop = 0;
  }, [page, queueMode, category]);

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
    const request = caseRequests.start();
    setReviewCase(null);
    setError('');
    void fetchReviewCase(caseId, request.signal)
      .then((result) => {
        if (!live || !request.isCurrent()) return;
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
        setOpenGroups(['attention', 'changes', 'files']);
      })
      .catch((cause: unknown) => {
        if (live && request.isCurrent()) setError((cause as Error).message);
      });
    return () => {
      live = false;
      request.cancel();
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
            : ['attention', 'changes', 'files']),
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
    if (target?.status === 'change') {
      const group = fileAction(target) ? 'files' : 'changes';
      setOpenGroups((current) => (current.includes(group) ? current : [...current, group]));
    }
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
    setOpenGroups(['attention', 'changes', 'files']);
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
      setOpenGroups(['attention', 'changes', 'files', 'missing', 'settled']);
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

  const refreshError = useRef<string | null>(null);
  const refresh = useCallback(async () => {
    const requestedId = caseId;
    const request = caseRequests.start();
    try {
      if (!requestedId) {
        const result = await refreshQueue();
        if (result?.items[0] && !selectedCaseRef.current) setCaseId(result.items[0].case_id);
        return true;
      }
      const updated = await fetchReviewCase(requestedId, request.signal);
      if (selectedCaseRef.current !== requestedId || !request.isCurrent()) return true;
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
      if (selectedCaseRef.current !== requestedId || !request.isCurrent()) return true;
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
      if (selectedCaseRef.current === requestedId && request.isCurrent()) {
        refreshError.current = (cause as Error).message;
        setError(refreshError.current);
      }
      return false;
    }
  }, [caseId, refreshQueue, queue, page]);
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
    const request = caseRequests.start();
    setError('');
    setNotice('');
    setReanalyzingCase(requestedId);
    try {
      await reanalyzeReviewCase(requestedId, refreshCatalogue);
      if (selectedCaseRef.current === requestedId && request.isCurrent()) {
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
      if (selectedCaseRef.current === caseId) {
        const request = caseRequests.start();
        void fetchReviewCase(caseId, request.signal)
          .then((result) => {
            if (request.isCurrent()) setReviewCase(result);
          })
          .catch(() => undefined);
      }
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
        { manual: candidate === manualRelease, audio_copy: Boolean(candidate.audio_copy_of_video) },
      );
      if (!result.success) throw new Error(result.error || 'Could not select this release');
      if (selectedCaseRef.current !== requestedCase) return;
      setNotice(result.message || 'Release selected; updating this album plan');
      const request = caseRequests.start();
      const updated = await fetchReviewCase(reviewCase.case_id, request.signal);
      if (!request.isCurrent() || selectedCaseRef.current !== requestedCase) return;
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

  return {
    act,
    actGroup,
    active,
    activeAssignment,
    activeDecision,
    activeRowKey,
    albumNumber,
    albumReady,
    albumTitleChange,
    albumView,
    applyBusy,
    applyProgress,
    applyReady,
    applyingThisAlbum,
    assignments,
    assignmentsByKey,
    blockedActionCount,
    busy,
    busyDecision,
    caseId,
    caseListRef,
    category,
    choiceCount,
    compactSelectedActionDetail,
    decisions,
    decisionsByFinding,
    dialog,
    error,
    errorCase,
    filterCount,
    folderFinding,
    globalNotice,
    hasIntentDecision,
    inspectReleaseId,
    inspectorOpen,
    inspectorRef,
    intentFinding,
    lastRun,
    leaveAlbum,
    mappedSlots,
    matchingFilter,
    metadataDecisionCount,
    missingAssignments,
    missingFindings,
    mutationBusy,
    nextCaseId,
    notice,
    openDecision,
    openGroups,
    operationCase,
    page,
    pageCount,
    pairedConflictCount,
    pendingDecisions,
    pinCandidate,
    plan,
    previewCandidate,
    previousCaseId,
    primaryCandidate,
    proposedChangeCount,
    proposedChangeGroups,
    query,
    queue,
    queueError,
    queueExpanded,
    queueLoading,
    queueMode,
    queueToggleRef,
    queueTotals,
    readyActionControls,
    readyChangeGroups,
    readyIncludedInGroup,
    readySummaryList,
    reanalyzeAlbum,
    reanalyzing,
    reconsiderScope,
    refresh,
    refreshQueue,
    releaseCandidates,
    releaseChoicePending,
    releaseHasMultipleDiscs,
    releaseIdInput,
    releaseLookupVersion,
    releaseOrderedAssignments,
    renderAssignmentRow,
    requestAllMissing,
    retryOrganization,
    reviewCase,
    reviewDecisions,
    runScan,
    scanBusy,
    scanStatus,
    searchInput,
    selectCase,
    selectedActionDetail,
    selectedCaseRef,
    setActiveId,
    setActiveRowKey,
    setAlbumView,
    setBusy,
    setCaseId,
    setCategory,
    setError,
    setGlobalNotice,
    setInspectorOpen,
    setManualRelease,
    setNotice,
    setPage,
    setPreviewCandidate,
    setQuery,
    setQueueExpanded,
    setQueueMode,
    setReleaseIdInput,
    setSearchInput,
    setTrackFilter,
    setTrackView,
    settledSlots,
    showTrackList,
    sourceFolders,
    stopCollecting,
    toggleGroup,
    totalSlots,
    trackFilter,
    trackListScroll,
    trackView,
    unidentified,
    unidentifiedFinding,
    visibleAttention,
    visibleChanges,
    visibleFiles,
    visibleMissing,
    visibleSettled,
    visibleUncollected,
    wishlistedCount,
  };
}

export type ReviewController = ReturnType<typeof useReviewController>;
