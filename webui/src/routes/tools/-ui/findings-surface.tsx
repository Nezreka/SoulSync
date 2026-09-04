/**
 * The findings surface — library health, the grouped inbox, and the finding
 * list that lives inside whichever group is open.
 *
 * This replaced a flat, paginated wall of rows. What it keeps from that
 * version, deliberately:
 *
 * 1. **Fix All runs in the background.** At library scale a synchronous
 *    fix-all outlives every browser and proxy timeout, and the user was told
 *    it failed while the server quietly kept fixing. The start endpoint
 *    returns immediately, a 2s poll drives the bulk bar, and a reload mid-run
 *    picks the progress back up (`_checkBulkFixResume`).
 * 2. Every per-row behaviour — select, fix (with its per-type prompts),
 *    dismiss, detail expansion, pagination. The open group hosts the SAME
 *    list component, scoped by `finding_type`, so none of it was rebuilt.
 *
 * What it drops, deliberately:
 *
 * - The auto-switch-to-All-Status notice. It existed because an empty pending
 *   list looked like a bug; the status segmented control now shows the count
 *   in every status at all times, so "why is this empty" answers itself and
 *   the filter never changes itself behind the user's back.
 * - The four count pills and the per-job chips. Counts moved into the status
 *   control; per-job counts belong on the job's own card.
 *
 * Bulk scoping is now by finding TYPE rather than by job, which is what makes
 * a one-click group fix safe: `fix_action` means different things per type
 * ('delete' removes a file for an orphan, and names the track to KEEP for a
 * duplicate), so the backend refuses an action that spans more than one type.
 */

import { FindingsAlbumGrid } from './findings-album-grid';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { FindingGroup, FindingTypeInfo } from '../-tools.groups';
import type {
  BulkFixStatus,
  CacheHealthStats,
  RepairFinding,
  RepairJob,
  RepairJobRun,
} from '../-tools.types';

import {
  bulkFindingAction,
  clearFindings,
  dismissFinding,
  dismissFindingChecked,
  dismissFindingType,
  fetchBulkFixStatus,
  fetchCacheHealth,
  fetchFindingCounts,
  fetchFindingGroups,
  fetchFindingTypes,
  fetchRepairFindings,
  fixFinding,
  reopenFinding,
  startBulkFix,
  stopBulkFix,
} from '../-tools.api';
import {
  bulkFixLoopMessage,
  bulkFixRunMessage,
  cacheHealthLabel,
  cacheHealthScore,
  findingFilePath,
  findingRowFixLabel,
  findingSeverityIcon,
  findingStatusBadge,
  findingTypeLabel,
  findingsBulkBarState,
  findingsPagination,
  formatCacheAge,
  MASS_ORPHAN_THRESHOLD,
  normalizeFindingsPageSize,
  REPAIR_DEFAULT_PAGE_SIZE,
  REPAIR_PAGE_SIZE_OPTIONS,
} from '../-tools.core';
import { safeFixablePending, visibleGroups } from '../-tools.groups';
import { FindingDetail } from './finding-detail';
import { useFindingPrompts } from './finding-prompts';
import { FindingsInbox } from './findings-inbox';
import { HealthHero } from './health-hero';

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
}

/** The vanilla's poll interval for a background Fix All run. */
const BULK_FIX_POLL_MS = 2000;

const PAGE_SIZE_KEY = 'repairFindingsPageSize';

function readStoredPageSize(): number {
  try {
    return normalizeFindingsPageSize(localStorage.getItem(PAGE_SIZE_KEY));
  } catch {
    return REPAIR_DEFAULT_PAGE_SIZE;
  }
}

/** Finding types whose fix needs a question answered before it can run. Keyed
 *  by TYPE now, not by job — a job can emit more than one type. */
const TYPE_ORPHAN = 'orphan_file';
const TYPE_DEAD = 'dead_file';
const TYPE_ACOUSTID = 'acoustid_mismatch';
const TYPE_RETAG = 'library_retag';
const TYPE_BACKFILL = 'missing_discography_track';
const TYPE_QUALITY = 'quality_upgrade';

/** Above this many files, a whole-group orphan DELETE goes through the
 *  type-the-phrase dialog. Same number the filter-wide Fix All has always
 *  used; the group button is that button's successor, so it inherits the
 *  gate rather than inventing a stricter one. */
const GROUP_MASS_DELETE_THRESHOLD = 50;

const STATUS_SEGMENTS = [
  { value: 'pending', label: 'Open' },
  { value: 'resolved', label: 'Fixed' },
  { value: 'dismissed', label: 'Dismissed' },
  { value: '', label: 'All' },
] as const;

const SORT_OPTIONS = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'severity', label: 'Severity' },
  // Severity alone could never order an upgrade backlog - every below-profile
  // track is 'info', so they all tied and fell back to scan order. These two
  // sort on the audio itself.
  { value: 'quality', label: 'Worst quality first' },
  { value: 'quality_desc', label: 'Best quality first' },
  { value: 'path', label: 'File path' },
] as const;

export interface FindingsSurfaceProps {
  /** The job list, for the job filter. */
  jobs: RepairJob[];
  /** Run history — the hero's trend line reads findings-per-run off it. */
  runs: RepairJobRun[];
  /** Library size, for the per-1,000-tracks health normalisation. */
  trackCount: number | null;
  /** A jump from the run history: scope the surface to one job's open
   *  findings. The token re-fires the same job. */
  focusJob?: { jobId: string; token: number } | null;
  /** `updateRepairStatus()` — refresh the pending badge after any mutation. */
  onStatusChanged: () => void;
}

