import { useCallback, useEffect, useRef, useState } from 'react';

import type { AdlQuarantineEntry, AdlReviewSummary, AdlSubView } from './-adl.types';

import { fetchQuarantine, fetchReviewQueueSummary, fetchVerificationConfig } from './-adl.api';

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
  quarantineLoaded: boolean;
  /**
   * server counts, polled. null until the first one lands.
   *
   * the quarantine list is loaded ONCE and then only when you click into the
   * tab, so its length went stale the moment anything downloaded. the number at
   * the top only moved when you happened to open the area. this is what the
   * badge reads instead.
   */
  summary: AdlReviewSummary | null;
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
    quarantineLoaded: false,
    summary: null,
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
    const entries = await fetchQuarantine();
    loadedRef.current = true;
    loadingRef.current = false;
    setState((prev) => ({ ...prev, quarantine: entries, quarantineLoaded: true }));
    void refreshSummaryRef.current?.();
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
        // With no unverified queue possible, quarantine is the only view.
        const next = prev.acoustidEnabled === false ? 'quarantine' : view;
        return { ...prev, subView: next };
      });
      if (view === 'quarantine') void loadQuarantine(true);
    },
    [loadQuarantine],
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
