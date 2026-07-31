import { useCallback, useEffect, useRef, useState } from 'react';

import type { AdlQuarantineEntry, AdlSubView } from './-adl.types';

import { fetchQuarantine, fetchVerificationConfig } from './-adl.api';

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
  toggleUnverified: (key: string) => void;
  toggleQuarantine: (id: string) => void;
  toggleGroup: (key: string) => void;
}

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
    openUnverified: new Set<string>(),
    openQuarantine: new Set<string>(),
    openGroups: new Set<string>(),
  });

  const loadingRef = useRef(false);

  const loadQuarantine = useCallback(async (force = false) => {
    // One in flight at a time, and skip entirely unless forced once loaded —
    // this hits a filesystem scan server-side.
    if (loadingRef.current) return;
    let shouldRun = true;
    setState((prev) => {
      if (prev.quarantineLoaded && !force) shouldRun = false;
      return prev;
    });
    if (!shouldRun) return;

    loadingRef.current = true;
    const entries = await fetchQuarantine();
    loadingRef.current = false;
    setState((prev) => ({ ...prev, quarantine: entries, quarantineLoaded: true }));
  }, []);

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
