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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { SHELL_LIBRARY_SCOPE_CHANGED_EVENT } from '@/platform/shell/bridge';

import type { FindingAlbumGroup, FindingsQuery, FindingsViewScope } from '../-tools.api';
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
  dismissFindingType,
  fetchBulkFixStatus,
  fetchCacheHealth,
  fetchFindingCounts,
  fetchFindingGroups,
  fetchFindingTypes,
  fetchRepairFindings,
  fixFinding,
  reopenFinding,
  reopenFindingsBulk,
  startBulkFix,
  stopBulkFix,
} from '../-tools.api';
import {
  bulkFixLoopMessage,
  bulkFixRunMessage,
  cacheHealthLabel,
  cacheHealthScore,
  findingFilePath,
  findingHeadline,
  findingRedownloadTrackId,
  findingRowFixLabel,
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
import { useToolsMedia } from '../-tools.media';
import { RedownloadModal } from '../../artist-detail/-ui/redownload-modal';
import { AlbumInspectionTray, groupIdentity } from './album-inspection-tray';
import { FindingArt } from './finding-art';
import { FindingDetail } from './finding-detail';
import {
  ReidentifyFindingModal,
  TYPE_ACOUSTID,
  TYPE_BACKFILL,
  TYPE_DEAD,
  TYPE_ORPHAN,
  TYPE_QUALITY,
  TYPE_RETAG,
  useFindingFix,
} from './finding-fix';
import { useFindingPrompts } from './finding-prompts';
import { FindingsAlbumGrid } from './findings-album-grid';
import { FindingsInbox } from './findings-inbox';
import { HealthHero } from './health-hero';
import { VideoFindingDetail } from './video-finding-detail';

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
}

// moved to finding-fix, which Simple mode shares (review 5 W3-TT-3)
export { openSuspectAlbum } from './finding-fix';

/** The vanilla's poll interval for a background Fix All run. */
const BULK_FIX_POLL_MS = 2000;

/** how long the undo bar stays after a dismiss. */
const UNDO_MS = 8000;

/** a dialog is up (a confirm, a prompt, the reidentify modal). the shell keeps
 *  hidden and faded dialogs in the page, so presence alone means nothing: the
 *  element and every ancestor must actually be showing and clickable. */
function visibleDialogOpen(): boolean {
  const candidates = document.querySelectorAll<HTMLElement>(
    '[role="dialog"], [aria-modal="true"], .repair-help-overlay',
  );
  for (const el of candidates) {
    let node: HTMLElement | null = el;
    let shown = true;
    while (node && node !== document.body) {
      const style = window.getComputedStyle(node);
      if (
        node.hidden ||
        style.display === 'none' ||
        style.visibility === 'hidden' ||
        style.opacity === '0' ||
        style.pointerEvents === 'none'
      ) {
        shown = false;
        break;
      }
      node = node.parentElement;
    }
    if (shown) return true;
  }
  return false;
}

const PAGE_SIZE_KEY = 'repairFindingsPageSize';

function readStoredPageSize(): number {
  try {
    return normalizeFindingsPageSize(localStorage.getItem(PAGE_SIZE_KEY));
  } catch {
    return REPAIR_DEFAULT_PAGE_SIZE;
  }
}

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

const VIDEO_SORT_OPTIONS = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'severity', label: 'Severity' },
  { value: 'title', label: 'Title' },
  { value: 'path', label: 'File path' },
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

/**
 * the newest finished run, as a key. the run history reloads when a job
 * finishes (#1144); when this changes, a job just landed findings.
 */
export function latestRunKey(runs: readonly RepairJobRun[]): string {
  let newest: RepairJobRun | null = null;
  for (const run of runs) {
    if (!run.finished_at) continue;
    if (!newest || String(run.finished_at) > String(newest.finished_at)) newest = run;
  }
  return newest ? `${newest.id ?? ''}@${newest.finished_at}` : '';
}

export interface FindingsSurfaceProps {
  /** The job list, for the job filter. */
  jobs: RepairJob[];
  /** Run history — the hero's trend line reads findings-per-run off it. */
  runs: RepairJobRun[];
  /** Library size, for the per-1,000-tracks health normalisation. */
  trackCount: number | null;
  /** What that size is OF, when it isn't tracks ("8,864 movies · 105,472
   *  episodes"). */
  sizeLabel?: string | null;
  /** A jump from the run history: scope the surface to one job's open
   *  findings. The token re-fires the same job. */
  focusJob?: {
    jobId: string;
    token: number;
    query?: string;
    severity?: string;
    findingType?: string;
  } | null;
  /** `updateRepairStatus()` — refresh the pending badge after any mutation. */
  onStatusChanged: () => void;
  /** Default view mode: 'albums' (default for UI) or 'inbox' (default for unit tests). */
  defaultView?: 'albums' | 'inbox' | 'table';
}

