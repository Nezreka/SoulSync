/**
 * The video downloads controller — the React port of
 * webui/static/video/video-downloads-page.js's poll + actions.
 *
 * Cadence is the old page's: 2s while anything is active, 6s when idle,
 * paused while the tab is hidden. A failed poll keeps the old list on
 * screen (never blanks). The effect cleanup stops the poller on unmount —
 * the vanilla's `_onPage()` visibility check is replaced by mount lifetime.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { VdlDownload, VdlFilter, VdlMeta, VdlView, VdlYtMeta } from './-vdl.types';

import {
  blockVideoRelease,
  cancelVideoDownload,
  clearFailedVideoDownloads,
  clearVideoDownloads,
  fetchVideoDownloads,
  fetchVideoMeta,
  fetchVideoYtMeta,
  removeVideoDownload,
  retryVideoDownload,
  stopVideoWishlistSearches,
} from './-vdl.api';
import {
  vdlIsActive,
  vdlIsFail,
  vdlIsRemovable,
  vdlIsRetryable,
  vdlMatchesFilter,
  vdlMetaURL,
  vdlRowType,
} from './-vdl.helpers';
import { VDL_POLL_ACTIVE_MS, VDL_POLL_IDLE_MS } from './-vdl.types';

const toast = (message: string, type: string) => window.showToast?.(message, type);

/**
 * Module-level video view state — survives Music|Video switches and
 * unmount/remount, restoring parity with the old page's module-scope
 * _filter / _expanded / _gcollapse / _sub. Written through on every setter.
 */
const vdlViewStore = {
  view: 'downloads' as VdlView,
  filter: 'all' as VdlFilter,
  expanded: new Set<number>(),
  collapsedGroups: new Set<string>(),
};

/** The video sidebar's live count. Hidden at 0, "99+" capped — the old rule. */
export function updateVideoDownloadsBadge(n: number): void {
  const badge = document.querySelector('[data-video-downloads-badge]');
  if (!badge) return;
  if (n > 0) {
    badge.textContent = n > 99 ? '99+' : String(n);
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
}

function okOf(res: { ok?: boolean; success?: boolean } | null): boolean {
  return Boolean(res && (res.ok ?? res.success));
}

export interface VdlDownloadsController {
  downloads: VdlDownload[];
  /** False until the first fetch settles, so the empty state does not flash. */
  loaded: boolean;
  view: VdlView;
  setView: (view: VdlView) => void;
  filter: VdlFilter;
  setFilter: (filter: VdlFilter) => void;
  /** Rows after the status filter, in server order. */
  visible: VdlDownload[];
  /** `removable`: failed + cancelled rows (import_failed excluded). */
  counts: { active: number; completed: number; failed: number; removable: number; total: number };
  hasRunningWork: boolean;
  /** Failed rows that can actually be re-grabbed (≥2 shows "Retry Failed"). */
  retryableCount: number;
  /** Combined live speed of downloading rows, bytes/sec. */
  liveSpeedBps: number;
  /** Cards with an open detail drawer — survives re-patches. */
  expanded: ReadonlySet<number>;
  toggleExpanded: (id: number) => void;
  /** Grouped season-pack cards currently collapsed. */
  collapsedGroups: ReadonlySet<string>;
  toggleGroup: (key: string) => void;
  /** Lazy TMDB/YouTube detail for an expanded card. Pure read — no fetch. */
  metaFor: (d: VdlDownload) => VdlMeta | VdlYtMeta | null | undefined;
  /** Start the lazy detail fetch for an expanded card (no-op when cached). */
  ensureMeta: (d: VdlDownload) => void;
  refresh: () => Promise<void>;
  cancelOne: (id: number) => Promise<void>;
  retryOne: (id: number) => Promise<void>;
  blockOne: (d: VdlDownload) => Promise<void>;
  removeOne: (id: number) => Promise<void>;
  cancelAll: () => Promise<void>;
  retryAllFailed: () => Promise<void>;
  clearFinished: () => Promise<void>;
  clearFailed: () => Promise<void>;
  retryAllPending: boolean;
}

/**
 * Keeps the video sidebar badge live while the app is up but the unified
 * page's video view is NOT mounted (its own 2s poll owns the badge then).
 * The old page ran this as an 8s background poll on the video side.
 */
export function VideoDownloadsBadgePoller(): null {
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    const pollOnce = async () => {
      const onVideo = document.body.getAttribute('data-side') === 'video';
      const viewMounted = document.querySelector('[data-vdl-root]') !== null;
      if (!onVideo || viewMounted || document.hidden) return;
      const data = await fetchVideoDownloads();
      if (!alive) return;
      updateVideoDownloadsBadge((data.downloads ?? []).filter((d) => vdlIsActive(d.status)).length);
    };
    const tick = async () => {
      if (!alive) return;
      await pollOnce();
      if (!alive) return;
      timer = setTimeout(() => void tick(), 8000);
    };
    /* the old page re-ran the badge poll shortly after a wishlist change */
    const onWishlistChanged = () => {
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = setTimeout(() => void pollOnce(), 200);
    };
    document.addEventListener('soulsync:video-wishlist-changed', onWishlistChanged);
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      if (retryTimer) clearTimeout(retryTimer);
      document.removeEventListener('soulsync:video-wishlist-changed', onWishlistChanged);
    };
  }, []);
  return null;
}