export function FindingsSurface({
  jobs,
  runs,
  trackCount,
  focusJob,
  onStatusChanged,
}: FindingsSurfaceProps) {
  /** job_id → display name, falling back to a de-underscored id for a job the
   *  list hasn't loaded (or one that has since been removed). */
  const jobLabel = useCallback(
    (jobId: string) =>
      jobs.find((job) => job.job_id === jobId)?.display_name || (jobId || '').replace(/_/g, ' '),
    [jobs],
  );

  const [jobFilter, setJobFilter] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('pending');
  const [sort, setSort] = useState('newest');
  const [query, setQuery] = useState('');
  const [pageSize, setPageSize] = useState(readStoredPageSize);
  const [page, setPage] = useState(0);

  /** Which group is expanded. Exactly one at a time: the open group hosts the
   *  single finding list, which is what lets every row feature survive
   *  unchanged instead of being rebuilt per group. */
  const [openType, setOpenType] = useState('');
  // list | album | artist. Resets whenever a different type is opened, because
  // a grouping that made sense for upgrades rarely does for the next type.
  const [groupView, setGroupView] = useState<'list' | 'album' | 'artist'>('list');

  const [groups, setGroups] = useState<FindingGroup[]>([]);
  const [types, setTypes] = useState<FindingTypeInfo[]>([]);
  const [items, setItems] = useState<RepairFinding[] | null>(null);
  const [total, setTotal] = useState(0);
  /**
   * The page the SERVER echoed back, which is what the pagination is built
   * on. It is normally the page we asked for, but if the backend ever clamps
   * an out-of-range request the highlight and the prev/next arithmetic must
   * follow the server, not the ask.
   */
  const [serverPage, setServerPage] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [counts, setCounts] = useState<Awaited<ReturnType<typeof fetchFindingCounts>> | null>(null);
  const [cacheHealth, setCacheHealth] = useState<CacheHealthStats | null>(null);

  const [selected, setSelected] = useState<ReadonlySet<number>>(() => new Set());
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(() => new Set());
  const [busyFix, setBusyFix] = useState<ReadonlySet<number>>(() => new Set());

  const [bulkRun, setBulkRun] = useState<BulkFixStatus | null>(null);
  const bulkTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const prompts = useFindingPrompts();

  const typeMap = useMemo(() => {
    const map = new Map<string, FindingTypeInfo>();
    for (const info of types) map.set(info.type, info);
    return map;
  }, [types]);
  const typeInfo = useCallback((findingType: string) => typeMap.get(findingType), [typeMap]);

  /** A non-empty search escapes the grouping: people search for a path or a
   *  title, not for a category, and making them guess the category first is
   *  the failure the inbox is supposed to remove. */
  const searching = query.trim().length > 0;
  const listType = searching ? '' : openType;

  // ── Loading ────────────────────────────────────────────────────────────────

  /** Set below, so `loadCounts` can call the watcher without the two
   *  useCallbacks depending on each other in a cycle. */
  const watchBulkFixRef = useRef<() => void>(() => {});

  const loadCounts = useCallback(async () => {
    // `_checkBulkFixResume` runs at the TOP of every dashboard load, not just
    // on open — so a run started from another tab is picked up by the next
    // refresh rather than only by a re-entry into this page.
    void fetchBulkFixStatus().then((status) => {
      if (status?.running) watchBulkFixRef.current();
    });
    try {
      setCounts(await fetchFindingCounts());
    } catch {
      setCounts(null);
      return;
    }
    setCacheHealth(await fetchCacheHealth());
  }, []);

  const loadGroups = useCallback(async () => {
    setGroups(await fetchFindingGroups());
  }, []);

  const loadFindings = useCallback(async () => {
    // Nothing is open and nothing is being searched: the inbox IS the view,
    // and fetching 30 rows nobody asked to see was most of what made the old
    // page slow to arrive.
    if (!searching && !openType) {
      setItems(null);
      setTotal(0);
      return;
    }
    try {
      const data = await fetchRepairFindings({
        jobId: jobFilter,
        severity: severityFilter,
        status: statusFilter,
        findingType: listType,
        sort,
        q: query.trim(),
        page,
        limit: pageSize,
      });
      setLoadError(null);
      setSelected(new Set());
      setBusyFix(new Set());
      setTotal(data.total);
      setServerPage(data.page);
      setItems(data.items);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
    }
  }, [
    jobFilter,
    listType,
    openType,
    page,
    pageSize,
    query,
    searching,
    severityFilter,
    sort,
    statusFilter,
  ]);

  const refreshAll = useCallback(() => {
    setSelected(new Set());
    void loadCounts();
    void loadGroups();
    void loadFindings();
    onStatusChanged();
  }, [loadCounts, loadFindings, loadGroups, onStatusChanged]);

  // ── The background Fix All run ─────────────────────────────────────────────

  const watchBulkFixRun = useCallback(() => {
    if (bulkTimer.current) return; // already watching

    const poll = async () => {
      const status = await fetchBulkFixStatus();
      if (!status) return; // transient — keep polling

      if (status.running) {
        setBulkRun(status);
        return;
      }

      // Finished, or nothing ever ran on this server.
      if (bulkTimer.current) clearInterval(bulkTimer.current);
      bulkTimer.current = null;
      setBulkRun(null);
      if (status.total) {
        const { message, type } = bulkFixRunMessage(status);
        toast(message, type);
      }
      refreshAll();
    };

    bulkTimer.current = setInterval(() => void poll(), BULK_FIX_POLL_MS);
    void poll();
  }, [refreshAll]);

  watchBulkFixRef.current = watchBulkFixRun;

  useEffect(
    () => () => {
      if (bulkTimer.current) clearInterval(bulkTimer.current);
      bulkTimer.current = null;
    },
    [],
  );

  useEffect(() => {
    void loadCounts();
    void loadGroups();
    void fetchFindingTypes().then(setTypes);
  }, [loadCounts, loadGroups]);

  useEffect(() => {
    void loadFindings();
  }, [loadFindings]);

  /** Arriving from a run row. Reset everything that could hide the rows the
   *  user just asked for — a stale search or a dismissed-status filter would
   *  make the jump land on an empty surface. */
  useEffect(() => {
    if (!focusJob) return;
    setJobFilter(focusJob.jobId);
    setStatusFilter('pending');
    setSeverityFilter('');
    setQuery('');
    setOpenType('');
    setPage(0);
  }, [focusJob]);

  // ── Filters ────────────────────────────────────────────────────────────────

  const changePageSize = useCallback((value: string) => {
    const size = normalizeFindingsPageSize(value);
    setPageSize(size);
    try {
      localStorage.setItem(PAGE_SIZE_KEY, String(size));
    } catch {
      // Private-mode storage failures are non-fatal, as in the vanilla.
    }
    setPage(0);
  }, []);

  const toggleOpenGroup = useCallback((findingType: string) => {
    setOpenType((current) => (current === findingType ? '' : findingType));
    setPage(0);
    setSelected(new Set());
  }, []);

  /** A bar segment click opens that group and scrolls it into view. */
  const pickType = useCallback((findingType: string) => {
    setOpenType(findingType);
    // The bar is built from PENDING rows, so a click from it means "show me
    // these" — landing on a group the dismissed filter then hides would look
    // like the click did nothing.
    setStatusFilter('pending');
    setPage(0);
    // Deferred: the group's body only exists after the state lands.
    setTimeout(() => {
      document
        .getElementById(`repair-group-${findingType}`)
        // Optional-called: jsdom has no scrollIntoView, and scrolling is not
        // worth throwing inside a timer nobody is awaiting.
        ?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    }, 60);
  }, []);

  // ── Selection ──────────────────────────────────────────────────────────────

  const toggleSelect = useCallback((id: number, checked: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(
    (checked: boolean) => {
      setSelected(checked ? new Set((items || []).map((finding) => finding.id)) : new Set());
    },
    [items],
  );

  const toggleDetail = useCallback((id: number) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // ── Per-finding actions ────────────────────────────────────────────────────

  const dismissOne = useCallback(
    async (id: number) => {
      try {
        await dismissFinding(id);
      } catch (error) {
        // Was a bare `return`: the row stayed put with no toast and no
        // refresh, so a failed dismiss was indistinguishable from a missed
        // click, and people clicked it again and again.
        toast(`Could not dismiss finding: ${(error as Error).message}`, 'error');
        return;
      }
      refreshAll();
    },
    [refreshAll],
  );

  /** The undo half of dismiss. Dismiss suppresses that finding forever, which
   *  is only safe to offer freely because this exists. */
  const reopenOne = useCallback(
    async (id: number) => {
      if (await reopenFinding(id)) {
        toast('Finding reopened', 'success');
        refreshAll();
      } else {
        toast('Could not reopen that finding', 'error');
      }
    },
    [refreshAll],
  );

  /** `fixRepairFinding`. The per-type prompts run BEFORE the button is disabled. */
  const fixOne = useCallback(
    async (finding: RepairFinding) => {
      const type = finding.finding_type;
      let fixAction: string | null = null;

      if (type === TYPE_ORPHAN) {
        fixAction = await prompts.promptOrphan();
        if (!fixAction) return;
      }
      if (type === TYPE_DEAD) {
        fixAction = await prompts.promptDeadFile();
        if (!fixAction) return;
      }
      if (type === TYPE_ACOUSTID) {
        // an ambiguous finding carries its candidate recordings - hand them to
        // the dialog so the user can pick which one to retag/relocate as
        const d = (finding.details || {}) as { ambiguous?: boolean; candidates?: string[] };
        fixAction = await prompts.promptAcoustid(d.ambiguous ? d.candidates : undefined);
        if (!fixAction) return;
      }
      if (type === TYPE_QUALITY) {
        fixAction = await prompts.promptQuality();
        if (!fixAction) return;
        if (fixAction === 'ignore') {
          await dismissOne(finding.id);
          return;
        }
      }
      if (type === TYPE_RETAG && !finding.details?.has_manual_conflict) {
        // Nothing to settle on this row — the plain apply is the whole action.
      } else if (type === TYPE_RETAG) {
        fixAction = await prompts.promptRetag(1, 1);
        if (!fixAction) return;
        if (fixAction === 'safe') fixAction = null;
      }
      if (type === TYPE_BACKFILL) {
        const choice = await prompts.promptBackfill(1);
        if (!choice) return;
        if (choice === 'dismiss') {
          await dismissOne(finding.id);
          return;
        }
        // 'add_to_wishlist' falls through with no fix_action — the handler
        // already adds to the wishlist by default.
      }

      // A finding with no catalogue subject cannot be re-downloaded — the fix
      // is a plain delete, and unlike every prompt above it has no dialog of
      // its own to stop at. Confirm it here rather than let one click remove a
      // file from disk.
      if (findingRowFixLabel(finding) === 'Delete File') {
        const confirmed = await window.showConfirmDialog?.({
          title: 'Delete File',
          message: `Permanently delete ${findingFilePath(finding) || 'this file'} from disk? It is not in your library, so nothing will be queued to replace it.`,
          confirmText: 'Delete',
          destructive: true,
        });
        if (!confirmed) return;
      }

      setBusyFix((current) => new Set(current).add(finding.id));
      try {
        const result = await fixFinding(finding.id, fixAction);
        toast(
          result.success ? result.message || 'Fixed successfully' : result.error || 'Fix failed',
          result.success ? 'success' : 'error',
        );
        refreshAll();
      } catch {
        toast('Error applying fix', 'error');
        // Restored ONLY on the error path. On success the button stays
        // disabled until the reload lands — which is what stops a second
        // click firing a second fix while the request is in flight.
        setBusyFix((current) => {
          const next = new Set(current);
          next.delete(finding.id);
          return next;
        });
      }
    },
    [dismissOne, prompts, refreshAll],
  );

  /** `selectDuplicateToKeep`. */
  const keepDuplicate = useCallback(
    async (findingId: number, trackId: string) => {
      const confirmed = await window.showConfirmDialog?.({
        title: 'Keep This Version',
        message: 'Keep this version and remove the other duplicate(s)?',
        confirmText: 'Keep',
        destructive: true,
      });
      if (!confirmed) return;
      try {
        const result = await fixFinding(findingId, trackId);
        toast(
          result.success
            ? result.message || 'Duplicate resolved'
            : result.error || 'Failed to resolve duplicate',
          result.success ? 'success' : 'error',
        );
        refreshAll();
      } catch {
        toast('Error resolving duplicate', 'error');
      }
    },
    [refreshAll],
  );

  /** `applyCoverArtTarget` — per-image apply, no confirm. */
  const applyCoverArt = useCallback(
    async (findingId: number, target: 'album' | 'artist') => {
      try {
        const result = await fixFinding(findingId, target);
        toast(
          result.success
            ? result.message || `Applied ${target} art`
            : result.error || `Failed to apply ${target} art`,
          result.success ? 'success' : 'error',
        );
        refreshAll();
      } catch {
        toast('Error applying art', 'error');
      }
    },
    [refreshAll],
  );

  // ── Selection-scoped bulk actions ──────────────────────────────────────────

  const bulkDismiss = useCallback(async () => {
    if (selected.size === 0) return;
    const count = selected.size;
    try {
      await bulkFindingAction([...selected], 'dismiss');
      toast(`${count} findings dismissed`, 'success');
      refreshAll();
    } catch {
      toast('Error updating findings', 'error');
    }
  }, [refreshAll, selected]);

  /** `bulkFixFindings` — ask once per finding KIND, then fix one id at a time. */
  const bulkFix = useCallback(async () => {
    if (selected.size === 0) return;
    const ids = [...selected];
    const byId = new Map((items || []).map((finding) => [finding.id, finding]));
    const typeOf = (id: number) => byId.get(id)?.finding_type;
    const withType = (findingType: string) => ids.filter((id) => typeOf(id) === findingType);

    const orphanIds = withType(TYPE_ORPHAN);
    let orphanAction: string | null = null;
    if (orphanIds.length > 0) {
      orphanAction = await prompts.promptOrphan();
      if (!orphanAction) return;
      // The scary dialog is for mass DELETION only — staging is reversible.
      if (orphanAction === 'delete' && orphanIds.length > MASS_ORPHAN_THRESHOLD) {
        const hasMassFlag = ids.some((id) => Boolean((byId.get(id)?.details || {}).mass_orphan));
        if (hasMassFlag && !(await prompts.promptWitnessMe(orphanIds.length))) return;
      }
    }

    const deadIds = withType(TYPE_DEAD);
    let deadAction: string | null = null;
    if (deadIds.length > 0) {
      deadAction = await prompts.promptDeadFile();
      if (!deadAction) return;
    }

    const acoustidIds = withType(TYPE_ACOUSTID);
    let acoustidAction: string | null = null;
    if (acoustidIds.length > 0) {
      acoustidAction = await prompts.promptAcoustid();
      if (!acoustidAction) return;
    }

    const backfillIds = withType(TYPE_BACKFILL);
    let backfillAction: string | null = null;
    if (backfillIds.length > 0) {
      backfillAction = await prompts.promptBackfill(backfillIds.length);
      if (!backfillAction) return;
    }

    const qualityIds = withType(TYPE_QUALITY);
    let qualityAction: string | null = null;
    if (qualityIds.length > 0) {
      qualityAction = await prompts.promptQuality();
      if (!qualityAction) return;
    }

    let fixed = 0;
    let failed = 0;
    let lastError = '';
    toast(`Fixing ${ids.length} findings...`, 'info');

    for (const id of ids) {
      const findingType = typeOf(id);
      try {
        // Backfill "Just Clear" and quality "Ignore" both dismiss rather than
        // fix, so they bypass the fix endpoint entirely.
        if (
          (findingType === TYPE_BACKFILL && backfillAction === 'dismiss') ||
          (findingType === TYPE_QUALITY && qualityAction === 'ignore')
        ) {
          try {
            if (await dismissFindingChecked(id)) fixed++;
            else {
              failed++;
              lastError = 'dismiss failed';
            }
          } catch {
            failed++;
          }
          continue;
        }

        let fixAction: string | null = null;
        if (findingType === TYPE_ORPHAN && orphanAction) fixAction = orphanAction;
        else if (findingType === TYPE_DEAD && deadAction) fixAction = deadAction;
        else if (findingType === TYPE_ACOUSTID && acoustidAction) fixAction = acoustidAction;
        else if (findingType === TYPE_QUALITY && qualityAction) fixAction = qualityAction;
        // Backfill "Add to Wishlist" falls through with no action — the fix
        // handler already adds to the wishlist by default.

        const result = await fixFinding(id, fixAction);
        if (result.success) fixed++;
        else {
          failed++;
          lastError = result.error || 'unknown error';
        }
      } catch {
        failed++;
      }
    }

    const { message, type } = bulkFixLoopMessage(fixed, failed, lastError);
    toast(message, type);
    refreshAll();
  }, [items, prompts, refreshAll, selected]);

  // ── Group-scoped actions ───────────────────────────────────────────────────

  /**
   * Fix a whole group. Every run is scoped to ONE finding type, which is the
   * only way `fix_action` can mean anything — 'delete' removes the file for
   * an orphan and names the track to KEEP for a duplicate.
   */
  const fixGroup = useCallback(
    async (group: FindingGroup, info: FindingTypeInfo | undefined) => {
      const label = info?.label || group.finding_type.replace(/_/g, ' ');
      const count = group.pending;
      let fixAction: string | null = null;

      if (group.finding_type === TYPE_BACKFILL) {
        const choice = await prompts.promptBackfill(count);
        if (!choice) return;
        if (choice === 'dismiss') {
          const confirmed = await window.showConfirmDialog?.({
            title: 'Clear All Discography Findings',
            message: `Clear all ${count.toLocaleString()} discography findings without adding any to the wishlist? Tracks can be re-detected on the next scan.`,
            confirmText: 'Clear All',
            destructive: false,
          });
          if (!confirmed) return;
          try {
            // Clear DELETES the rows, so the next scan can raise them again —
            // which is what "just clear" has always meant here. Dismiss would
            // suppress them permanently, a different promise entirely. Only
            // safe when the type comes from a single job, which is the real
            // case; otherwise fall back to the honest permanent dismiss.
            const jobIds = group.job_ids || [];
            const result =
              jobIds.length === 1
                ? await clearFindings({
                    jobId: jobIds[0],
                    status: 'pending',
                    // Scope to THIS type. Without it a job that emits several
                    // finding types would lose all of its pending rows when
                    // you cleared one group (the #1142 family).
                    findingType: group.finding_type,
                  })
                : {
                    success: true,
                    deleted: (await dismissFindingType(group.finding_type)).updated,
                  };
            toast(
              result.success
                ? `Cleared ${(result.deleted || 0).toLocaleString()} findings`
                : 'Clear failed',
              result.success ? 'success' : 'error',
            );
          } catch {
            toast('Error clearing findings', 'error');
          }
          refreshAll();
          return;
        }
        // 'add_to_wishlist' falls through with no fix_action.
      } else if (group.finding_type === TYPE_RETAG) {
        // Two requests wear one button: write the library's values, and write
        // them even over the fields this user edited by hand. The count comes
        // with the group so the choice is informed rather than a coin toss.
        fixAction = await prompts.promptRetag(count, group.manual_conflicts || 0);
        if (!fixAction) return;
        // 'safe' IS the default the handler takes with no action at all;
        // sending it would only add a string nothing reads.
        if (fixAction === 'safe') fixAction = null;
      } else if (group.finding_type === TYPE_DEAD) {
        fixAction = await prompts.promptDeadFile();
        if (!fixAction) return;
      } else if (group.finding_type === TYPE_ACOUSTID) {
        fixAction = await prompts.promptAcoustid();
        if (!fixAction) return;
      } else if (group.finding_type === TYPE_QUALITY) {
        fixAction = await prompts.promptQuality();
        if (!fixAction) return;
        if (fixAction === 'ignore') {
          const updated = (await dismissFindingType(group.finding_type)).updated || 0;
          toast(`Dismissed ${updated.toLocaleString()} quality findings`, 'success');
          refreshAll();
          return;
        }
      } else if (group.finding_type === TYPE_ORPHAN) {
        fixAction = await prompts.promptOrphan();
        if (!fixAction) return;
        if (fixAction === 'delete' && count > GROUP_MASS_DELETE_THRESHOLD) {
          if (!(await prompts.promptWitnessMe(count))) return;
        } else if (fixAction === 'delete') {
          const confirmed = await window.showConfirmDialog?.({
            title: 'Delete Orphan Files',
            message: `Permanently delete ${count.toLocaleString()} orphan files from disk? This cannot be undone.`,
            confirmText: 'Delete',
            destructive: true,
          });
          if (!confirmed) return;
        } else {
          const confirmed = await window.showConfirmDialog?.({
            title: 'Move to Staging',
            message: `Move ${count.toLocaleString()} orphan files to the import folder? Files are NOT deleted — you can review and import them.`,
            confirmText: 'Move All to Staging',
            destructive: false,
          });
          if (!confirmed) return;
        }
      } else {
        // Everything else takes its default action. Destructive types still
        // spell out what happens to files; safe ones just confirm the scale.
        const confirmed = await window.showConfirmDialog?.({
          title: `${info?.verb || 'Fix'} ${label}`,
          message: info?.destructive
            ? `Apply "${info.verb || 'Fix'}" to all ${count.toLocaleString()} ${label.toLowerCase()} findings? This moves or deletes files on disk and cannot be undone.`
            : `Apply "${info?.verb || 'Fix'}" to all ${count.toLocaleString()} ${label.toLowerCase()} findings? This only writes metadata — no files are deleted or moved.`,
          confirmText: info?.verb || 'Fix',
          destructive: Boolean(info?.destructive),
        });
        if (!confirmed) return;
      }

      try {
        const result = await startBulkFix({ findingType: group.finding_type, fixAction });
        if (result.started) {
          toast(`Fixing ${result.total} ${label.toLowerCase()} in the background…`, 'info');
          watchBulkFixRun();
        } else if (result.already_running) {
          toast('A bulk fix is already running — showing its progress', 'info');
          watchBulkFixRun();
        } else {
          toast(result.error || 'Bulk fix failed to start', 'error');
        }
      } catch {
        toast('Error starting bulk fix', 'error');
      }
    },
    [prompts, refreshAll, watchBulkFixRun],
  );

  const dismissGroup = useCallback(
    async (group: FindingGroup, info: FindingTypeInfo | undefined) => {
      const label = info?.label || group.finding_type.replace(/_/g, ' ');
      const confirmed = await window.showConfirmDialog?.({
        title: `Dismiss all ${label}`,
        message: `Dismiss all ${group.pending.toLocaleString()} open ${label.toLowerCase()} findings? Dismissed findings are never raised again — you can bring one back from the Dismissed view.`,
        confirmText: 'Dismiss all',
        destructive: false,
      });
      if (!confirmed) return;
      try {
        const result = await dismissFindingType(group.finding_type);
        toast(
          result.success
            ? `Dismissed ${(result.updated || 0).toLocaleString()} findings`
            : result.error || 'Failed to dismiss',
          result.success ? 'success' : 'error',
        );
        refreshAll();
      } catch {
        toast('Error dismissing findings', 'error');
      }
    },
    [refreshAll],
  );

  const safeCount = useMemo(() => safeFixablePending(groups, typeInfo), [groups, typeInfo]);

  /** Every safe type in one run. The backend drops destructive types itself
   *  (`safe_only`), so this cannot become "fix everything" through drift. */
  const fixAllSafe = useCallback(async () => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Fix all safe findings',
      message: `Apply the default fix to all ${safeCount.toLocaleString()} findings that only write metadata? Nothing is deleted or moved — anything that touches a file on disk is skipped.`,
      confirmText: 'Fix all safe',
      destructive: false,
    });
    if (!confirmed) return;
    try {
      const result = await startBulkFix({ safeOnly: true });
      if (result.started) {
        toast(`Fixing ${result.total} findings in the background…`, 'info');
        watchBulkFixRun();
      } else if (result.already_running) {
        toast('A bulk fix is already running — showing its progress', 'info');
        watchBulkFixRun();
      } else {
        toast(result.error || 'Bulk fix failed to start', 'error');
      }
    } catch {
      toast('Error starting bulk fix', 'error');
    }
  }, [safeCount, watchBulkFixRun]);

  /** `clearRepairFindings` — deletes rows outright, filter-scoped. */
  const clearAll = useCallback(async () => {
    const needle = query.trim();
    const scopeLabel = jobFilter ? jobLabel(jobFilter) : 'all jobs';
    const statusLabel = statusFilter ? ` (${statusFilter})` : '';
    // Spell out EVERY filter being applied. The button deletes rows for good,
    // and its old message named only the job and status — so a user who had
    // narrowed by severity or search read a prompt describing a far wider
    // delete than they expected, and got one (#1142).
    const extra = [
      severityFilter ? `severity ${severityFilter}` : '',
      needle ? `matching "${needle}"` : '',
    ].filter(Boolean);
    const extraLabel = extra.length ? `, ${extra.join(', ')}` : '';
    const confirmed = await window.showConfirmDialog?.({
      title: 'Clear Findings',
      message: `Delete all findings for ${scopeLabel}${statusLabel}${extraLabel}? This cannot be undone.`,
      confirmText: 'Clear',
      destructive: true,
    });
    if (!confirmed) return;
    try {
      const result = await clearFindings({
        jobId: jobFilter,
        status: statusFilter,
        severity: severityFilter,
        q: needle,
      });
      toast(
        result.success
          ? `Cleared ${result.deleted} findings`
          : result.error || 'Failed to clear findings',
        result.success ? 'success' : 'error',
      );
      refreshAll();
    } catch {
      toast('Error clearing findings', 'error');
    }
  }, [jobFilter, jobLabel, query, refreshAll, severityFilter, statusFilter]);

  // ── Render ─────────────────────────────────────────────────────────────────

  const shown = useMemo(
    () =>
      visibleGroups(groups, {
        jobId: jobFilter,
        severity: severityFilter,
        status: statusFilter,
      }),
    [groups, jobFilter, severityFilter, statusFilter],
  );

  /**
   * Fixing a group empties it, and the pending filter then hides it — which
   * would leave `openType` pointing at a row that is no longer on screen and
   * a fetched list with nowhere to render. Fall back to the inbox.
   */
  useEffect(() => {
    if (openType && !shown.some((group) => group.finding_type === openType)) setOpenType('');
  }, [openType, shown]);

  const bar = findingsBulkBarState(selected.size, items?.length || 0, total);
  const pagination = findingsPagination(total, serverPage, pageSize);
  const runningJobs = jobs.filter((job) => job.is_running).length;
  const statusCount = (value: string) => {
    if (!counts) return null;
    if (value === 'pending') return counts.pending || 0;
    if (value === 'resolved') return counts.resolved || 0;
    if (value === 'dismissed') return counts.dismissed || 0;
    if (value === 'auto_fixed') return counts.auto_fixed || 0;
    // The server's own total, not a sum of the four we happen to name — it
    // counts every status row, including any the UI has not learned about.
    return counts.total || 0;
  };
  /** Auto-fixed is its own status: findings the worker dealt with by itself.
   *  The segment only appears once there are any, so a library where nothing
   *  auto-fixes never grows a control that always reads zero. */
  const statusSegments =
    (counts?.auto_fixed || 0) > 0
      ? [
          ...STATUS_SEGMENTS.slice(0, 3),
          { value: 'auto_fixed', label: 'Auto-fixed' },
          STATUS_SEGMENTS[3],
        ]
      : STATUS_SEGMENTS;

  /**
   * A run in flight is surface-level, not list-level. It was inside the list
   * when the list was always on screen; now that the inbox is the default
   * view, a bar that only appears inside an open group would leave a fix the
   * user just started with no visible progress at all.
   */
  const bulkRunBar = bulkRun?.running ? (
    <div className="repair-findings-bulk running" id="repair-findings-bulk">
      <span className="repair-bulk-count" id="repair-bulk-count">
        Fixing {bulkRun.done} / {bulkRun.total}&hellip;
      </span>
      <button
        className="btn btn--sm btn--secondary"
        type="button"
        onClick={() => {
          stopBulkFix();
          toast('Stopping after the current fix...', 'info');
        }}
      >
        Stop
      </button>
    </div>
  ) : null;

  /* Opening a different type drops any grouping: "by album" made sense for an
     upgrade backlog and rarely does for the next type along. */
  useEffect(() => {
    setGroupView('list');
  }, [openType]);

  /* The list/album/artist switch. Only offered inside an opened type - grouping
     across every type at once would mix "192kbps" and "missing lyrics" into one
     album card and mean nothing. */
  const viewSwitch = openType ? (
    <div className="repair-view-switch" role="group" aria-label="Group findings by">
      {([
        ['list', 'List'],
        ['album', 'Albums'],
        ['artist', 'Artists'],
      ] as const).map(([value, label]) => (
        <button
          type="button"
          key={value}
          aria-pressed={groupView === value}
          onClick={() => setGroupView(value)}
        >
          {label}
        </button>
      ))}
    </div>
  ) : null;

  /* Drilling into a card reuses the search box, which already matches
     details_json - so the album name filters straight to that album's rows and
     every existing control (select all, bulk fix, sort) keeps working on it. */
  const groupedView =
    openType && groupView !== 'list' ? (
      <FindingsAlbumGrid
        groupBy={groupView}
        status={statusFilter}
        findingType={openType}
        onOpen={(group) => {
          const needle = groupView === 'artist' ? group.artist : group.album;
          if (needle) setQuery(needle);
          setGroupView('list');
          setPage(0);
        }}
      />
    ) : null;

  const findingList = (
    <>
      {viewSwitch ? <div className="repair-findings-toolbar">{viewSwitch}</div> : null}
      {groupedView}
      {groupedView ? null : (
    <>
      {bar.showBar ? (
        <div className="repair-findings-bulk" id="repair-findings-selection">
          <span className="repair-bulk-count">{bar.countLabel}</span>
          <button className="btn btn--sm btn--primary" type="button" onClick={() => void bulkFix()}>
            Fix Selected
          </button>
          <button
            className="btn btn--sm btn--secondary"
            type="button"
            onClick={() => void bulkDismiss()}
          >
            Dismiss Selected
          </button>
        </div>
      ) : null}

      <div className="repair-list-controls">
        <label className="repair-select-all" title="Select all on this page">
          <input
            type="checkbox"
            id="repair-select-all-cb"
            checked={bar.selectAllChecked}
            ref={(node) => {
              if (node) node.indeterminate = bar.selectAllIndeterminate;
            }}
            onChange={(event) => toggleSelectAll(event.target.checked)}
          />
          <span>Select all on this page</span>
        </label>
        <select
          id="repair-findings-sort"
          title="Sort"
          value={sort}
          onChange={(event) => {
            setSort(event.target.value);
            setPage(0);
          }}
        >
          {SORT_OPTIONS.map((option) => (
            <option value={option.value} key={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <select
          id="repair-page-size-select"
          title="Findings per page"
          value={String(pageSize)}
          onChange={(event) => changePageSize(event.target.value)}
        >
          {REPAIR_PAGE_SIZE_OPTIONS.map((size) => (
            <option value={String(size)} key={size}>
              {size} / page
            </option>
          ))}
        </select>
      </div>

      <div className="repair-findings-list" id="repair-findings-list">
        {loadError !== null ? (
          <div className="repair-empty">
            Error loading findings
            <div style={{ marginTop: 6, fontSize: 12, opacity: 0.75 }}>{loadError}</div>
          </div>
        ) : items === null ? (
          <div className="repair-loading">Loading findings...</div>
        ) : items.length === 0 ? (
          <div className="repair-empty">Nothing here matches your filters.</div>
        ) : (
          items.map((finding) => (
            <FindingCard
              finding={finding}
              key={finding.id}
              selected={selected.has(finding.id)}
              expanded={expanded.has(finding.id)}
              fixing={busyFix.has(finding.id)}
              onToggleSelect={toggleSelect}
              onToggleDetail={toggleDetail}
              jobLabel={jobLabel}
              onFix={fixOne}
              onDismiss={dismissOne}
              onReopen={reopenOne}
              onKeepDuplicate={(findingId, trackId) => void keepDuplicate(findingId, trackId)}
              onApplyCoverArt={(findingId, target) => void applyCoverArt(findingId, target)}
            />
          ))
        )}
      </div>

      <div className="repair-findings-pagination" id="repair-findings-pagination">
        {items && items.length > 0 && pagination.totalPages > 1 ? (
          <>
            {pagination.showPrev ? (
              <button
                className="repair-page-btn"
                type="button"
                onClick={() => setPage(serverPage - 1)}
              >
                &larr;
              </button>
            ) : null}
            {pagination.showFirst ? (
              <button className="repair-page-btn" type="button" onClick={() => setPage(0)}>
                1
              </button>
            ) : null}
            {pagination.showFirstEllipsis ? <span className="repair-page-info">...</span> : null}
            {pagination.pages.map((index) => (
              <button
                className={`repair-page-btn ${index === serverPage ? 'active' : ''}`}
                type="button"
                key={index}
                onClick={() => setPage(index)}
              >
                {index + 1}
              </button>
            ))}
            {pagination.showLastEllipsis ? <span className="repair-page-info">...</span> : null}
            {pagination.showLast ? (
              <button
                className="repair-page-btn"
                type="button"
                onClick={() => setPage(pagination.totalPages - 1)}
              >
                {pagination.totalPages}
              </button>
            ) : null}
            {pagination.showNext ? (
              <button
                className="repair-page-btn"
                type="button"
                onClick={() => setPage(serverPage + 1)}
              >
                &rarr;
              </button>
            ) : null}
            <span className="repair-page-info">{total.toLocaleString()} total</span>
          </>
        ) : null}
      </div>
    </>
      )}
    </>
  );

  return (
    <>
      <HealthHero
        groups={groups}
        typeInfo={typeInfo}
        trackCount={trackCount}
        runs={runs}
        runningJobs={runningJobs}
        safeCount={safeCount}
        onFixAllSafe={() => void fixAllSafe()}
        fixAllBusy={Boolean(bulkRun?.running)}
        onPickType={pickType}
      />

      {bulkRunBar}

      {cacheHealth && (cacheHealth.total_entities || cacheHealth.total_searches) ? (
        <div className="repair-cache-health">
          {/* The modal stays vanilla: `openCacheHealthModal` is reached from
              the Metadata Cache card too, and it opens onward into the
              ~320-line failed-MB-lookups manager. */}
          <div className="repair-cache-health-bar" onClick={() => window.openCacheHealthModal?.()}>
            <span className={`repair-cache-health-dot ${cacheHealthScore(cacheHealth)}`} />
            <span className="repair-cache-health-title">Metadata Cache</span>
            <span className="repair-cache-health-summary">
              {(cacheHealth.total_entities || 0).toLocaleString()} entities ·{' '}
              {cacheHealthLabel(cacheHealthScore(cacheHealth))}
            </span>
            <span className="repair-cache-health-action">View Details ›</span>
          </div>
        </div>
      ) : null}

      {/* The section anchor lands here rather than on the inbox: the toolbar
          is always present, and the inbox is not (a search replaces it). */}
      <div className="repair-findings-toolbar" id="repair-section-findings">
        <div className="repair-status-segments" role="group" aria-label="Finding status">
          {statusSegments.map((segment) => {
            const count = statusCount(segment.value);
            return (
              <button
                type="button"
                key={segment.value || 'all'}
                className={`repair-status-seg${statusFilter === segment.value ? ' active' : ''}`}
                onClick={() => {
                  setStatusFilter(segment.value);
                  setPage(0);
                }}
              >
                {segment.label}
                {count !== null ? (
                  <span className="repair-status-seg-count">{count.toLocaleString()}</span>
                ) : null}
              </button>
            );
          })}
        </div>

        <input
          type="search"
          className="repair-findings-search"
          id="repair-findings-search"
          placeholder="Search titles and paths…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setPage(0);
          }}
        />

        <div className="repair-findings-filters">
          <select
            id="repair-findings-job-filter"
            value={jobFilter}
            onChange={(event) => {
              setJobFilter(event.target.value);
              setPage(0);
            }}
          >
            <option value="">All Jobs</option>
            {jobs.map((job) => (
              <option value={job.job_id} key={job.job_id}>
                {job.display_name}
              </option>
            ))}
          </select>
          <select
            id="repair-findings-severity-filter"
            value={severityFilter}
            onChange={(event) => {
              setSeverityFilter(event.target.value);
              setPage(0);
            }}
          >
            <option value="">All Severity</option>
            {/* `error` is what the corruption detector emits — the most urgent
                findings in the system, and they had no filter at all. Labelled
                Critical because that is the word the rest of the UI uses. */}
            <option value="error">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
        </div>

        <button
          className="repair-clear-btn"
          type="button"
          title="Clear findings matching current filters"
          onClick={() => void clearAll()}
        >
          Clear Findings
        </button>
      </div>

      {searching ? (
        <>
          <div className="repair-search-note">
            Searching every finding for <b>{query.trim()}</b> — clear the search to go back to
            groups.
          </div>
          {findingList}
        </>
      ) : (
        <FindingsInbox
          groups={shown}
          typeInfo={typeInfo}
          status={statusFilter}
          openType={openType}
          onToggleOpen={toggleOpenGroup}
          onFixGroup={(group, info) => void fixGroup(group, info)}
          onDismissGroup={(group, info) => void dismissGroup(group, info)}
          busy={Boolean(bulkRun?.running)}
        >
          {findingList}
        </FindingsInbox>
      )}

      {prompts.promptNode}
    </>
  );
}

// ── A finding card ───────────────────────────────────────────────────────────

function FindingCard({
  finding,
  selected,
  expanded,
  fixing,
  onToggleSelect,
  onToggleDetail,
  jobLabel,
  onFix,
  onDismiss,
  onReopen,
  onKeepDuplicate,
  onApplyCoverArt,
}: {
  finding: RepairFinding;
  selected: boolean;
  expanded: boolean;
  fixing: boolean;
  onToggleSelect: (id: number, checked: boolean) => void;
  onToggleDetail: (id: number) => void;
  jobLabel: (jobId: string) => string;
  onFix: (finding: RepairFinding) => Promise<void>;
  onDismiss: (id: number) => Promise<void>;
  onReopen: (id: number) => Promise<void>;
  onKeepDuplicate: (findingId: number, trackId: string) => void;
  onApplyCoverArt: (findingId: number, target: 'album' | 'artist') => void;
}) {
  const details = finding.details || {};
  const filePath = findingFilePath(finding);
  const fixLabel = findingRowFixLabel(finding);
  const statusBadge = findingStatusBadge(finding.status, finding.user_action);

  return (
    <div
      className={`repair-finding-card ${finding.severity}`}
      data-id={finding.id}
      data-job-id={finding.job_id}
      data-mass-orphan={String(Boolean(details.mass_orphan))}
    >
      <div className="repair-finding-main" onClick={() => onToggleDetail(finding.id)}>
        <div className="repair-finding-select" onClick={(event) => event.stopPropagation()}>
          <input
            type="checkbox"
            checked={selected}
            onChange={(event) => onToggleSelect(finding.id, event.target.checked)}
          />
        </div>
        <div className="repair-finding-content">
          <div className="repair-finding-title">
            <span className="repair-finding-icon">{findingSeverityIcon(finding.severity)}</span>
            {finding.title}
            <span className="repair-finding-type-badge">
              {findingTypeLabel(finding.finding_type)}
            </span>
            {statusBadge ? (
              <span className={`repair-finding-status-badge ${finding.status}`}>{statusBadge}</span>
            ) : null}
          </div>
          <div className="repair-finding-desc">{finding.description || ''}</div>
          {filePath ? <div className="repair-finding-path">{filePath}</div> : null}
          <div className="repair-finding-meta">
            {/* The display name the filter dropdown uses — a row showing the
                raw snake_case id meant the same job went by two different
                names on one screen. */}
            <span>{jobLabel(finding.job_id)}</span>
            <span>&middot;</span>
            <span>{finding.entity_type || 'file'}</span>
            {finding.entity_id ? (
              <>
                <span>&middot;</span>
                <span>ID: {finding.entity_id}</span>
              </>
            ) : null}
            <span>&middot;</span>
            <span>{formatCacheAge(finding.created_at)}</span>
          </div>
        </div>
        {/* This wrapper swallows clicks so the row toggle doesn't undo them. */}
        <div className="repair-finding-actions" onClick={(event) => event.stopPropagation()}>
          {finding.status === 'pending' ? (
            <>
              {fixLabel ? (
                <button
                  className="repair-finding-btn fix"
                  type="button"
                  title={fixLabel}
                  disabled={fixing}
                  onClick={() => void onFix(finding)}
                >
                  {fixing ? '...' : fixLabel}
                </button>
              ) : null}
              <button
                className="repair-finding-btn dismiss"
                type="button"
                title="Dismiss — never show this finding again"
                onClick={() => void onDismiss(finding.id)}
              >
                &times;
              </button>
            </>
          ) : (
            // Dismiss is permanent by design. Offering it freely is only fair
            // if it can be taken back, and this is the only place that undo
            // could live — the row is the thing that was dismissed.
            <button
              className="repair-finding-btn reopen"
              type="button"
              title="Reopen — put this finding back in the open list"
              onClick={() => void onReopen(finding.id)}
            >
              Reopen
            </button>
          )}
          <button
            className={`repair-finding-expand-btn${expanded ? ' open' : ''}`}
            type="button"
            data-finding={finding.id}
            title={expanded ? 'Hide details' : 'Details'}
            aria-expanded={expanded}
            onClick={(event) => {
              // This chevron was decorative — the row body was the only way to
              // expand, so the one control that LOOKS like the expander did
              // nothing. stopPropagation, or the row toggle undoes this click.
              event.stopPropagation();
              onToggleDetail(finding.id);
            }}
          >
            &#9660;
          </button>
        </div>
      </div>
      <div
        className={`repair-finding-detail${expanded ? ' open' : ''}`}
        id={`repair-detail-${finding.id}`}
      >
        <div className="repair-finding-detail-inner">
          {/* Mounted ONLY while open. Every collapsed row used to build its
              full 20-branch detail tree and fetch its album/artist art, hidden
              behind max-height:0 — so a 100-row page rendered 100 invisible
              panels and hammered the thumbnail endpoints for them. */}
          {expanded ? (
            <FindingDetail
              finding={finding}
              onKeepDuplicate={onKeepDuplicate}
              onApplyCoverArt={onApplyCoverArt}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