export function FindingsSurface({
  jobs,
  runs,
  trackCount,
  sizeLabel,
  focusJob,
  onStatusChanged,
  defaultView,
}: FindingsSurfaceProps) {
  const media = useToolsMedia();
  const video = media === 'video';
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

  const [viewMode, setViewMode] = useState<'albums' | 'inbox' | 'table'>(() => {
    // the album grid only holds findings that recorded an album, so as the
    // landing view it hid everything else: 134 orphan files were simply not
    // on screen. issue types shows every finding, so that's where you land,
    // and albums is a lens you pick (music only, video has no albums).
    if (defaultView && !(video && defaultView === 'albums')) return defaultView;
    try {
      const saved = localStorage.getItem('soulsync-findings-view-mode');
      if (saved === 'inbox' || saved === 'table') return saved;
      if (saved === 'albums' && !video) return saved;
    } catch {}
    return 'inbox';
  });

  const changeViewMode = (mode: 'albums' | 'inbox' | 'table') => {
    setViewMode(mode);
    try {
      localStorage.setItem('soulsync-findings-view-mode', mode);
    } catch {}
  };

  const [selectedAlbum, setSelectedAlbum] = useState<FindingAlbumGroup | null>(null);
  /** findings the album grid holds, so the albums view can say how many it
   *  CAN'T show. null until the grid loads, or when the server capped it. */
  const [albumCovered, setAlbumCovered] = useState<number | null>(null);
  const [redownloadFinding, setRedownloadFinding] = useState<RepairFinding | null>(null);
  const redownloadTrackId = redownloadFinding ? findingRedownloadTrackId(redownloadFinding) : null;

  /** Which group is expanded. Exactly one at a time: the open group hosts the
   *  single finding list, which is what lets every row feature survive
   *  unchanged instead of being rebuilt per group. */
  const [openType, setOpenType] = useState('');
  // list | album | artist. Resets whenever a different type is opened, because
  // a grouping that made sense for upgrades rarely does for the next type.
  const [groupView, setGroupView] = useState<'list' | 'album' | 'artist'>('list');
  /** an album or artist card drilled into from the grouped view: the open
   *  type's rows of that one group, by the grid's own key. it used to put the
   *  name in the search box, which escaped the type and listed every type's
   *  rows whose text held the name, for select all and Fix Selected to act
   *  on (review 5 W3-TT-2). */
  const [drill, setDrill] = useState<NonNullable<FindingsQuery['group']> | null>(null);

  const [groups, setGroups] = useState<FindingGroup[]>([]);
  /** the inbox rows: `groups` again, unless a job or severity filter is set,
   *  then counted by the server for those filters. a row's count is what its
   *  Fix all / Dismiss all / Reopen all act on, and only the server knows how
   *  many of a type's rows came from one job (review 5 W3-TT). the hero
   *  keeps the whole library. */
  const [inboxGroups, setInboxGroups] = useState<FindingGroup[]>([]);
  const [groupsLoaded, setGroupsLoaded] = useState(false);
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
  const [reidentifyingFinding, setReidentifyingFinding] = useState<RepairFinding | null>(null);

  const [bulkRun, setBulkRun] = useState<BulkFixStatus | null>(null);
  const bulkTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const prompts = useFindingPrompts();

  /** the last dismiss, offered back for a few seconds. dismiss means "never
   *  raise this again", which is only fair to make one click if it's one
   *  click to take back. */
  const [undo, setUndo] = useState<{ ids: number[]; label: string; token: number } | null>(null);
  const offerUndo = useCallback((ids: number[], label: string) => {
    setUndo((previous) => ({ ids, label, token: (previous?.token || 0) + 1 }));
  }, []);
  useEffect(() => {
    if (!undo) return;
    const timer = setTimeout(
      () => setUndo((current) => (current?.token === undo.token ? null : current)),
      UNDO_MS,
    );
    return () => clearTimeout(timer);
  }, [undo]);

  /** row lookups from callbacks that shouldn't re-create on every page load */
  const itemsRef = useRef<RepairFinding[] | null>(null);

  const typeMap = useMemo(() => {
    const map = new Map<string, FindingTypeInfo>();
    for (const info of types) map.set(info.type, info);
    return map;
  }, [types]);
  const typeInfo = useCallback((findingType: string) => typeMap.get(findingType), [typeMap]);

  /** the row's fix button label, or null when the row has none. the catalog
   *  decides (it knows every fixable type); the old client list is only the
   *  fallback before the catalog has loaded, and only on music. */
  const rowFixLabel = useCallback(
    (finding: RepairFinding): string | null => {
      const special = findingRowFixLabel(finding);
      if (special === 'Open album' || special === 'Delete File') return special;
      const info = typeMap.get(finding.finding_type);
      if (info) return info.fixable ? info.verb || 'Fix' : null;
      return video ? null : special;
    },
    [typeMap, video],
  );
  const rowCanFix = (finding: RepairFinding) => Boolean(rowFixLabel(finding));

  /** A non-empty search escapes the grouping: people search for a path or a
   *  title, not for a category, and making them guess the category first is
   *  the failure the inbox is supposed to remove. */
  const searching = query.trim().length > 0;
  const listType = searching ? '' : openType;
  /** a drill-in only narrows the open type's list; a search leaves both */
  const activeDrill = !searching && openType ? drill : null;

  /** what the list shows, sent with Fix Selected / Dismiss Selected so the
   *  server can refuse a row the view no longer lists (review 5 W3-TT) */
  const viewScope = useMemo<FindingsViewScope>(
    () => ({
      jobId: jobFilter,
      status: statusFilter,
      severity: severityFilter,
      findingType: listType,
      q: query.trim(),
      group: activeDrill ?? undefined,
    }),
    [activeDrill, jobFilter, listType, query, severityFilter, statusFilter],
  );
  /** the inbox's filters, for the group-row bulk buttons */
  const groupFilters = useMemo(
    () => ({ jobId: jobFilter || undefined, severity: severityFilter || undefined }),
    [jobFilter, severityFilter],
  );

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
    // the metadata cache is the music side's
    if (!video) setCacheHealth(await fetchCacheHealth());
  }, [video]);

  const groupsToken = useRef(0);
  const loadGroups = useCallback(async () => {
    // a filter changed while an older load was out: the newest wins
    const token = ++groupsToken.current;
    const filtered = Boolean(jobFilter || severityFilter);
    const [all, narrowed] = await Promise.all([
      fetchFindingGroups(),
      filtered ? fetchFindingGroups({ jobId: jobFilter, severity: severityFilter }) : null,
    ]);
    if (token !== groupsToken.current) return;
    setGroups(all);
    setInboxGroups(narrowed ?? all);
    setGroupsLoaded(true);
  }, [jobFilter, severityFilter]);

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
        group: activeDrill ?? undefined,
        page,
        limit: pageSize,
      });
      setLoadError(null);
      setSelected(new Set());
      setBusyFix(new Set());
      setTotal(data.total);
      setServerPage(data.page);
      itemsRef.current = data.items;
      setItems(data.items);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
    }
  }, [
    activeDrill,
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

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const changed = () => {
      timer ??= setTimeout(() => {
        timer = undefined;
        void loadCounts();
        void loadGroups();
        if (!selectedCount.current) void loadFindings();
      }, 500);
    };
    window.addEventListener('ss:library-changed', changed);
    return () => {
      window.removeEventListener('ss:library-changed', changed);
      clearTimeout(timer);
    };
  }, [loadCounts, loadGroups, loadFindings]);

  // G16-4: another library picked in the header. every count, group and row
  // was the old library's, and Fix All acted on what was on screen; a
  // selection from the old library is dropped, never carried over
  // review 4 R4-E-1: the album grids load on their own token, and an open
  // album tray is a selection too
  useEffect(() => {
    const switched = () => {
      setSelectedAlbum(null);
      setRefreshToken((token) => token + 1);
      refreshAll();
    };
    window.addEventListener(SHELL_LIBRARY_SCOPE_CHANGED_EVENT, switched);
    return () => window.removeEventListener(SHELL_LIBRARY_SCOPE_CHANGED_EVENT, switched);
  }, [refreshAll]);

  // a job finished: its findings only showed after a page refresh (#1386).
  // any change after the first render counts, even from no runs at all (a
  // fresh install's first job); the history arriving on open costs one
  // extra small reload. scheduled jobs finish in the background too, so the
  // open list only reloads when nothing's ticked: reloading it clears the
  // selection you were building
  const [refreshToken, setRefreshToken] = useState(0);
  const runKey = latestRunKey(runs);
  const seenRunKey = useRef<string | null>(null);
  const selectedCount = useRef(0);
  selectedCount.current = selected.size;
  useEffect(() => {
    const previous = seenRunKey.current;
    seenRunKey.current = runKey;
    if (previous === null || previous === runKey) return;
    void loadCounts();
    void loadGroups();
    if (selectedCount.current === 0) void loadFindings();
    onStatusChanged();
    setRefreshToken((token) => token + 1);
  }, [runKey, loadCounts, loadGroups, loadFindings, onStatusChanged]);

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
  }, [loadCounts]);

  useEffect(() => {
    void loadGroups();
  }, [loadGroups]);

  useEffect(() => {
    void fetchFindingTypes().then(setTypes);
  }, []);

  useEffect(() => {
    void loadFindings();
  }, [loadFindings]);

  /** Arriving from a run row or Studio Triage Center. Reset everything that could hide
   *  the rows the user just asked for. */
  useEffect(() => {
    if (!focusJob) return;
    setJobFilter(focusJob.jobId || '');
    setStatusFilter('pending');
    setSeverityFilter(focusJob.severity ?? '');
    setQuery(focusJob.query ?? '');
    setOpenType(focusJob.findingType ?? '');
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
      const title = itemsRef.current?.find((finding) => finding.id === id)?.title;
      offerUndo([id], title ? `Dismissed "${title}"` : 'Finding dismissed');
      refreshAll();
    },
    [offerUndo, refreshAll],
  );

  const runUndo = useCallback(async () => {
    const current = undo;
    if (!current) return;
    setUndo(null);
    let back = 0;
    for (const id of current.ids) {
      if (await reopenFinding(id)) back += 1;
    }
    toast(
      back
        ? `Brought back ${back.toLocaleString()} finding${back === 1 ? '' : 's'}`
        : 'Could not undo that',
      back ? 'success' : 'error',
    );
    refreshAll();
  }, [refreshAll, undo]);

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

  /** `fixRepairFinding`, shared with Simple mode's album tray so both ask the
   *  same per-type questions (review 5 W3-TT-3). The per-type prompts run
   *  BEFORE the button is disabled. `override` is a non-default action picked
   *  in the detail panel (the video ghost's Forget); it skips the prompts
   *  because the user just chose. */
  const expandFinding = useCallback((finding: RepairFinding) => {
    setExpanded((current) => new Set([...current, finding.id]));
  }, []);
  const setFixBusy = useCallback((id: number, busy: boolean) => {
    setBusyFix((current) => {
      const next = new Set(current);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);
  const fixOne = useFindingFix({
    video,
    typeInfo,
    prompts,
    dismiss: dismissOne,
    refresh: refreshAll,
    onExpand: expandFinding,
    onReidentify: setReidentifyingFinding,
    setBusy: setFixBusy,
  });

  const handleInspectRedownload = useCallback((finding: RepairFinding) => {
    setRedownloadFinding(finding);
  }, []);

  /** `selectDuplicateToKeep`. */
  const keepDuplicate = useCallback(
    async (findingId: number, trackId: string) => {
      const catalogue = trackId.startsWith('merge-');
      const native = trackId.startsWith('file-') || trackId.startsWith('keep_best');
      const confirmed = await window.showConfirmDialog?.({
        title: catalogue ? 'Merge Releases' : 'Keep This Version',
        message: catalogue
          ? 'Merge these catalogue entries into the selected release? Both provider editions and all physical audio files are preserved.'
          : native
            ? 'Keep this file and move redundant copies to recoverable quarantine? Protected companions, shared files and playlist copies stay.'
            : 'Keep this version and remove the other duplicate(s)?',
        confirmText: catalogue ? 'Merge' : 'Keep',
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
    const ids = [...selected];
    try {
      const { updated, refused } = await bulkFindingAction(ids, 'dismiss', viewScope);
      // the server dismissed only what this view still lists
      if (refused) {
        toast(
          `${refused.toLocaleString()} selected finding${refused === 1 ? ' is' : 's are'} no longer in this list and ${refused === 1 ? 'was' : 'were'} left alone`,
          'info',
        );
      }
      offerUndo(ids, `${(updated || ids.length - refused).toLocaleString()} findings dismissed`);
      refreshAll();
    } catch {
      toast('Error updating findings', 'error');
    }
  }, [offerUndo, refreshAll, selected, viewScope]);

  /** `bulkFixFindings` — ask once per finding KIND, then fix one id at a time. */
  const bulkFix = useCallback(async () => {
    if (selected.size === 0) return;
    const byId = new Map((items || []).map((finding) => [finding.id, finding]));
    const ids = [...selected].filter(
      (id) => byId.get(id)?.finding_type !== 'native_duplicate_releases',
    );
    if (ids.length !== selected.size) {
      toast('Review each duplicate release and select the release group to keep.', 'info');
      if (!ids.length) return;
    }
    const typeOf = (id: number) => byId.get(id)?.finding_type;
    const withType = (findingType: string) => ids.filter((id) => typeOf(id) === findingType);

    let orphanAction: string | null = null;
    let deadAction: string | null = null;
    let acoustidAction: string | null = null;
    let backfillAction: string | null = null;
    let qualityAction: string | null = null;
    if (video) {
      // one question for the whole selection, only when something in it
      // changes a file on disk
      const risky = [...new Set(ids.map(typeOf).filter(Boolean) as string[])].filter(
        (findingType) => typeInfo(findingType)?.destructive,
      );
      if (risky.length) {
        const labels = risky.map((t) => typeInfo(t)?.label || t.replace(/_/g, ' ')).join(', ');
        const confirmed = await window.showConfirmDialog?.({
          title: `Fix ${ids.length.toLocaleString()} findings`,
          message: `Some of these change files on disk (${labels}). ${
            risky.length === 1 ? typeInfo(risky[0])?.confirm || '' : ''
          }`.trim(),
          confirmText: 'Fix selected',
          destructive: true,
        });
        if (!confirmed) return;
      }
    } else {
      const orphanIds = withType(TYPE_ORPHAN);
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
      if (deadIds.length > 0) {
        deadAction = await prompts.promptDeadFile();
        if (!deadAction) return;
      }

      const acoustidIds = withType(TYPE_ACOUSTID);
      if (acoustidIds.length > 0) {
        acoustidAction = await prompts.promptAcoustid(undefined, acoustidIds.length);
        if (!acoustidAction) return;
      }

      const backfillIds = withType(TYPE_BACKFILL);
      if (backfillIds.length > 0) {
        backfillAction = await prompts.promptBackfill(backfillIds.length);
        if (!backfillAction) return;
      }

      const qualityIds = withType(TYPE_QUALITY);
      if (qualityIds.length > 0) {
        qualityAction = await prompts.promptQuality();
        if (!qualityAction) return;
      }
    }

    const nativeDuplicateIds = withType('native_duplicate_tracks');
    let nativeDuplicateAction: string | null = null;
    if (nativeDuplicateIds.length) {
      const weak = nativeDuplicateIds.some((id) =>
        Boolean(byId.get(id)?.details?.requires_recording_confirmation),
      );
      const confirmed = await window.showConfirmDialog?.({
        title: weak ? 'Confirm Recordings & Keep Best' : 'Keep Best',
        message: weak
          ? 'Confirm that each selected duplicate group contains the same recording after reviewing its files. Keep the recommended copies and quarantine redundant files? Protected copies stay.'
          : 'Keep the recommended copies and move redundant files to recoverable quarantine? Protected copies stay.',
        confirmText: weak ? 'Confirm Recordings & Keep Best' : 'Keep Best',
        destructive: true,
      });
      if (!confirmed) return;
      nativeDuplicateAction = weak ? 'keep_best:confirmed' : 'keep_best';
    }

    let fixed = 0;
    let failed = 0;
    let skipped = 0;
    let refused = 0;
    let otherArtistSkipped = 0;
    let lastError = '';
    toast(`Fixing ${ids.length} findings...`, 'info');

    // Backfill "Just Clear" and quality "Ignore" both dismiss rather than
    // fix, so they bypass the fix endpoint entirely. one scoped dismiss, so
    // the server checks them against this view like every other row
    // (review 5 W3-TT).
    const dismissIds = ids.filter(
      (id) =>
        (typeOf(id) === TYPE_BACKFILL && backfillAction === 'dismiss') ||
        (typeOf(id) === TYPE_QUALITY && qualityAction === 'ignore'),
    );
    if (dismissIds.length) {
      try {
        const result = await bulkFindingAction(dismissIds, 'dismiss', viewScope);
        fixed += result.updated;
        refused += result.refused;
        const lost = dismissIds.length - result.updated - result.refused;
        if (lost > 0) {
          failed += lost;
          lastError = 'dismiss failed';
        }
      } catch {
        failed += dismissIds.length;
        lastError = 'dismiss failed';
      }
    }
    const dismissed = new Set(dismissIds);

    for (const id of ids) {
      if (dismissed.has(id)) continue;
      const findingType = typeOf(id);
      try {
        let fixAction: string | null = null;
        if (findingType === TYPE_ORPHAN && orphanAction) fixAction = orphanAction;
        else if (findingType === TYPE_DEAD && deadAction) fixAction = deadAction;
        else if (findingType === TYPE_ACOUSTID && acoustidAction) fixAction = acoustidAction;
        else if (findingType === TYPE_QUALITY && qualityAction) fixAction = qualityAction;
        else if (findingType === 'native_duplicate_tracks') fixAction = nativeDuplicateAction;
        // Backfill "Add to Wishlist" falls through with no action — the fix
        // handler already adds to the wishlist by default.

        // bulk: true — fixing a selection is a bulk run even though it
        // posts one id at a time. Without it every selected finding
        // would count as an explicit single fix, and the album gate
        // would let a select-all wishlist every other artist on every
        // compilation in the selection.
        // scope: the server refuses a row this view no longer lists
        const result = await fixFinding(id, fixAction, { bulk: true, scope: viewScope });
        // Counted even on a successful fix: a partial gate resolves the
        // finding, and without this the held-back tracks are silent here
        // (the background Fix All reports them in its run note).
        otherArtistSkipped += result.other_artist_skipped || 0;
        if (result.refused) refused++;
        else if (result.success) fixed++;
        else if (result.skipped_by_design) skipped++;
        else {
          failed++;
          lastError = result.error || 'unknown error';
        }
      } catch {
        failed++;
      }
    }

    const { message, type } = bulkFixLoopMessage(
      fixed,
      failed,
      lastError,
      skipped,
      otherArtistSkipped,
    );
    toast(message, type);
    if (refused) {
      toast(
        `${refused.toLocaleString()} selected finding${refused === 1 ? ' is' : 's are'} no longer in this list and ${refused === 1 ? 'was' : 'were'} left alone`,
        'info',
      );
    }
    refreshAll();
  }, [items, prompts, refreshAll, selected, typeInfo, video, viewScope]);

  // ── Group-scoped actions ───────────────────────────────────────────────────

  /**
   * Fix a whole group. Every run is scoped to ONE finding type, which is the
   * only way `fix_action` can mean anything — 'delete' removes the file for
   * an orphan and names the track to KEEP for a duplicate.
   */
  const fixGroup = useCallback(
    async (group: FindingGroup, info: FindingTypeInfo | undefined) => {
      if (group.finding_type === 'native_duplicate_releases') {
        toast('Review each duplicate release and select the release group to keep.', 'info');
        return;
      }
      const label = info?.label || group.finding_type.replace(/_/g, ' ');
      const count = group.pending;
      let fixAction: string | null = null;

      // Everything without a question of its own takes its default action.
      // Destructive types still spell out what happens to files; safe ones
      // just confirm the scale.
      const confirmDefault = () =>
        window.showConfirmDialog?.({
          title: `${info?.verb || 'Fix'} ${label}`,
          message: info?.confirm
            ? `Apply "${info.verb || 'Fix'}" to all ${count.toLocaleString()} ${label.toLowerCase()} findings? ${info.confirm}`
            : info?.destructive
              ? `Apply "${info.verb || 'Fix'}" to all ${count.toLocaleString()} ${label.toLowerCase()} findings? This moves or deletes files on disk and cannot be undone.`
              : `Apply "${info?.verb || 'Fix'}" to all ${count.toLocaleString()} ${label.toLowerCase()} findings? This only writes metadata — no files are deleted or moved.`,
          confirmText: info?.verb || 'Fix',
          destructive: Boolean(info?.destructive),
        });

      if (video) {
        // the music questions below key on slugs video shares (quality_upgrade)
        // but means something else by. video asks the one catalog question.
        if (!(await confirmDefault())) return;
      } else if (group.finding_type === TYPE_BACKFILL) {
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
                    // and to the severity the row counted (review 5 W3-TT)
                    severity: groupFilters.severity,
                  })
                : {
                    success: true,
                    deleted: (await dismissFindingType(group.finding_type, groupFilters)).updated,
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
        fixAction = await prompts.promptAcoustid(undefined, count);
        if (!fixAction) return;
      } else if (group.finding_type === TYPE_QUALITY) {
        fixAction = await prompts.promptQuality();
        if (!fixAction) return;
        if (fixAction === 'ignore') {
          const updated = (await dismissFindingType(group.finding_type, groupFilters)).updated || 0;
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
      } else if (group.finding_type === 'native_duplicate_tracks') {
        const confirmed = await window.showConfirmDialog?.({
          title: 'Keep Best',
          message: `Keep recommended copies for ${count.toLocaleString()} duplicate findings and move redundant files to recoverable quarantine? Protected copies stay. Weak matches remain pending for individual recording confirmation.`,
          confirmText: 'Keep Best',
          destructive: true,
        });
        if (!confirmed) return;
        fixAction = 'keep_best';
      } else if (!(await confirmDefault())) {
        return;
      }

      try {
        // the inbox's job / severity filters: the run fixes the rows the
        // row counted, not every row of the type (review 5 W3-TT)
        const result = await startBulkFix({
          findingType: group.finding_type,
          fixAction,
          jobId: groupFilters.jobId,
          severity: groupFilters.severity,
        });
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
    [groupFilters, prompts, refreshAll, video, watchBulkFixRun],
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
        const result = await dismissFindingType(group.finding_type, groupFilters);
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
    [groupFilters, refreshAll],
  );

  /** Reopen all dismissed/resolved findings of one type — the bulk undo for
   *  dismiss. */
  const reopenGroup = useCallback(
    async (group: FindingGroup, info: FindingTypeInfo | undefined) => {
      const label = info?.label || group.finding_type.replace(/_/g, ' ');
      const total = group.dismissed || 0;
      const confirmed = await window.showConfirmDialog?.({
        title: `Reopen all ${label}`,
        message: `Reopen ${total.toLocaleString()} dismissed ${label.toLowerCase()} findings? They will show as pending again.`,
        confirmText: 'Reopen all',
        destructive: false,
      });
      if (!confirmed) return;
      try {
        const count = await reopenFindingsBulk(
          groupFilters.jobId,
          group.finding_type,
          groupFilters.severity,
        );
        toast(
          count > 0 ? `Reopened ${count.toLocaleString()} findings` : 'No findings to reopen',
          count > 0 ? 'success' : 'info',
        );
        refreshAll();
      } catch (e) {
        toast(e instanceof Error ? e.message : 'Error reopening findings', 'error');
      }
    },
    [groupFilters, refreshAll],
  );

  const safeCount = useMemo(() => safeFixablePending(groups, typeInfo), [groups, typeInfo]);

  /** Every safe type in one run. The backend drops destructive types itself
   *  (`safe_only`), so this cannot become "fix everything" through drift. */
  const fixAllSafe = useCallback(async () => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Fix all safe findings',
      message: video
        ? `Apply the default fix to all ${safeCount.toLocaleString()} findings that only change SoulSync's own data? Nothing on disk is touched and nothing is downloaded; those findings are skipped.`
        : `Apply the default fix to all ${safeCount.toLocaleString()} findings that only write metadata? Nothing is deleted or moved — anything that touches a file on disk is skipped.`,
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
  }, [safeCount, video, watchBulkFixRun]);

  /** `clearRepairFindings` — deletes rows outright, filter-scoped. */
  const clearAll = useCallback(async () => {
    const needle = query.trim();
    const scopeLabel = jobFilter ? jobLabel(jobFilter) : 'all jobs';
    const statusLabel = statusFilter ? ` (${statusFilter})` : '';
    // Spell out EVERY filter being applied. The button deletes rows for good,
    // and its old message named only the job and status — so a user who had
    // narrowed by severity or search read a prompt describing a far wider
    // delete than they expected, and got one (#1142).
    // a drilled-in card is a filter too: its type and its album or artist
    // (review 5 W3-TT-2)
    const drillType = activeDrill ? typeInfo(listType)?.label || listType.replace(/_/g, ' ') : '';
    const extra = [
      severityFilter ? `severity ${severityFilter}` : '',
      needle ? `matching "${needle}"` : '',
      activeDrill ? `${drillType} only` : '',
      activeDrill
        ? activeDrill.groupBy === 'album'
          ? `on "${activeDrill.album}" by ${activeDrill.artist}`
          : `by ${activeDrill.artist}`
        : '',
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
        findingType: activeDrill ? listType : undefined,
        group: activeDrill ?? undefined,
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
  }, [
    activeDrill,
    jobFilter,
    jobLabel,
    listType,
    query,
    refreshAll,
    severityFilter,
    statusFilter,
    typeInfo,
  ]);

  // ── Keyboard triage ────────────────────────────────────────────────────────
  //
  // j/k move, x selects, enter opens, f fixes, d dismisses, z undoes, / jumps
  // to search. gmail's keys, because working through two hundred findings
  // with a mouse is the slow part of all this. letters only: arrows and space
  // keep scrolling the page like they always did.

  const [cursor, setCursor] = useState(-1);
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    setCursor(-1);
  }, [items]);

  useEffect(() => {
    if (cursor < 0 || !items?.[cursor]) return;
    document
      .querySelector(`.repair-finding-card[data-id="${items[cursor].id}"]`)
      ?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [cursor, items]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest?.('input, textarea, select, [contenteditable="true"]')) return;
      // the surface is mounted but hidden in simple mode and on the tools tab
      if (!surfaceRef.current || surfaceRef.current.offsetParent === null) return;
      if (visibleDialogOpen()) return;
      const key = event.key;
      if (key === '/') {
        event.preventDefault();
        document.getElementById('repair-findings-search')?.focus();
        return;
      }
      if (key === 'z' && undo) {
        event.preventDefault();
        void runUndo();
        return;
      }
      const rows = items || [];
      if (!rows.length) return;
      if (key === 'j' || key === 'k') {
        event.preventDefault();
        setCursor((current) =>
          key === 'j' ? Math.min(rows.length - 1, current + 1) : Math.max(0, current - 1),
        );
        return;
      }
      const row = rows[cursor];
      if (!row) return;
      if (key === 'x') {
        event.preventDefault();
        toggleSelect(row.id, !selected.has(row.id));
      } else if (key === 'Enter' || key === 'o') {
        event.preventDefault();
        toggleDetail(row.id);
      } else if (key === 'd' && row.status === 'pending') {
        event.preventDefault();
        void dismissOne(row.id);
      } else if (key === 'f' && row.status === 'pending' && rowCanFix(row)) {
        event.preventDefault();
        void fixOne(row);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  });

  // ── Render ─────────────────────────────────────────────────────────────────

  const shown = useMemo(
    () =>
      visibleGroups(inboxGroups, {
        jobId: jobFilter,
        severity: severityFilter,
        status: statusFilter,
      }),
    [inboxGroups, jobFilter, severityFilter, statusFilter],
  );

  /**
   * Fixing a group empties it, and the pending filter then hides it — which
   * would leave `openType` pointing at a row that is no longer on screen and
   * a fetched list with nowhere to render. Fall back to the inbox.
   */
  useEffect(() => {
    if (openType && !shown.some((group) => group.finding_type === openType)) setOpenType('');
  }, [openType, shown]);

  /** one type is open in the inbox: the surface shows that type alone */
  const focused = Boolean(openType) && !searching && viewMode === 'inbox';
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
  /** only comparable with no narrowing filter, and only for one status (the
   *  album endpoint reads "all statuses" as pending) */
  const albumsUncovered =
    albumCovered !== null && statusFilter && !jobFilter && !severityFilter && !query.trim()
      ? Math.max(0, (statusCount(statusFilter) ?? 0) - albumCovered)
      : 0;

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
    setDrill(null);
  }, [openType]);

  /* The list/album/artist switch. Only offered inside an opened type - grouping
     across every type at once would mix "192kbps" and "missing lyrics" into one
     album card and mean nothing. */
  const viewSwitch =
    openType && !video ? (
      <div className="repair-view-switch" role="group" aria-label="Group findings by">
        {(
          [
            ['list', 'List'],
            ['album', 'Albums'],
            ['artist', 'Artists'],
          ] as const
        ).map(([value, label]) => (
          <button
            type="button"
            key={value}
            aria-pressed={groupView === value && !(value === 'list' && activeDrill)}
            onClick={() => {
              setGroupView(value);
              setDrill(null);
              setPage(0);
            }}
          >
            {label}
          </button>
        ))}
      </div>
    ) : null;

  /* Drilling into a card lists the open type's rows of that one album (or
     artist) by the grid's own key, and every existing control (select all,
     bulk fix, sort) keeps working on exactly those rows. It used to put the
     name in the search box, which dropped the type and listed any finding
     whose text held the name (review 5 W3-TT-2). */
  const groupedView =
    openType && groupView !== 'list' ? (
      <FindingsAlbumGrid
        groupBy={groupView}
        jobId={jobFilter || undefined}
        status={statusFilter}
        findingType={openType}
        refreshToken={refreshToken}
        onOpen={(group) => {
          const identity = groupIdentity(group);
          if (!identity) {
            toast(
              'This card has no artist or album name, so its findings can’t be listed alone',
              'info',
            );
            return;
          }
          setDrill(identity);
          setGroupView('list');
          setPage(0);
        }}
      />
    ) : null;

  const drillChip = activeDrill ? (
    <span className="repair-drill-chip" data-testid="repair-drill-chip">
      <span className="repair-drill-chip-label">
        {activeDrill.groupBy === 'album' ? (
          <>
            <b>{activeDrill.album}</b> by {activeDrill.artist}
          </>
        ) : (
          <>
            by <b>{activeDrill.artist}</b>
          </>
        )}
      </span>
      <button
        type="button"
        className="repair-drill-chip-clear"
        aria-label="Show every finding of this type again"
        title="Show every finding of this type again"
        onClick={() => {
          setDrill(null);
          setPage(0);
        }}
      >
        &times;
      </button>
    </span>
  ) : null;

  const findingList = (
    <>
      {viewSwitch ? (
        <div className="repair-findings-toolbar">
          {viewSwitch}
          {groupView === 'list' ? drillChip : null}
        </div>
      ) : null}
      {groupedView}
      {groupedView ? null : (
        <>
          {bar.showBar ? (
            <div className="repair-findings-bulk" id="repair-findings-selection">
              <span className="repair-bulk-count">{bar.countLabel}</span>
              <button
                className="btn btn--sm btn--primary"
                type="button"
                onClick={() => void bulkFix()}
              >
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
              {(video ? VIDEO_SORT_OPTIONS : SORT_OPTIONS).map((option) => (
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
            <span className="repair-kb-hint" aria-hidden="true">
              <kbd>j</kbd>
              <kbd>k</kbd> move · <kbd>x</kbd> select · <kbd>f</kbd> fix · <kbd>d</kbd> dismiss ·{' '}
              <kbd>z</kbd> undo
            </span>
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
              items.map((finding, index) => (
                <FindingCard
                  finding={finding}
                  key={finding.id}
                  video={video}
                  info={typeInfo(finding.finding_type)}
                  fixLabel={rowFixLabel(finding)}
                  active={index === cursor}
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
                  onInspectRedownload={handleInspectRedownload}
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
                {pagination.showFirstEllipsis ? (
                  <span className="repair-page-info">...</span>
                ) : null}
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
    <div className={`repair-findings-surface${focused ? ' focused' : ''}`} ref={surfaceRef}>
      {/* one type is open: the page is that type now, and the library's
          health is a click back. kept mounted so its numbers don't reload. */}
      <div className="repair-findings-overview" hidden={focused}>
        <HealthHero
          groups={groups}
          typeInfo={typeInfo}
          trackCount={trackCount}
          sizeLabel={sizeLabel}
          loading={!groupsLoaded}
          runs={runs}
          runningJobs={runningJobs}
          safeCount={safeCount}
          onFixAllSafe={() => void fixAllSafe()}
          fixAllBusy={Boolean(bulkRun?.running)}
          onPickType={pickType}
        />
      </div>

      {bulkRunBar}

      {!focused && cacheHealth && (cacheHealth.total_entities || cacheHealth.total_searches) ? (
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

        {/* inside one type the question is how to group ITS rows (the List /
            Albums / Artists switch below), not which view of everything */}
        {focused ? null : (
          <div className="repair-view-mode-tabs" role="tablist" aria-label="Findings View Mode">
            {/* issue types first: it's the view that holds every finding */}
            <button
              type="button"
              role="tab"
              aria-selected={viewMode === 'inbox'}
              className={`repair-view-mode-tab${viewMode === 'inbox' ? ' active' : ''}`}
              onClick={() => changeViewMode('inbox')}
            >
              Issue types
            </button>
            {video ? null : (
              <button
                type="button"
                role="tab"
                aria-selected={viewMode === 'albums'}
                className={`repair-view-mode-tab${viewMode === 'albums' ? ' active' : ''}`}
                onClick={() => changeViewMode('albums')}
              >
                Albums
              </button>
            )}
            <button
              type="button"
              role="tab"
              aria-selected={viewMode === 'table'}
              className={`repair-view-mode-tab${viewMode === 'table' ? ' active' : ''}`}
              onClick={() => changeViewMode('table')}
            >
              Flat list
            </button>
          </div>
        )}

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

        {/* deletes rows outright, which is not what dismiss does (dismiss
            remembers, clear forgets and lets the next scan raise them again).
            it sits last and quiet so it isn't mistaken for the triage button. */}
        <button
          className="repair-clear-btn"
          type="button"
          title="Delete the findings these filters match. The next scan can raise them again. Dismiss is the one that remembers."
          onClick={() => void clearAll()}
        >
          Delete matching…
        </button>
      </div>

      {viewMode === 'albums' && !searching ? (
        <div className="repair-albums-view">
          {selectedAlbum ? (
            <AlbumInspectionTray
              group={selectedAlbum}
              status={statusFilter}
              jobId={jobFilter || undefined}
              findingType={listType || undefined}
              onClose={() => setSelectedAlbum(null)}
              onFixFinding={fixOne}
              onDismissFinding={dismissOne}
              onInspectRedownload={handleInspectRedownload}
              onRefresh={refreshAll}
              onReopenFinding={async (id) => {
                if (await reopenFinding(id)) {
                  refreshAll();
                }
              }}
            />
          ) : null}
          {albumsUncovered > 0 ? (
            <div className="repair-albums-uncovered" role="note">
              <span>
                <strong>{albumsUncovered.toLocaleString()}</strong>{' '}
                {albumsUncovered === 1 ? 'finding isn’t' : 'findings aren’t'} about an album, so
                {albumsUncovered === 1 ? ' it isn’t' : ' they aren’t'} in this grid.
              </span>
              <button type="button" onClick={() => changeViewMode('inbox')}>
                See every finding by issue type
              </button>
            </div>
          ) : null}
          <FindingsAlbumGrid
            groupBy="album"
            jobId={jobFilter || undefined}
            status={statusFilter || undefined}
            findingType={listType || undefined}
            q={query.trim() || undefined}
            selectedGroupKey={selectedAlbum?.key}
            refreshToken={refreshToken}
            onOpen={(group) => setSelectedAlbum(group)}
            onLoaded={(covered, capped) => setAlbumCovered(capped ? null : covered)}
          />
        </div>
      ) : viewMode === 'table' && !searching ? (
        findingList
      ) : searching ? (
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
          severity={severityFilter}
          openType={openType}
          onToggleOpen={toggleOpenGroup}
          onFixGroup={(group, info) => void fixGroup(group, info)}
          onDismissGroup={(group, info) => void dismissGroup(group, info)}
          onReopenGroup={(group, info) => void reopenGroup(group, info)}
          busy={Boolean(bulkRun?.running)}
        >
          {findingList}
        </FindingsInbox>
      )}

      {prompts.promptNode}

      {reidentifyingFinding ? (
        <ReidentifyFindingModal
          finding={reidentifyingFinding}
          onApplied={async () => {
            await dismissOne(reidentifyingFinding.id);
          }}
          onClose={() => {
            setReidentifyingFinding(null);
            refreshAll();
          }}
        />
      ) : null}

      {/* a finding with no track behind it (a fake-lossless FILE finding) has no
          id to search for; its finding id is not a track id */}
      {redownloadFinding && redownloadTrackId ? (
        <RedownloadModal
          track={{
            id: redownloadTrackId,
            track_id: redownloadTrackId,
            title: String(
              (redownloadFinding.details as Record<string, any>)?.track_title ||
                redownloadFinding.title ||
                '',
            ),
            file_path:
              (redownloadFinding.details as Record<string, any>)?.file_path ||
              redownloadFinding.file_path ||
              '',
            format: (redownloadFinding.details as Record<string, any>)?.format || '',
            bitrate: (redownloadFinding.details as Record<string, any>)?.bitrate || 0,
          }}
          album={{
            id: (redownloadFinding.details as Record<string, any>)?.album_id || '',
            name:
              (redownloadFinding.details as Record<string, any>)?.album_title ||
              (redownloadFinding.details as Record<string, any>)?.album ||
              '',
            title:
              (redownloadFinding.details as Record<string, any>)?.album_title ||
              (redownloadFinding.details as Record<string, any>)?.album ||
              '',
            tracks: [
              {
                id: redownloadTrackId,
                track_id: redownloadTrackId,
                title: String(
                  (redownloadFinding.details as Record<string, any>)?.track_title ||
                    redownloadFinding.title ||
                    '',
                ),
                file_path:
                  (redownloadFinding.details as Record<string, any>)?.file_path ||
                  redownloadFinding.file_path ||
                  '',
              },
            ],
          }}
          artistName={String(
            (redownloadFinding.details as Record<string, any>)?.artist_name ||
              (redownloadFinding.details as Record<string, any>)?.artist ||
              '',
          )}
          upgrade={
            redownloadFinding.finding_type === 'quality_upgrade' ||
            redownloadFinding.finding_type === 'fake_lossless'
          }
          onReload={() => {
            refreshAll();
          }}
          onClose={() => {
            setRedownloadFinding(null);
            refreshAll();
          }}
        />
      ) : null}

      {undo ? (
        <div className="repair-undo-bar" role="status" aria-live="polite">
          <span className="repair-undo-text">{undo.label}</span>
          <button type="button" className="repair-undo-btn" onClick={() => void runUndo()}>
            Undo
          </button>
          <button
            type="button"
            className="repair-undo-close"
            aria-label="Close"
            onClick={() => setUndo(null)}
          >
            &times;
          </button>
        </div>
      ) : null}
    </div>
  );
}

// ── A finding card ───────────────────────────────────────────────────────────

/** music types whose fix is a re-download: the row opens the source picker
 *  (RedownloadModal) instead of firing a blind fix. */
const MUSIC_REDOWNLOAD_TYPES = new Set([
  'quality_upgrade',
  'missing_discography_track',
  'fake_lossless',
  'short_preview_track',
  'dead_file',
]);

function FindingCard({
  finding,
  video,
  info,
  fixLabel,
  active,
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
  onInspectRedownload,
}: {
  finding: RepairFinding;
  video?: boolean;
  /** the served catalog row for this type */
  info?: FindingTypeInfo;
  /** the fix button's label, or null for a review-only row */
  fixLabel: string | null;
  /** the keyboard cursor is on this row */
  active?: boolean;
  selected: boolean;
  expanded: boolean;
  fixing: boolean;
  onToggleSelect: (id: number, checked: boolean) => void;
  onToggleDetail: (id: number) => void;
  jobLabel: (jobId: string) => string;
  onFix: (finding: RepairFinding, override?: string) => Promise<void>;
  onDismiss: (id: number) => Promise<void>;
  onReopen: (id: number) => Promise<void>;
  onKeepDuplicate: (findingId: number, trackId: string) => void;
  onApplyCoverArt: (findingId: number, target: 'album' | 'artist') => void;
  onInspectRedownload?: (finding: RepairFinding) => void;
}) {
  const details = finding.details || {};
  const filePath = findingFilePath(finding);
  const statusBadge = findingStatusBadge(finding.status, finding.user_action);
  const typeLabel = info?.label || findingTypeLabel(finding.finding_type);
  const age = formatCacheAge(finding.created_at);
  const headline = findingHeadline(finding);

  return (
    <div
      className={`repair-finding-card ${finding.severity}${active ? ' kb-active' : ''}${
        expanded ? ' expanded' : ''
      }`}
      data-id={finding.id}
      data-job-id={finding.job_id}
      data-mass-orphan={String(Boolean(details.mass_orphan))}
    >
      <div className="repair-finding-main" onClick={() => onToggleDetail(finding.id)}>
        <div className="repair-finding-select" onClick={(event) => event.stopPropagation()}>
          <input
            type="checkbox"
            checked={selected}
            aria-label={`Select ${finding.title}`}
            onChange={(event) => onToggleSelect(finding.id, event.target.checked)}
          />
        </div>
        <FindingArt
          album={finding.album_thumb_url}
          artist={finding.artist_thumb_url}
          file={finding.entity_type === 'file' || finding.entity_type === 'folder'}
        />
        <div className="repair-finding-content">
          <div className="repair-finding-title">
            {finding.severity === 'info' ? null : (
              <span
                className={`repair-finding-icon ${finding.severity}`}
                title={finding.severity}
              />
            )}
            <span className="repair-finding-name">{headline.title}</span>
            <span className="repair-finding-type-badge">{typeLabel}</span>
            {statusBadge ? (
              <span className={`repair-finding-status-badge ${finding.status}`}>{statusBadge}</span>
            ) : null}
          </div>
          <div className="repair-finding-desc">{headline.sub}</div>
          {/* a row with a cover already says what it is; its path waits in
              the opened row. a bare file has nothing else to go by. */}
          {filePath && (expanded || !(finding.album_thumb_url || finding.artist_thumb_url)) ? (
            <div className="repair-finding-path">{filePath}</div>
          ) : null}
          {expanded ? (
            <div className="repair-finding-meta">
              {/* The display name the filter dropdown uses — a row showing the
                  raw snake_case id meant the same job went by two different
                  names on one screen. The entity id is for us, not the user:
                  it lives in the tooltip now. */}
              <span
                title={
                  finding.entity_id
                    ? `${finding.entity_type || 'item'} ${finding.entity_id}`
                    : undefined
                }
              >
                {jobLabel(finding.job_id)}
              </span>
              <span>&middot;</span>
              <span>
                {age === 'now' ? 'found just now' : age === '—' ? 'found' : `found ${age} ago`}
              </span>
            </div>
          ) : null}
        </div>
        {headline.fields.length ? (
          <div className="repair-finding-fields" aria-label="Fields that differ">
            {headline.fields.slice(0, 4).map((field) => (
              <span className="repair-finding-field" key={field}>
                {field}
              </span>
            ))}
            {headline.fields.length > 4 ? (
              <span className="repair-finding-field more">+{headline.fields.length - 4}</span>
            ) : null}
          </div>
        ) : null}
        <span className="repair-finding-age">{age === '—' ? '' : age === 'now' ? 'now' : age}</span>
        {/* This wrapper swallows clicks so the row toggle doesn't undo them. */}
        <div className="repair-finding-actions" onClick={(event) => event.stopPropagation()}>
          {finding.status === 'pending' ? (
            <>
              {fixLabel ? (
                <button
                  className={`repair-finding-btn fix${info?.destructive ? ' destructive' : ''}`}
                  type="button"
                  title={info?.confirm ? `${fixLabel}: ${info.confirm}` : fixLabel}
                  disabled={fixing}
                  onClick={() => {
                    if (
                      !video &&
                      (MUSIC_REDOWNLOAD_TYPES.has(finding.finding_type) ||
                        Boolean(fixLabel?.toLowerCase().includes('download'))) &&
                      onInspectRedownload &&
                      findingRedownloadTrackId(finding)
                    ) {
                      onInspectRedownload(finding);
                    } else {
                      void onFix(finding);
                    }
                  }}
                >
                  {fixing ? '...' : fixLabel}
                </button>
              ) : null}
              <button
                className="repair-finding-btn dismiss"
                type="button"
                title="Dismiss. It won't be raised again (you can undo, or reopen it later)"
                aria-label={`Dismiss ${finding.title}`}
                onClick={() => void onDismiss(finding.id)}
              >
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
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
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
        </div>
      </div>
      {finding.last_error ? (
        <div className="repair-finding-error-banner" onClick={(event) => event.stopPropagation()}>
          <div className="repair-finding-error-header">
            <span className="repair-finding-error-title">Resolution Attempt Failed</span>
          </div>
          <div className="repair-finding-error-text">{finding.last_error}</div>
          <button
            type="button"
            className="repair-finding-error-retry"
            disabled={fixing}
            onClick={() => void onFix(finding)}
          >
            Retry Fix
          </button>
        </div>
      ) : null}
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
            video ? (
              <VideoFindingDetail
                finding={finding}
                onFixAction={(target, action) => void onFix(target, action)}
              />
            ) : (
              <FindingDetail
                finding={finding}
                onKeepDuplicate={onKeepDuplicate}
                onApplyCoverArt={onApplyCoverArt}
              />
            )
          ) : null}
        </div>
      </div>
    </div>
  );
}
