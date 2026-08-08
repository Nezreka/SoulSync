/**
 * Drives the sequential-sync machine against the download engine —
 * `SequentialSyncManager.syncNext/waitForSyncCompletion` (core.js 1254-1305)
 * and `startSequentialSync` (downloads.js 4059-4099).
 *
 * THE ENGINE STAYS VANILLA. `startPlaylistSync`, the `activeSyncPollers` map
 * and `disablePlaylistSelection` all live in downloads.js, which survives the
 * flip. They arrive as a REQUIRED injected object rather than window lookups,
 * for the same reason `runPipeline` does on useAutoSync: a window lookup that
 * silently resolves to undefined is a dead button, and the type system can
 * make that impossible instead.
 *
 * THE STATE IS MODULE-SCOPED, not component state, because
 * `sequentialSyncManager` is a module singleton (core.js 409) and a run
 * OUTLIVES THE PAGE. Leaving /sync mid-sync and coming back finds the vanilla
 * still going. Per-component state would come back reading idle while the
 * engine was still working, and the runner would be left holding refs to an
 * unmounted tree. The store below is the singleton's counterpart; the hook is
 * just a subscription to it.
 *
 * The loop is a plain async function, not an effect. Effects re-run on
 * dependency changes and die with the component; this run has to survive both.
 */

import { useCallback, useSyncExternalStore } from 'react';

import type { SyncActionsState } from './-sync.sidebar';

import {
  SEQUENTIAL_CANCELLED,
  SEQUENTIAL_IDLE,
  SEQUENTIAL_NONE_SELECTED,
  type SequentialSyncState,
  sequentialActionsState,
  sequentialCompleteToast,
  sequentialDurationSeconds,
  sequentialFailureToast,
  sequentialStart,
  syncOrderedSelection,
  syncSelectionLocked,
} from './-sync.sequential';

export interface SequentialSyncEngine {
  /** startPlaylistSync (downloads.js 3840). Resolves once the sync is KICKED
   *  OFF, not once it finishes — completion is observed via isSyncing. */
  startPlaylistSync: (playlistId: string) => Promise<void>;
  /**
   * Whether the engine still holds a poller for this playlist —
   * `!!activeSyncPollers[id]`. The vanilla watches the poller's disappearance
   * rather than any completion event, because `stopSyncPolling` is the single
   * place every terminal path funnels through.
   */
  isSyncing: (playlistId: string) => boolean;
  /** disablePlaylistSelection (4101-4106). */
  setSelectionDisabled: (disabled: boolean) => void;
  /** updateRefreshButtonState (4119) — called after complete AND cancel. */
  refreshButtons: () => void;
  toast: (message: string, kind: 'success' | 'error' | 'info' | 'warning') => void;
}

/* ── The singleton's counterpart ──────────────────────────────────────────── */

let storeState: SequentialSyncState = SEQUENTIAL_IDLE;
const listeners = new Set<() => void>();

/**
 * Raised by cancel, checked at every await boundary in the runner.
 *
 * THE VANILLA NEEDED A GUARD HERE TOO, and lacked one until this port found
 * it: `cancel()` reset the manager while the `setTimeout(syncNext, 1000)` from
 * the previous iteration was still queued, so the callback read `0 >= 0`,
 * called `complete()`, and announced a success for zero playlists — with a
 * duration measured against a `startTime` cancel had already nulled. Fixed in
 * core.js; here it simply cannot happen, because the runner stops at the flag.
 */
let cancelled = false;
let running = false;