export function useVideoDownloads(): VdlDownloadsController {
  const [downloads, setDownloads] = useState<VdlDownload[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [view, setViewState] = useState<VdlView>(vdlViewStore.view);
  const [filter, setFilterState] = useState<VdlFilter>(vdlViewStore.filter);
  const [expanded, setExpandedState] = useState<ReadonlySet<number>>(vdlViewStore.expanded);
  const [collapsedGroups, setCollapsedGroupsState] = useState<ReadonlySet<string>>(
    vdlViewStore.collapsedGroups,
  );
  const [retryAllPending, setRetryAllPending] = useState(false);
  /** id → TMDB/YT detail. null = fetch in flight; undefined = not started. */
  const [meta, setMeta] = useState<Record<number, VdlMeta | VdlYtMeta | null>>({});

  /* setters write through to the module store so view state survives unmount */
  const setView = useCallback((v: VdlView) => {
    vdlViewStore.view = v;
    setViewState(v);
  }, []);
  const setFilter = useCallback((f: VdlFilter) => {
    vdlViewStore.filter = f;
    setFilterState(f);
  }, []);

  const downloadsRef = useRef(downloads);
  downloadsRef.current = downloads;

  const refresh = useCallback(async () => {
    const data = await fetchVideoDownloads();
    if (data.downloads) {
      setDownloads(data.downloads);
      updateVideoDownloadsBadge(data.downloads.filter((d) => vdlIsActive(d.status)).length);
      // The old page never rendered the empty state on a failed fetch — a
      // failure leaves `downloads` absent, so keep the skeleton shimmer.
      setLoaded(true);
    }
  }, []);

  /* the poller: 2s while active, 6s idle, paused when hidden, dead on unmount */
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      if (!alive) return;
      if (!document.hidden) await refresh();
      if (!alive) return;
      const active = downloadsRef.current.some((d) => vdlIsActive(d.status));
      timer = setTimeout(() => void tick(), active ? VDL_POLL_ACTIVE_MS : VDL_POLL_IDLE_MS);
    };
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [refresh]);

  /* a fresh grab (from a detail page's get-modal) lands within a tick */
  useEffect(() => {
    const onStarted = () => {
      window.setTimeout(() => void refresh(), 350);
    };
    document.addEventListener('soulsync:video-download-started', onStarted);
    return () => document.removeEventListener('soulsync:video-download-started', onStarted);
  }, [refresh]);

  /* the wishlist page's badge poll asks this — kept as the old global */
  useEffect(() => {
    const anyActive = () => downloadsRef.current.some((d) => vdlIsActive(d.status));
    window._vdpgAnyActive = anyActive;
    return () => {
      // Never delete on unmount: the poller would fall back to its idle 30s
      // cadence mid-download whenever the video view isn't mounted. Leave a
      // session-level last-known value instead.
      if (window._vdpgAnyActive === anyActive) {
        const lastKnown = anyActive();
        window._vdpgAnyActive = () => lastKnown;
      }
    };
  }, []);

  const toggleExpanded = useCallback((id: number) => {
    setExpandedState((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      vdlViewStore.expanded = next;
      return next;
    });
  }, []);

  const toggleGroup = useCallback((key: string) => {
    setCollapsedGroupsState((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      vdlViewStore.collapsedGroups = next;
      return next;
    });
  }, []);

  /**
   * The cached lazy detail for a card: undefined = not started, null = in
   * flight (drawer shows "Loading…"), an object = ready. Pure read — the
   * fetch is kicked off by ensureMeta (from an effect), never during render.
   */
  const metaFor = useCallback(
    (d: VdlDownload): VdlMeta | VdlYtMeta | null | undefined => meta[d.id],
    [meta],
  );

  const metaStartedRef = useRef<Record<number, boolean>>({});
  const ensureMeta = useCallback((d: VdlDownload) => {
    if (metaStartedRef.current[d.id] || !d.media_id) return;
    metaStartedRef.current[d.id] = true;
    const url = vdlMetaURL(d);
    if (!url) {
      // owned re-grab: media_id isn't a TMDB id — facts only, no fetch.
      setMeta((prev) => ({ ...prev, [d.id]: {} }));
      return;
    }
    setMeta((prev) => ({ ...prev, [d.id]: null }));
    const isYt = vdlRowType(d) === 'youtube';
    void (isYt ? fetchVideoYtMeta(d.media_id) : fetchVideoMeta(url)).then((m) => {
      setMeta((prev) => ({ ...prev, [d.id]: (m ?? {}) as VdlMeta | VdlYtMeta }));
    });
  }, []);

  const cancelOne = useCallback(
    async (id: number) => {
      await cancelVideoDownload(id);
      await refresh();
    },
    [refresh],
  );

  const retryOne = useCallback(
    async (id: number) => {
      const res = await retryVideoDownload(id);
      if (okOf(res)) toast('Retrying', 'info');
      else toast(res?.error || 'Retry failed', 'error');
      await refresh();
    },
    [refresh],
  );

  /**
   * Block this exact release. On a failed row the retry goes to a DIFFERENT
   * release (next:true — candidates → requery → wishlist search, never the
   * one just blocked); on import_failed the file itself may still be manually
   * importable, so there is no auto-retry.
   */
  const blockOne = useCallback(
    async (d: VdlDownload) => {
      const res = await blockVideoRelease(d.id);
      if (!okOf(res)) {
        toast(res?.error || 'Could not block that release', 'error');
        await refresh();
        return;
      }
      if (d.status === 'failed') {
        const r2 = await retryVideoDownload(d.id, true);
        const msg = !okOf(r2)
          ? 'Release blocked'
          : r2?.wishlist_search
            ? 'Release blocked — searching for another release'
            : 'Release blocked — retrying with another';
        toast(msg, 'success');
      } else {
        toast('Release blocked', 'success');
      }
      await refresh();
    },
    [refresh],
  );

  /** Take one failed/cancelled row off the list -- no retry, history kept. */
  const removeOne = useCallback(
    async (id: number) => {
      const res = await removeVideoDownload(id);
      if (okOf(res)) toast('Removed from the list', 'success');
      else toast(res?.error || 'Could not remove that download', 'error');
      await refresh();
    },
    [refresh],
  );

  const cancelAll = useCallback(async () => {
    const ids = downloadsRef.current.filter((d) => vdlIsActive(d.status)).map((d) => d.id);
    // stop the searches first, or a running search-all re-fills the list as we cancel it
    await stopVideoWishlistSearches();
    await Promise.all(ids.map((id) => cancelVideoDownload(id)));
    toast(
      `Cancelled ${ids.length} download${ids.length === 1 ? '' : 's'}, wishlist searches stopped`,
      'info',
    );
    await refresh();
  }, [refresh]);

  const retryAllFailed = useCallback(async () => {
    const ids = downloadsRef.current.filter(vdlIsRetryable).map((d) => d.id);
    setRetryAllPending(true);
    try {
      const results = await Promise.all(ids.map((id) => retryVideoDownload(id)));
      const n = results.filter(okOf).length;
      toast(
        n ? `Retrying ${n} download${n === 1 ? '' : 's'}` : 'Nothing could be retried',
        n ? 'info' : 'error',
      );
    } finally {
      setRetryAllPending(false);
    }
    await refresh();
  }, [refresh]);

  const clearFinished = useCallback(async () => {
    await clearVideoDownloads();
    toast('Cleared finished downloads', 'success');
    await refresh();
  }, [refresh]);

  const clearFailed = useCallback(async () => {
    const res = await clearFailedVideoDownloads();
    const n = Number(res?.cleared) || 0;
    if (res) toast(`Cleared ${n} failed download${n === 1 ? '' : 's'}`, 'success');
    else toast('Could not clear failed downloads', 'error');
    await refresh();
  }, [refresh]);

  const visible = downloads.filter((d) => vdlMatchesFilter(d.status, filter));

  const counts = {
    active: downloads.filter((d) => vdlIsActive(d.status)).length,
    completed: downloads.filter((d) => d.status === 'completed').length,
    failed: downloads.filter((d) => vdlIsFail(d.status)).length,
    removable: downloads.filter((d) => vdlIsRemovable(d.status)).length,
    total: downloads.length,
  };
  const hasRunningWork = counts.active > 0;
  const retryableCount = downloads.filter(vdlIsRetryable).length;
  const liveSpeedBps = downloads.reduce(
    (sum, d) => sum + (d.status === 'downloading' ? Number(d.speed_bps) || 0 : 0),
    0,
  );

  return {
    downloads,
    loaded,
    view,
    setView,
    filter,
    setFilter,
    visible,
    counts,
    hasRunningWork,
    retryableCount,
    liveSpeedBps,
    expanded,
    toggleExpanded,
    collapsedGroups,
    toggleGroup,
    metaFor,
    ensureMeta,
    refresh,
    cancelOne,
    retryOne,
    blockOne,
    removeOne,
    cancelAll,
    retryAllFailed,
    clearFinished,
    clearFailed,
    retryAllPending,
  };
}
