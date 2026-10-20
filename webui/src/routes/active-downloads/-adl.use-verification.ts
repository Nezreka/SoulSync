import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  AdlDeletedList,
  AdlQuarantineEntry,
  AdlReviewSummary,
  AdlSubView,
} from './-adl.types';

import {
  QUARANTINE_PAGE_SIZE,
  fetchDeletedFiles,
  fetchQuarantine,
  fetchReviewQueueSummary,
  fetchVerificationConfig,
} from './-adl.api';

export interface AdlVerificationState {
  /**
   * Whether an unverified review queue can exist at all.
   *
   * `null` until the config lands, and treated as enabled meanwhile — showing
   * the pill and then hiding it is better than flashing quarantine-only at
   * everyone on every page load.
   */
  acoustidEnabled: boolean | null;
  subView: AdlSubView;
  quarantine: AdlQuarantineEntry[];
  quarantineTotal: number | null;
  /** Raw server offset consumed so far — see quarantineOffsetRef. */
  quarantineOffset: number;
  quarantineLoaded: boolean;
  /** the last list-fetch failure; null when the list is trustworthy. */
  quarantineError: string | null;
  /**
   * server counts, polled. null until the first one lands.
   *
   * the quarantine list is loaded ONCE and then only when you click into the
   * tab, so its length went stale the moment anything downloaded. the number at
   * the top only moved when you happened to open the area. this is what the
   * badge reads instead.
   */
  summary: AdlReviewSummary | null;
  /** the .deleted quarantine - loaded when the Deleted pill is opened. */
  deleted: AdlDeletedList | null;
  deletedLoaded: boolean;
  openUnverified: ReadonlySet<string>;
  openQuarantine: ReadonlySet<string>;
  openGroups: ReadonlySet<string>;
}

export interface AdlVerificationController {
  state: AdlVerificationState;
  /** Resolved for rendering — null counts as enabled. */
  acoustidEnabled: boolean;
  setSubView: (view: AdlSubView) => void;
  loadQuarantine: (force?: boolean) => Promise<void>;
  loadMoreQuarantine: () => Promise<void>;
  loadAllQuarantine: () => Promise<{ entries: AdlQuarantineEntry[]; complete: boolean }>;
  loadDeleted: (force?: boolean) => Promise<void>;
  refreshSummary: () => Promise<void>;
  toggleUnverified: (key: string) => void;
  toggleQuarantine: (id: string) => void;
  toggleGroup: (key: string) => void;
}

/** how often the review counts refresh. */
export const REVIEW_SUMMARY_POLL_MS = 15000;

/**
 * The review queue behind the ⚠ pill: config, quarantine data and sub-view.
 *
 * Expanded-row state lives in Sets keyed by a STABLE id rather than in the DOM,
 * because the page re-renders every 2 seconds from the downloads poll — the
 * vanilla learned this the hard way and its comments say so. A DOM-only toggle
 * collapses again a moment after you click it.
 */