function emit(next: SequentialSyncState) {
  storeState = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Stable identity while unchanged — useSyncExternalStore requires it. */
function getSnapshot() {
  return storeState;
}

/**
 * TEST ONLY. Module state persists across renders by design, which also means
 * it persists across tests; each one starts from a clean run.
 */
export function resetSequentialSyncStore(): void {
  storeState = SEQUENTIAL_IDLE;
  cancelled = false;
  running = false;
  listeners.clear();
}

export interface SequentialSyncDeps {
  engine: SequentialSyncEngine;
  nameFor: (playlistId: string) => string | null | undefined;
  now: () => number;
  wait: (ms: number) => Promise<void>;
}

function finish(deps: SequentialSyncDeps, announce: () => void) {
  emit(SEQUENTIAL_IDLE);
  running = false;
  deps.engine.setSelectionDisabled(false);
  announce();
  deps.engine.refreshButtons();
}

async function runQueue(deps: SequentialSyncDeps, queue: readonly string[], startedAt: number) {
  const { engine, nameFor, now, wait } = deps;

  for (let index = 0; index < queue.length; index += 1) {
    if (cancelled) break;
    const playlistId = queue[index];
    if (storeState.running) emit({ ...storeState, currentIndex: index });

    try {
      await engine.startPlaylistSync(playlistId);
      // waitForSyncCompletion (1283-1297): the poller vanishing IS the
      // completion signal. Re-checked against the cancel flag so a cancel
      // during a long sync does not sit here until the engine finishes.
      while (engine.isSyncing(playlistId) && !cancelled) {
        await wait(1000);
      }
    } catch (error) {
      // 1273-1276: a failure is announced and the run CONTINUES to the next
      // playlist. One bad playlist must not strand the queue.
      engine.toast(
        sequentialFailureToast(
          nameFor(playlistId),
          playlistId,
          error instanceof Error ? error.message : String(error),
        ),
        'error',
      );
    }

    if (cancelled) break;
    // 1280 — the deliberate gap between syncs.
    await wait(1000);
  }

  if (cancelled) {
    finish(deps, () => {
      engine.toast(SEQUENTIAL_CANCELLED, 'info');
    });
    return;
  }
  const duration = sequentialDurationSeconds({ ...SEQUENTIAL_IDLE, startedAt }, now());
  finish(deps, () => {
    engine.toast(sequentialCompleteToast(queue.length, duration), 'success');
  });
}

/**
 * The Start Sync button's ONE action, matching the vanilla toggle: starts when
 * idle, cancels when running. Module-level so a cancel works from any mount.
 */
export function toggleSequentialSync(
  deps: SequentialSyncDeps,
  order: readonly string[],
  selected: ReadonlySet<string>,
): void {
  if (running) {
    // The cancel half. The runner sees the flag at its next boundary and
    // announces; doing it here too would toast twice.
    cancelled = true;
    return;
  }

  const queue = syncOrderedSelection(order, selected);
  if (queue.length === 0) {
    // 4073-4076 — refuses, with a toast, before touching the manager.
    deps.engine.toast(SEQUENTIAL_NONE_SELECTED, 'error');
    return;
  }

  const startedAt = deps.now();
  const next = sequentialStart(storeState, queue, startedAt);
  if (next === storeState) return; // refused; nothing to do
  cancelled = false;
  running = true;
  emit(next);
  deps.engine.setSelectionDisabled(true);
  void runQueue(deps, queue, startedAt);
}

export interface UseSequentialSyncOptions {
  engine: SequentialSyncEngine;
  /**
   * How many playlists are selected right now. The selection store lives on
   * the page (the Spotify tab reads it too), so it arrives as a value —
   * without it `actions` would always report zero and the Start Sync button
   * would be permanently disabled.
   */
  selectedCount: number;
  /** Resolves the display name for the label. See sequentialActionsState. */
  nameFor: (playlistId: string) => string | null | undefined;
  /** Injected for tests. Defaults to the wall clock. */
  now?: () => number;
  /** Injected for tests. The 1s gap between syncs (1280) and the 1s completion
   *  poll (1293) are the vanilla's cadences. */
  wait?: (ms: number) => Promise<void>;
}

const realWait = (ms: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });

export interface UseSequentialSync {
  state: SequentialSyncState;
  /** What the sidebar renders. */
  actions: SyncActionsState;
  /** Whether playlist selection is frozen for the duration of the run. */
  locked: boolean;
  /** Start when idle, cancel when running — the vanilla's one toggle. */
  toggle: (order: readonly string[], selected: ReadonlySet<string>) => void;
}

export function useSequentialSync({
  engine,
  selectedCount,
  nameFor,
  now = Date.now,
  wait = realWait,
}: UseSequentialSyncOptions): UseSequentialSync {
  // Subscribing rather than owning: a run started by an earlier mount is still
  // the same run, and this is how a remount picks it back up mid-flight.
  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const toggle = useCallback(
    (order: readonly string[], selected: ReadonlySet<string>) => {
      toggleSequentialSync({ engine, nameFor, now, wait }, order, selected);
    },
    [engine, nameFor, now, wait],
  );

  return {
    state,
    actions: sequentialActionsState(state, selectedCount, nameFor),
    locked: syncSelectionLocked(state),
    toggle,
  };
}
