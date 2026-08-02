import { useCallback, useSyncExternalStore } from 'react';

import type { DiscoverDownload, DownloadState } from './-discover.download-bar';

import {
  addDownload,
  AUTO_REMOVE_MS,
  HYDRATE_ENDPOINT,
  hydrateState,
  markCompleted,
  MONITOR_INTERVAL_MS,
  nextNotFoundCount,
  planAfterRehydrate,
  planOpenModal,
  publishDownloadGlobals,
  removeDownload,
  restStatusIsTerminal,
  shouldAutoRemove,
  shouldGiveUp,
  SNAPSHOT_DEBOUNCE_MS,
  SNAPSHOT_ENDPOINT,
  snapshotPayload,
  syncDownloadState,
} from './-discover.download-bar';

/**
 * The discover download bar's STORE — module-scoped, not hook-scoped.
 *
 * Transcribed from addDiscoverDownload + monitorDiscoverDownload +
 * removeDiscoverDownload (discover.js 11579-11718), the snapshot system
 * (12190-12310) and the bubble-open flow (11793-11845), over the module.
 *
 * Why a module store and not useState: the window contract. wishlist-tools.js
 * reads `discoverDownloads` unguarded while rendering the dashboard, and
 * init.js calls `hydrateDiscoverDownloadsFromSnapshot` at app start — both on
 * pages where no discover component is mounted. So the store lives at module
 * scope, publishes its globals AT MODULE LOAD (the module contract's own
 * words), and the hook is just a `useSyncExternalStore` view over it.
 *
 * Load order makes the ownership work: module scripts execute after the
 * classic static scripts, so these assignments REPLACE the vanilla's global
 * function declarations, and every cross-file caller — bare
 * `addDiscoverDownload(...)` included — resolves to this store.
 *
 * The monitor keeps the REST poll as the guaranteed path (the vanilla's
 * WebSocket subscription is an enhancement gated on socketConnected, same
 * decision as usePlaylistSync). Its order is the vanilla's: an active modal
 * PROCESS short-circuits the poll entirely; otherwise `/api/sync/status/<id>`
 * decides — terminal completes with the 30s auto-remove; five consecutive
 * 404s means the sync never started, and the entry is REMOVED.
 */

let state: DownloadState = {};
const listeners = new Set<() => void>();
const monitors: Record<string, ReturnType<typeof setInterval>> = {};
let snapshotTimer: ReturnType<typeof setTimeout> | null = null;

function emit() {
  syncDownloadState(window as unknown as Record<string, unknown>, state);
  for (const l of listeners) l();
  scheduleSnapshot();
}

function scheduleSnapshot() {
  if (snapshotTimer) clearTimeout(snapshotTimer);
  snapshotTimer = setTimeout(() => {
    const payload = snapshotPayload(state);
    // An empty state is never written (12208); the server absorbs staleness.
    if (!payload) return;
    void fetch(SNAPSHOT_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ downloads: payload }),
    }).catch(() => {});
  }, SNAPSHOT_DEBOUNCE_MS);
}

function stopMonitor(playlistId: string) {
  if (monitors[playlistId]) {
    clearInterval(monitors[playlistId]);
    delete monitors[playlistId];
  }
}

function complete(playlistId: string) {
  state = markCompleted(state, playlistId);
  emit();
  setTimeout(() => {
    if (shouldAutoRemove(state, playlistId)) remove(playlistId);
  }, AUTO_REMOVE_MS);
}

