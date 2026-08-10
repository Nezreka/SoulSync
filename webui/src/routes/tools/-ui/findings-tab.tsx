/**
 * The Findings tab — dashboard, filters, the finding list, pagination, the bulk
 * bar and every fix path.
 *
 * Ported from `loadRepairFindingsDashboard`, `loadRepairFindings`,
 * `renderRepairFindingsPagination`, `_updateFindingsBulkBar`,
 * `fixAllMatchingFindings`, `bulkFixFindings`, `bulkRepairAction`,
 * `clearRepairFindings` and the per-finding actions in enrichment.js.
 *
 * Three things the vanilla does that are easy to "improve" by accident:
 *
 * 1. **The expand chevron is decorative.** `.repair-finding-actions` carries an
 *    `onclick="event.stopPropagation()"`, and `.repair-finding-expand-btn` has no
 *    handler of its own — so clicking the chevron does nothing and only a click
 *    on the row body toggles the detail. Preserved deliberately; changing it is a
 *    UX decision, not a port.
 *
 * 2. **The auto-switch notice is never removed.** When the default "pending"
 *    filter finds nothing but other statuses have rows, the vanilla flips to
 *    "All Status" once and inserts an explanatory notice as a SIBLING of the list
 *    — so later reloads, which only rewrite the list's innerHTML, leave it in
 *    place for the rest of the page's life. Same here.
 *
 * 3. **Fix All runs in the background.** At library scale a synchronous fix-all
 *    outlives every browser and proxy timeout, and the user was told it failed
 *    while the server quietly kept fixing. The start endpoint returns
 *    immediately, a 2s poll drives the bulk bar, and a reload mid-run picks the
 *    progress back up (`_checkBulkFixResume`).
 *
 * ONE deliberate deviation. In the vanilla the job chips' `active` highlight is
 * baked in when the dashboard renders, and the filter `<select>`'s onchange calls
 * only `loadRepairFindings()` — so picking a job from the dropdown leaves the
 * chip highlight pointing at the wrong job until something else reloads the
 * dashboard. Here the highlight is derived from `jobFilter` state, so it is
 * always right. Reproducing the stale highlight would mean deliberately
 * snapshotting the filter value, i.e. building the bug on purpose.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { BulkFixStatus, CacheHealthStats, RepairFinding, RepairJob } from '../-tools.types';

import {
  bulkFindingAction,
  clearFindings,
  dismissFinding,
  dismissFindingChecked,
  fetchBulkFixStatus,
  fetchCacheHealth,
  fetchFindingCounts,
  fetchRepairFindings,
  fixFinding,
  startBulkFix,
  stopBulkFix,
} from '../-tools.api';
import {
  bulkFixLoopMessage,
  bulkFixRunMessage,
  cacheHealthLabel,
  cacheHealthScore,
  findingFilePath,
  findingFixLabel,
  findingSeverityIcon,
  findingStatusBadge,
  findingTypeLabel,
  findingsBulkBarState,
  findingsPagination,
  formatCacheAge,
  isMassOrphanFix,
  MASS_ORPHAN_THRESHOLD,
  normalizeFindingsPageSize,
  REPAIR_DEFAULT_PAGE_SIZE,
  REPAIR_PAGE_SIZE_OPTIONS,
} from '../-tools.core';
import { FindingDetail } from './finding-detail';
import { useFindingPrompts } from './finding-prompts';

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

/** Which prompt a job id implies, for the bulk path's "ask once per kind" pass. */
const JOB_ORPHAN = 'orphan_file_detector';
const JOB_DEAD = 'dead_file_cleaner';
const JOB_ACOUSTID = 'acoustid_scanner';
const JOB_BACKFILL = 'discography_backfill';
const JOB_QUALITY = 'quality_upgrade_scanner';

export interface FindingsTabProps {
  /** The job list, for the filter dropdown. The vanilla fills it from
   *  `loadRepairJobs`, once, hence the hero owning it. */
  jobs: RepairJob[];
  /** Only load while the tab is showing — `switchRepairTab` was the trigger. */
  active: boolean;
  /** `updateRepairStatus()` — refresh the pending badge after any mutation. */
  onStatusChanged: () => void;
}