export function useAdlVerification(): AdlVerificationController {
  const [state, setState] = useState<AdlVerificationState>({
    acoustidEnabled: null,
    subView: 'unverified',
    quarantine: [],
    quarantineTotal: null,
    quarantineOffset: 0,
    quarantineLoaded: false,
    quarantineError: null,
    summary: null,
    deleted: null,
    deletedLoaded: false,
    openUnverified: new Set<string>(),
    openQuarantine: new Set<string>(),
    openGroups: new Set<string>(),
  });

  /**
   * Refs, not state, because both flags are read SYNCHRONOUSLY at the top of
   * loadQuarantine. The vanilla's guard was a plain module variable
   * (`if (_verifQuarLoading || (_verifQuarLoaded && !force)) return;`), and a
   * ref is its exact React equivalent. Reading `quarantineLoaded` back out of
   * state instead does not work: a no-op `setState` updater runs AFTER the
   * synchronous code that follows it, so the guard would always see its
   * initial value and never fire. `quarantineLoaded` still lives in state as
   * well, because the UI renders "Loading quarantine…" from it.
   */
  const loadingRef = useRef(false);
  const loadedRef = useRef(false);
  const deletedLoadingRef = useRef(false);
  const deletedLoadedRef = useRef(false);
  /**
   * Synchronous mirrors of the loaded quarantine page(s) and the server
   * total, for the same reason as the flags above: loadMore/loadAll read
   * the current length and total at call time, and state would still hold
   * the previous render's values. Updated wherever quarantine state is set.
   */
  const quarantineRef = useRef<AdlQuarantineEntry[]>([]);
  const quarantineTotalRef = useRef<number | null>(null);
  /**
   * Raw server offset consumed so far, tracked separately from the deduped
   * list length: a page of duplicates must still advance the cursor, or
   * loadMore/loadAll would refetch the same window forever.
   */
  const quarantineOffsetRef = useRef(0);

  /**
   * Set every render, read only when loadQuarantine runs. Lets the two call
   * each other without becoming each other's dependency.
   */
  const refreshSummaryRef = useRef<(() => Promise<void>) | null>(null);

  const loadQuarantine = useCallback(async (force = false) => {
    // One in flight at a time, and skip entirely unless forced once loaded —
    // this hits a filesystem scan server-side.
    if (loadingRef.current || (loadedRef.current && !force)) return;

    loadingRef.current = true;
    const result = await fetchQuarantine({ limit: QUARANTINE_PAGE_SIZE, offset: 0 });
    loadedRef.current = true;
    loadingRef.current = false;
    if ('entries' in result) {
      quarantineRef.current = result.entries;
      quarantineTotalRef.current = result.total;
      quarantineOffsetRef.current = result.entries.length;
    }
    // a failed fetch keeps the last good list and carries the reason - an
    // empty render with a big badge number must say WHY, not look empty
    setState((prev) =>
      'entries' in result
        ? {
            ...prev,
            quarantine: result.entries,
            quarantineTotal: result.total,
            quarantineOffset: quarantineOffsetRef.current,
            quarantineLoaded: true,
            quarantineError: null,
          }
        : { ...prev, quarantineLoaded: true, quarantineError: result.error },
    );
    void refreshSummaryRef.current?.();
  }, []);

  const loadMoreQuarantine = useCallback(async () => {
    if (loadingRef.current) return;
    const total = quarantineTotalRef.current;
    if (quarantineOffsetRef.current >= (total ?? 0)) return;

    loadingRef.current = true;
    const result = await fetchQuarantine({
      limit: QUARANTINE_PAGE_SIZE,
      offset: quarantineOffsetRef.current,
    });
    loadingRef.current = false;
    if ('entries' in result) {
      quarantineOffsetRef.current += result.entries.length;
      const seen = new Set(quarantineRef.current.map((e) => e.id));
      const appended = result.entries.filter((e) => !seen.has(e.id));
      const next = [...quarantineRef.current, ...appended];
      quarantineRef.current = next;
      quarantineTotalRef.current = result.total;
      loadedRef.current = true;
      setState((prev) => ({
        ...prev,
        quarantine: next,
        quarantineTotal: result.total,
        quarantineOffset: quarantineOffsetRef.current,
        quarantineLoaded: true,
        quarantineError: null,
      }));
    } else {
      setState((prev) => ({ ...prev, quarantineError: result.error }));
    }
  }, []);

  const loadAllQuarantine = useCallback(async (): Promise<{
    entries: AdlQuarantineEntry[];
    complete: boolean;
  }> => {
    if (loadingRef.current) return { entries: quarantineRef.current, complete: false };
    // Accumulate locally and publish ONCE at the end: per-page setState
    // re-mounted the growing full list on every page and re-froze the tab.
    let entries = [...quarantineRef.current];
    let expectedTotal = quarantineTotalRef.current;
    let lastTotal = quarantineTotalRef.current;
    let nextOffset = quarantineOffsetRef.current;
    let loaded = loadedRef.current;
    let fetchError: string | null = null;
    let drifted = false;
    // Page through until we hold `expectedTotal` entries. Start from what's
    // loaded; when nothing is loaded yet the first fetch is offset 0.
    while (expectedTotal === null || entries.length < expectedTotal) {
      loadingRef.current = true;
      const result = await fetchQuarantine({
        limit: QUARANTINE_PAGE_SIZE,
        offset: nextOffset,
      });
      loadingRef.current = false;
      if (!('entries' in result)) {
        fetchError = result.error;
        break;
      }
      const reportedTotal = result.total;
      nextOffset += result.entries.length;
      const seen = new Set(entries.map((e) => e.id));
      entries = [...entries, ...result.entries.filter((e) => !seen.has(e.id))];
      loaded = true;
      if (expectedTotal === null) {
        expectedTotal = reportedTotal;
        lastTotal = reportedTotal;
      } else if (reportedTotal !== expectedTotal) {
        // The queue changed mid-load: a count match against the new total
        // would over-claim coverage of a list we never fully saw.
        lastTotal = reportedTotal;
        drifted = true;
        break;
      } else {
        lastTotal = reportedTotal;
      }
      if (result.entries.length === 0) break;
      // A page of pure duplicates (offsets shifted) advances nextOffset but
      // not entries; the loop still terminates because nextOffset grows and
      // the server returns an empty page past the end — guard anyway.
      if (expectedTotal !== null && nextOffset > expectedTotal && entries.length < expectedTotal)
        break;
    }
    // Refs and the single published state must agree when this returns.
    quarantineRef.current = entries;
    quarantineTotalRef.current = lastTotal;
    quarantineOffsetRef.current = nextOffset;
    loadedRef.current = loaded;
    const complete =
      !fetchError && !drifted && expectedTotal !== null && entries.length >= expectedTotal;
    setState((prev) => ({
      ...prev,
      quarantine: entries,
      quarantineTotal: lastTotal,
      quarantineOffset: nextOffset,
      quarantineLoaded: loaded,
      quarantineError: fetchError,
    }));
    return { entries, complete };
  }, []);

  const loadDeleted = useCallback(async (force = false) => {
    // same shape as loadQuarantine - one in flight, cached until forced,
    // because the server side walks a folder tree.
    if (deletedLoadingRef.current || (deletedLoadedRef.current && !force)) return;

    deletedLoadingRef.current = true;
    const list = await fetchDeletedFiles();
    deletedLoadedRef.current = true;
    deletedLoadingRef.current = false;
    // null means the fetch failed - keep whatever was shown rather than
    // flashing an empty bin at someone.
    setState((prev) => ({
      ...prev,
      deleted: list ?? prev.deleted,
      deletedLoaded: true,
    }));
  }, []);

  /**
   * Pull the counts. Kept in a ref as well so `loadQuarantine` can call it
   * without the two of them depending on each other. approving five files should
   * move the badge immediately, not on the next tick.
   */
  const refreshSummary = useCallback(async () => {
    const next = await fetchReviewQueueSummary();
    // null means the fetch failed. keep the last known counts rather than
    // flashing zero at someone who has 72 files waiting.
    if (next) setState((prev) => ({ ...prev, summary: next }));
  }, []);
  refreshSummaryRef.current = refreshSummary;

  /**
   * REVIEW_SUMMARY_POLL_MS, not the downloads poll's 2s. Nothing here changes
   * fast and it touches the filesystem, so a slower beat is plenty.
   */
  useEffect(() => {
    void refreshSummary();
    const timer = setInterval(() => void refreshSummary(), REVIEW_SUMMARY_POLL_MS);
    return () => clearInterval(timer);
  }, [refreshSummary]);

  /**
   * Read the config once and decide whether the unverified queue can exist.
   *
   * TWO ways it cannot, and the second is easy to miss: AcoustID being off, and
   * `require_verified` being ON — in that mode unconfirmed tracks are
   * quarantined instead of imported unverified, so the queue is always empty.
   * Either way the sub-view collapses to quarantine.
   */
  useEffect(() => {
    let live = true;
    void (async () => {
      const config = await fetchVerificationConfig();
      if (!live) return;
      const enabled = Boolean(config.acoustid_enabled) && !config.require_verified;
      setState((prev) => ({
        ...prev,
        acoustidEnabled: enabled,
        subView: enabled ? prev.subView : 'quarantine',
      }));
      if (!enabled) void loadQuarantine(true);
    })();
    return () => {
      live = false;
    };
  }, [loadQuarantine]);

  const setSubView = useCallback(
    (view: AdlSubView) => {
      setState((prev) => {
        // With no unverified queue possible, quarantine stands in for it -
        // but the deleted view exists either way.
        const next = prev.acoustidEnabled === false && view === 'unverified' ? 'quarantine' : view;
        return { ...prev, subView: next };
      });
      if (view === 'quarantine') void loadQuarantine(true);
      if (view === 'deleted') void loadDeleted(true);
    },
    [loadQuarantine, loadDeleted],
  );

  const toggleIn = useCallback(
    (field: 'openUnverified' | 'openQuarantine' | 'openGroups', key: string) => {
      setState((prev) => {
        const next = new Set(prev[field]);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return { ...prev, [field]: next };
      });
    },
    [],
  );

  const toggleUnverified = useCallback(
    (key: string) => toggleIn('openUnverified', key),
    [toggleIn],
  );
  const toggleQuarantine = useCallback((id: string) => toggleIn('openQuarantine', id), [toggleIn]);
  const toggleGroup = useCallback((key: string) => toggleIn('openGroups', key), [toggleIn]);

  return {
    state,
    acoustidEnabled: state.acoustidEnabled !== false,
    setSubView,
    loadQuarantine,
    loadMoreQuarantine,
    loadAllQuarantine,
    loadDeleted,
    refreshSummary,
    toggleUnverified,
    toggleQuarantine,
    toggleGroup,
  };
}

export interface QuarantineGroup {
  key: string | null;
  members: AdlQuarantineEntry[];
}

/**
 * Group alternative candidates for the same track.
 *
 * The grouping rule lives in wishlist-tools.js (`_groupQuarantineEntries`) and
 * is shared with the library-history quarantine tab, so it is CALLED rather
 * than reimplemented — two copies of "are these the same track" would drift.
 * Without it every candidate is its own group, which is the honest fallback.
 */
export function groupQuarantine(entries: AdlQuarantineEntry[]): QuarantineGroup[] {
  const grouper = window._groupQuarantineEntries;
  if (typeof grouper === 'function') {
    return grouper(entries) as QuarantineGroup[];
  }
  return entries.map((entry) => ({ key: null, members: [entry] }));
}