function startMonitor(playlistId: string) {
  stopMonitor(playlistId);
  let notFound = 0;
  monitors[playlistId] = setInterval(() => {
    void (async () => {
      if (!state[playlistId]) {
        stopMonitor(playlistId);
        return;
      }
      // A modal-based download's process outranks the sync poll (11648-11667).
      const process = window.discoverDownloadProcess?.(playlistId);
      if (process) {
        if (process.status === 'complete') {
          stopMonitor(playlistId);
          complete(playlistId);
        }
        return;
      }
      try {
        const res = await fetch(`/api/sync/status/${playlistId}`);
        if (res.ok) {
          notFound = nextNotFoundCount(notFound, true, res.status);
          const data = (await res.json()) as { status?: string };
          if (restStatusIsTerminal(data.status)) {
            stopMonitor(playlistId);
            complete(playlistId);
          }
        } else {
          notFound = nextNotFoundCount(notFound, false, res.status);
          if (shouldGiveUp(notFound)) {
            // The sync never started — the entry goes, quietly (11696-11700).
            stopMonitor(playlistId);
            remove(playlistId);
          }
        }
      } catch {
        /* a missed poll is the next poll's problem (11702) */
      }
    })();
  }, MONITOR_INTERVAL_MS);
}

function add(
  playlistId: string,
  playlistName: string,
  playlistType: string,
  imageUrl: string | null = null,
) {
  state = addDownload(state, { playlistId, playlistName, playlistType, imageUrl });
  emit();
  startMonitor(playlistId);
}

function remove(playlistId: string) {
  stopMonitor(playlistId);
  state = removeDownload(state, playlistId);
  emit();
}

async function hydrate(): Promise<void> {
  try {
    const res = await fetch(HYDRATE_ENDPOINT);
    const data = (await res.json()) as {
      success?: boolean;
      downloads?: Record<string, never>;
    };
    if (!data.success) return;
    const { state: next, toMonitor } = hydrateState(data.downloads);
    if (Object.keys(next).length === 0) return;
    state = next;
    emit();
    // Only in-progress entries get a monitor restarted (12295).
    for (const id of toMonitor) startMonitor(id);
  } catch {
    /* app still works without yesterday's downloads */
  }
}

// AT MODULE LOAD — wishlist-tools reads these unguarded (module contract).
publishDownloadGlobals(window as unknown as Record<string, unknown>, {
  discoverDownloads: state,
  addDiscoverDownload: add,
  removeDiscoverDownload: remove,
  updateDiscoverDownloadBar: () => emit(),
  hydrateDiscoverDownloadsFromSnapshot: hydrate,
});

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Exposed for tests ONLY — resets module state between cases. */
export function resetDownloadStoreForTests(): void {
  for (const id of Object.keys(monitors)) stopMonitor(id);
  if (snapshotTimer) clearTimeout(snapshotTimer);
  snapshotTimer = null;
  state = {};
  syncDownloadState(window as unknown as Record<string, unknown>, state);
}

export interface DownloadBarController {
  state: DownloadState;
  add: typeof add;
  remove: typeof remove;
  hydrate: typeof hydrate;
  /** The bubble click (11793-11845): returns the toast to show, if any. */
  openBubble: (playlistId: string) => Promise<{ toast: string } | null>;
}

export function useDownloadBar(): DownloadBarController {
  const snapshot = useSyncExternalStore(subscribe, () => state);

  const openBubble = useCallback(async (playlistId: string) => {
    const process = window.discoverDownloadProcess?.(playlistId) ?? undefined;
    const plan = planOpenModal(process as never, (id) => document.getElementById(id) !== null);
    if (plan.via === 'element') {
      window.reopenActiveDownloadModal?.(playlistId);
      return null;
    }
    if (plan.via === 'id') {
      const el = document.getElementById(plan.modalId);
      if (el) (el as HTMLElement).style.display = 'flex';
      return null;
    }
    // Rehydrate, then re-plan (planAfterRehydrate).
    const rehydrated = (await window.rehydrateDiscoverDownloadModal?.(playlistId)) ?? false;
    const after = planAfterRehydrate(
      rehydrated,
      (window.discoverDownloadProcess?.(playlistId) ?? undefined) as never,
      state[playlistId] as DiscoverDownload | undefined,
    );
    if (after.via === 'element') {
      window.reopenActiveDownloadModal?.(playlistId);
      return null;
    }
    if (after.via === 'toast') return { toast: after.message };
    return null;
  }, []);

  return { state: snapshot, add, remove, hydrate, openBubble };
}