export function FindingsTab({ jobs, active, onStatusChanged }: FindingsTabProps) {
  const [jobFilter, setJobFilter] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('pending');
  const [pageSize, setPageSize] = useState(readStoredPageSize);
  const [page, setPage] = useState(0);

  const [items, setItems] = useState<RepairFinding[] | null>(null);
  const [total, setTotal] = useState(0);
  /**
   * The page the SERVER echoed back, which is what the vanilla paginates on:
   * `renderRepairFindingsPagination(data.total, data.page)`. It is normally the
   * page we asked for, but if the backend ever clamps an out-of-range request the
   * highlight and the prev/next arithmetic must follow the server, not the ask.
   */
  const [serverPage, setServerPage] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [counts, setCounts] = useState<Awaited<ReturnType<typeof fetchFindingCounts>> | null>(null);
  const [cacheHealth, setCacheHealth] = useState<CacheHealthStats | null>(null);

  const [selected, setSelected] = useState<ReadonlySet<number>>(() => new Set());
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(() => new Set());
  const [busyFix, setBusyFix] = useState<ReadonlySet<number>>(() => new Set());

  /** Set once we auto-switch, so the fallback can't loop. */
  const autoSwitched = useRef(false);
  const [autoSwitchNotice, setAutoSwitchNotice] = useState<number | null>(null);

  const [bulkRun, setBulkRun] = useState<BulkFixStatus | null>(null);
  const bulkTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const prompts = useFindingPrompts();

  // ── Loading ────────────────────────────────────────────────────────────────

  /** Set below, so `loadCounts` can call the watcher without the two useCallbacks
   *  depending on each other in a cycle. */
  const watchBulkFixRef = useRef<() => void>(() => {});

  const loadCounts = useCallback(async () => {
    // `_checkBulkFixResume` runs at the TOP of every dashboard load in the
    // vanilla, not just on open — so a run started from another tab is picked up
    // by the next refresh rather than only by a re-entry into this tab.
    void fetchBulkFixStatus().then((status) => {
      if (status?.running) watchBulkFixRef.current();
    });
    try {
      setCounts(await fetchFindingCounts());
    } catch {
      // `dashboard.innerHTML = ''` — the whole dashboard goes away on error, and
      // the cache-health call never happens: the vanilla only reaches
      // `_loadCacheHealthStats` on the success path.
      setCounts(null);
      return;
    }
    setCacheHealth(await fetchCacheHealth());
  }, []);

  const loadFindings = useCallback(async () => {
    try {
      const data = await fetchRepairFindings({
        jobId: jobFilter,
        severity: severityFilter,
        status: statusFilter,
        page,
        limit: pageSize,
      });
      setLoadError(null);
      setSelected(new Set());
      setBusyFix(new Set());
      setTotal(data.total);
      setServerPage(data.page);

      if (data.items.length === 0 && statusFilter === 'pending' && !autoSwitched.current) {
        // Nothing pending, but dedup-skip means a re-scan of already-dismissed
        // issues creates no new pending rows — so the badge count can point at
        // carry-over rows the default filter hides. Show them instead of an
        // empty pane.
        try {
          const other = await fetchFindingCounts();
          const otherTotal =
            (other.resolved || 0) + (other.dismissed || 0) + (other.auto_fixed || 0);
          if (otherTotal > 0) {
            autoSwitched.current = true;
            setAutoSwitchNotice(otherTotal);
            setStatusFilter('');
            setPage(0);
            return;
          }
        } catch {
          // fall through to the empty state
        }
      }
      setItems(data.items);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
    }
  }, [jobFilter, page, pageSize, severityFilter, statusFilter]);

  const refreshAll = useCallback(() => {
    setSelected(new Set());
    void loadCounts();
    void loadFindings();
    onStatusChanged();
  }, [loadCounts, loadFindings, onStatusChanged]);

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

  // `switchRepairTab('findings')` loaded both halves; loadCounts carries the
  // bulk-fix resume check with it.
  useEffect(() => {
    if (!active) return;
    void loadCounts();
  }, [active, loadCounts]);

  useEffect(() => {
    if (!active) return;
    void loadFindings();
  }, [active, loadFindings]);

  // ── Filters ────────────────────────────────────────────────────────────────

  /** `filterFindingsByJob` — clicking the active chip again clears the filter. */
  const filterByJob = useCallback((jobId: string) => {
    setJobFilter((current) => (current === jobId ? '' : jobId));
    setPage(0);
  }, []);

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
      } catch {
        // The vanilla logs and does nothing else — no toast, no refresh skip.
        return;
      }
      refreshAll();
    },
    [refreshAll],
  );

  /** `fixRepairFinding`. The per-type prompts run BEFORE the button is disabled. */
  const fixOne = useCallback(
    async (finding: RepairFinding) => {
      const type = finding.finding_type;
      let fixAction: string | null = null;

      if (type === 'orphan_file') {
        fixAction = await prompts.promptOrphan();
        if (!fixAction) return;
      }
      if (type === 'dead_file') {
        fixAction = await prompts.promptDeadFile();
        if (!fixAction) return;
      }
      if (type === 'acoustid_mismatch') {
        fixAction = await prompts.promptAcoustid();
        if (!fixAction) return;
      }
      if (type === 'quality_upgrade') {
        fixAction = await prompts.promptQuality();
        if (!fixAction) return;
        if (fixAction === 'ignore') {
          await dismissOne(finding.id);
          return;
        }
      }
      if (type === 'missing_discography_track') {
        const choice = await prompts.promptBackfill(1);
        if (!choice) return;
        if (choice === 'dismiss') {
          await dismissOne(finding.id);
          return;
        }
        // 'add_to_wishlist' falls through with no fix_action — the handler
        // already adds to the wishlist by default.
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
        // Restored ONLY on the error path, as the vanilla does. On success the
        // button stays disabled until the reload lands — which is what stops a
        // second click firing a second fix while the request is in flight. The
        // reload clears the whole set (see loadFindings); the vanilla got the
        // same effect for free by replacing the list's innerHTML.
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

  // ── Bulk actions ───────────────────────────────────────────────────────────

  /**
   * `bulkRepairAction`. The vanilla takes an action and its toast reads
   * "dismissed" or "resolved", but the only call site in the markup passes
   * 'dismiss' — there is no Resolve Selected button — so this is the dismiss
   * half only. The toast fires whether or not the request succeeded, as it does
   * in the vanilla.
   */
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
    const jobOf = (id: number) => byId.get(id)?.job_id;
    const withJob = (jobId: string) => ids.filter((id) => jobOf(id) === jobId);

    const orphanIds = withJob(JOB_ORPHAN);
    let orphanAction: string | null = null;
    if (orphanIds.length > 0) {
      orphanAction = await prompts.promptOrphan();
      if (!orphanAction) return;
      // The scary dialog is for mass DELETION only — staging is reversible.
      // NB this path compares the raw threshold rather than going through
      // isMassOrphanFix: the job scope is already known from the selection.
      if (orphanAction === 'delete' && orphanIds.length > MASS_ORPHAN_THRESHOLD) {
        const hasMassFlag = ids.some((id) => Boolean((byId.get(id)?.details || {}).mass_orphan));
        if (hasMassFlag && !(await prompts.promptWitnessMe(orphanIds.length))) return;
      }
    }

    const deadIds = withJob(JOB_DEAD);
    let deadAction: string | null = null;
    if (deadIds.length > 0) {
      deadAction = await prompts.promptDeadFile();
      if (!deadAction) return;
    }

    const acoustidIds = withJob(JOB_ACOUSTID);
    let acoustidAction: string | null = null;
    if (acoustidIds.length > 0) {
      acoustidAction = await prompts.promptAcoustid();
      if (!acoustidAction) return;
    }

    const backfillIds = withJob(JOB_BACKFILL);
    let backfillAction: string | null = null;
    if (backfillIds.length > 0) {
      backfillAction = await prompts.promptBackfill(backfillIds.length);
      if (!backfillAction) return;
    }

    const qualityIds = withJob(JOB_QUALITY);
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
      const jobId = jobOf(id);
      try {
        // Backfill "Just Clear" and quality "Ignore" both dismiss rather than
        // fix, so they bypass the fix endpoint entirely.
        if (
          (jobId === JOB_BACKFILL && backfillAction === 'dismiss') ||
          (jobId === JOB_QUALITY && qualityAction === 'ignore')
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
        if (jobId === JOB_ORPHAN && orphanAction) fixAction = orphanAction;
        else if (jobId === JOB_DEAD && deadAction) fixAction = deadAction;
        else if (jobId === JOB_ACOUSTID && acoustidAction) fixAction = acoustidAction;
        else if (jobId === JOB_QUALITY && qualityAction) fixAction = qualityAction;
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

  /** `fixAllMatchingFindings` — filter-wide, background, one prompt up front. */
  const fixAll = useCallback(async () => {
    let fixAction: string | null = null;

    if (jobFilter === JOB_BACKFILL) {
      // Three-way: wishlist / just clear / cancel. "Just clear" bypasses
      // bulk-fix entirely through the clear endpoint, hence the early return.
      const choice = await prompts.promptBackfill(total);
      if (!choice) return;
      if (choice === 'dismiss') {
        const confirmed = await window.showConfirmDialog?.({
          title: 'Clear All Discography Findings',
          message: `Clear all ${total} discography backfill findings without adding any to the wishlist? Tracks can be re-detected next scan.`,
          confirmText: 'Clear All',
          destructive: false,
        });
        if (!confirmed) return;
        toast(`Clearing ${total} findings...`, 'info');
        try {
          const result = await clearFindings(JOB_BACKFILL, 'pending');
          toast(
            result.success ? `Cleared ${result.deleted} findings` : result.error || 'Clear failed',
            result.success ? 'success' : 'error',
          );
        } catch {
          toast('Error clearing findings', 'error');
        }
        refreshAll();
        return;
      }
      // 'add_to_wishlist' falls through to bulk-fix. No destructive warning —
      // the backend handler only adds tracks to the wishlist.
    } else if (jobFilter === JOB_DEAD) {
      fixAction = await prompts.promptDeadFile();
      if (!fixAction) return;
    } else if (
      jobFilter === JOB_ORPHAN ||
      isMassOrphanFix(
        jobFilter,
        total,
        (items || []).some((finding) => Boolean((finding.details || {}).mass_orphan)),
      )
    ) {
      fixAction = await prompts.promptOrphan();
      if (!fixAction) return;
      if (fixAction === 'delete' && total > 50) {
        if (!(await prompts.promptWitnessMe(total))) return;
      } else if (fixAction === 'delete') {
        const confirmed = await window.showConfirmDialog?.({
          title: 'Delete Orphan Files',
          message: `Permanently delete ${total} orphan files from disk? This cannot be undone.`,
          confirmText: 'Delete',
          destructive: true,
        });
        if (!confirmed) return;
      } else if (fixAction === 'staging') {
        const confirmed = await window.showConfirmDialog?.({
          title: 'Move to Staging',
          message: `Move ${total} orphan files to the import folder? Files are NOT deleted — you can review and import them.`,
          confirmText: 'Move All to Staging',
          destructive: false,
        });
        if (!confirmed) return;
      }
    } else {
      const scopeLabel = jobFilter ? jobFilter.replace(/_/g, ' ') : 'all jobs';
      const confirmed = await window.showConfirmDialog?.({
        title: 'Fix All Findings',
        message: `Apply fixes to all ${total} pending fixable findings for ${scopeLabel}? This may delete files or remove database entries depending on finding type.`,
        confirmText: 'Fix All',
        destructive: true,
      });
      if (!confirmed) return;
    }

    try {
      const result = await startBulkFix({
        jobId: jobFilter,
        severity: severityFilter,
        fixAction,
      });
      if (result.started) {
        toast(`Fixing ${result.total} findings in the background...`, 'info');
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

    setSelected(new Set());
  }, [items, jobFilter, prompts, refreshAll, severityFilter, total, watchBulkFixRun]);

  /** `clearRepairFindings` — deletes rows outright, filter-scoped. */
  const clearAll = useCallback(async () => {
    const scopeLabel = jobFilter ? jobFilter.replace(/_/g, ' ') : 'all jobs';
    const statusLabel = statusFilter ? ` (${statusFilter})` : '';
    const confirmed = await window.showConfirmDialog?.({
      title: 'Clear Findings',
      message: `Delete all findings for ${scopeLabel}${statusLabel}? This cannot be undone.`,
      confirmText: 'Clear',
      destructive: true,
    });
    if (!confirmed) return;
    try {
      const result = await clearFindings(jobFilter, statusFilter);
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
  }, [jobFilter, refreshAll, statusFilter]);

  // ── Render ─────────────────────────────────────────────────────────────────

  const bar = findingsBulkBarState(selected.size, items?.length || 0, total);
  const pagination = findingsPagination(total, serverPage, pageSize);
  const barVisible = bar.showBar || Boolean(bulkRun?.running);

  return (
    <>
      <div className="repair-findings-dashboard" id="repair-findings-dashboard">
        <FindingsDashboard
          counts={counts}
          cacheHealth={cacheHealth}
          activeJob={jobFilter}
          onFilterByJob={filterByJob}
        />
      </div>

      <div className="repair-findings-toolbar">
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
            <option value="info">Info</option>
            <option value="warning">Warning</option>
          </select>
          <select
            id="repair-findings-status-filter"
            value={statusFilter}
            onChange={(event) => {
              setStatusFilter(event.target.value);
              setPage(0);
              // Touching this filter by hand disarms the auto-switch, exactly as
              // the vanilla's inline `_repairFindingsAutoSwitched=true` did.
              autoSwitched.current = true;
            }}
          >
            <option value="pending">Pending</option>
            <option value="">All Status</option>
            <option value="resolved">Resolved</option>
            <option value="dismissed">Dismissed</option>
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
          <span>Select All</span>
        </label>
        <div
          className="repair-findings-bulk"
          id="repair-findings-bulk"
          style={{ display: barVisible ? '' : 'none' }}
        >
          <span className="repair-bulk-count" id="repair-bulk-count">
            {bulkRun?.running ? (
              <>
                Fixing {bulkRun.done} / {bulkRun.total}&hellip;{' '}
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
              </>
            ) : (
              bar.countLabel
            )}
          </span>
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
          <button
            className="btn btn--sm btn--warning"
            id="repair-fix-all-btn"
            style={{ display: bar.showFixAll ? '' : 'none' }}
            type="button"
            onClick={() => void fixAll()}
          >
            {bar.fixAllLabel}
          </button>
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

      {autoSwitchNotice !== null ? (
        <div className="repair-auto-switch-notice" style={AUTO_SWITCH_NOTICE_STYLE}>
          No <b>pending</b> findings, but {autoSwitchNotice.toLocaleString()} carry-over
          (resolved/dismissed/auto-fixed). Showing <b>All Status</b> — change the filter above to
          switch back.
        </div>
      ) : null}

      <div className="repair-findings-list" id="repair-findings-list">
        {loadError !== null ? (
          <div className="repair-empty">
            Error loading findings
            <div style={{ marginTop: 6, fontSize: 12, opacity: 0.75 }}>{loadError}</div>
          </div>
        ) : items === null ? (
          <div className="repair-loading">Loading findings...</div>
        ) : items.length === 0 ? (
          <div className="repair-empty-state">
            <div className="repair-empty-icon">&#10003;</div>
            <div className="repair-empty-title">All Clear</div>
            <div className="repair-empty-text">
              No findings match your filters. Your library is looking good!
            </div>
          </div>
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
              onFix={fixOne}
              onDismiss={dismissOne}
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

      {prompts.promptNode}
    </>
  );
}

const AUTO_SWITCH_NOTICE_STYLE = {
  background: 'rgba(96,165,250,0.08)',
  border: '1px solid rgba(96,165,250,0.25)',
  borderRadius: '8px',
  padding: '10px 14px',
  marginBottom: '12px',
  fontSize: '13px',
  color: '#cbd5e1',
} as const;

// ── Dashboard ────────────────────────────────────────────────────────────────

/**
 * `loadRepairFindingsDashboard` + `_loadCacheHealthStats`. The auto-fixed pill is
 * conditional; the other three always show. Chips are sorted by total desc.
 */
function FindingsDashboard({
  counts,
  cacheHealth,
  activeJob,
  onFilterByJob,
}: {
  counts: Awaited<ReturnType<typeof fetchFindingCounts>> | null;
  cacheHealth: CacheHealthStats | null;
  activeJob: string;
  onFilterByJob: (jobId: string) => void;
}) {
  if (!counts) return null;

  const autoFixed = counts.auto_fixed || 0;
  const byJob = counts.by_job || {};
  const jobIds = Object.keys(byJob).sort((a, b) => byJob[b].total - byJob[a].total);
  const showHealth =
    cacheHealth && (cacheHealth.total_entities || cacheHealth.total_searches) ? cacheHealth : null;
  const score = showHealth ? cacheHealthScore(showHealth) : null;

  return (
    <>
      <div className="repair-dashboard-summary">
        <div className="repair-dashboard-stat pending">
          <span className="stat-count">{(counts.pending || 0).toLocaleString()}</span> pending
        </div>
        <div className="repair-dashboard-stat resolved">
          <span className="stat-count">{(counts.resolved || 0).toLocaleString()}</span> resolved
        </div>
        <div className="repair-dashboard-stat dismissed">
          <span className="stat-count">{(counts.dismissed || 0).toLocaleString()}</span> dismissed
        </div>
        {autoFixed > 0 ? (
          <div className="repair-dashboard-stat auto-fixed">
            <span className="stat-count">{autoFixed.toLocaleString()}</span> auto-fixed
          </div>
        ) : null}
      </div>

      {jobIds.length > 0 ? (
        <div className="repair-dashboard-jobs">
          {jobIds.map((jobId) => {
            const job = byJob[jobId];
            return (
              <div
                className={`repair-dashboard-chip ${activeJob === jobId ? 'active' : ''}`}
                key={jobId}
                onClick={() => onFilterByJob(jobId)}
              >
                <span className="repair-dashboard-chip-count">{job.total.toLocaleString()}</span>
                <span className="repair-dashboard-chip-name">
                  {job.display_name || jobId.replace(/_/g, ' ')}
                </span>
                {job.warning || job.info ? (
                  <span className="repair-dashboard-chip-bar">
                    {job.warning ? (
                      <span
                        className="repair-dashboard-chip-severity warning"
                        title={`${job.warning} warnings`}
                      />
                    ) : null}
                    {job.info ? (
                      <span
                        className="repair-dashboard-chip-severity info"
                        title={`${job.info} info`}
                      />
                    ) : null}
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}

      {showHealth && score ? (
        <div className="repair-cache-health">
          {/* The modal stays vanilla: `openCacheHealthModal` is reached from the
              Metadata Cache card too, and it opens onward into the ~320-line
              failed-MB-lookups manager. Same seam the launcher card uses, and the
              one globals.d.ts already declares for this call site. */}
          <div className="repair-cache-health-bar" onClick={() => window.openCacheHealthModal?.()}>
            <span className={`repair-cache-health-dot ${score}`} />
            <span className="repair-cache-health-title">Metadata Cache</span>
            <span className="repair-cache-health-summary">
              {(showHealth.total_entities || 0).toLocaleString()} entities ·{' '}
              {cacheHealthLabel(score)}
            </span>
            <span className="repair-cache-health-action">View Details ›</span>
          </div>
        </div>
      ) : null}
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
  onFix,
  onDismiss,
  onKeepDuplicate,
  onApplyCoverArt,
}: {
  finding: RepairFinding;
  selected: boolean;
  expanded: boolean;
  fixing: boolean;
  onToggleSelect: (id: number, checked: boolean) => void;
  onToggleDetail: (id: number) => void;
  onFix: (finding: RepairFinding) => Promise<void>;
  onDismiss: (id: number) => Promise<void>;
  onKeepDuplicate: (findingId: number, trackId: string) => void;
  onApplyCoverArt: (findingId: number, target: 'album' | 'artist') => void;
}) {
  const details = finding.details || {};
  const filePath = findingFilePath(finding);
  const fixLabel = findingFixLabel(finding.finding_type);
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
            <span>{finding.job_id.replace(/_/g, ' ')}</span>
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
        {/* This wrapper swallows clicks, which is also why the expand chevron
            below does nothing — see the note at the top of the file. */}
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
                title="Dismiss"
                onClick={() => void onDismiss(finding.id)}
              >
                &times;
              </button>
            </>
          ) : null}
          <button
            className={`repair-finding-expand-btn${expanded ? ' open' : ''}`}
            type="button"
            data-finding={finding.id}
            title="Details"
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
          <FindingDetail
            finding={finding}
            onKeepDuplicate={onKeepDuplicate}
            onApplyCoverArt={onApplyCoverArt}
          />
        </div>
      </div>
    </div>
  );
}
