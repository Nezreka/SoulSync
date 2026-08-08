/**
 * Drives the sequential-sync machine against the download engine —
 * `SequentialSyncManager.syncNext/waitForSyncCompletion` (core.js 1254-1297)
 * and `startSequentialSync` (downloads.js 4059-4099).
 *
 * THE ENGINE STAYS VANILLA. `startPlaylistSync`, the `activeSyncPollers` map
 * and `disablePlaylistSelection` all live in downloads.js, which survives the
 * flip. They arrive as a REQUIRED injected object rather than window lookups,
 * for the same reason `runPipeline` does on useAutoSync: a window lookup that
 * silently resolves to undefined is a dead button, and the type system can
 * make that impossible instead.
 *
 * The loop is an async runner held in a ref, not an effect. Effects re-run on
 * dependency changes; a sync run must survive every re-render the page makes
 * while it is going.
 */

import { useCallback, useRef, useState } from 'react';

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
  /**
   * The Start Sync button's ONE handler, matching the vanilla toggle: starts
   * when idle, cancels when running. `order` is the ids as the page lists
   * them — see syncOrderedSelection for why that is a parameter.
   */
  toggle: (order: readonly string[], selected: ReadonlySet<string>) => void;
}

export function useSequentialSync({
  engine,
  selectedCount,
  nameFor,
  now = Date.now,
  wait = realWait,
}: UseSequentialSyncOptions): UseSequentialSync {
  const [state, setState] = useState<SequentialSyncState>(SEQUENTIAL_IDLE);
  const stateRef = useRef(state);
  stateRef.current = state;

  /**
   * Raised by cancel, checked at every await boundary in the runner.
   *
   * THE VANILLA HAS NO SUCH FLAG, and that is a live bug. `cancel()` resets
   * the manager but the pending `setTimeout(() => this.syncNext(), 1000)` from
   * the previous iteration still fires. It then finds `currentIndex (0) >=
   * queue.length (0)` and calls `complete()`, which announces a SUCCESS toast
   * for zero playlists — and computes its duration from a `startTime` cancel
   * has already set to null, so `Date.now() - null` yields the epoch in
   * seconds. Cancelling a run pops a green "Sequential sync completed for 0
   * playlists in 1754584800.0s" a second later. Recorded in the dossier; the
   * port simply cannot reproduce it, because the runner stops at the flag.
   */
  const cancelledRef = useRef(false);
  const runningRef = useRef(false);

  const finish = useCallback(
    (announce: () => void) => {
      setState(SEQUENTIAL_IDLE);
      stateRef.current = SEQUENTIAL_IDLE;
      runningRef.current = false;
      engine.setSelectionDisabled(false);
      announce();
      engine.refreshButtons();
    },
    [engine],
  );

  const run = useCallback(
    async (queue: readonly string[], startedAt: number) => {
      for (let index = 0; index < queue.length; index += 1) {
        if (cancelledRef.current) break;
        const playlistId = queue[index];
        setState((prev) => (prev.running ? { ...prev, currentIndex: index } : prev));

        try {
          await engine.startPlaylistSync(playlistId);
          // waitForSyncCompletion (1283-1297): the poller vanishing IS the
          // completion signal. Re-checked against the cancel flag so a cancel
          // during a long sync does not sit here until the engine finishes.
          while (engine.isSyncing(playlistId) && !cancelledRef.current) {
            await wait(1000);
          }
        } catch (error) {
          // 1273-1276: a failure is announced and the run CONTINUES to the
          // next playlist. One bad playlist must not strand the queue.
          engine.toast(
            sequentialFailureToast(
              nameFor(playlistId),
              playlistId,
              error instanceof Error ? error.message : String(error),
            ),
            'error',
          );
        }

        if (cancelledRef.current) break;
        // 1280 — the deliberate gap between syncs.
        await wait(1000);
      }

      if (cancelledRef.current) {
        finish(() => {
          engine.toast(SEQUENTIAL_CANCELLED, 'info');
        });
        return;
      }
      const duration = sequentialDurationSeconds({ ...SEQUENTIAL_IDLE, startedAt }, now());
      finish(() => {
        engine.toast(sequentialCompleteToast(queue.length, duration), 'success');
      });
    },
    [engine, nameFor, now, wait, finish],
  );

  const toggle = useCallback(
    (order: readonly string[], selected: ReadonlySet<string>) => {
      if (runningRef.current) {
        // The cancel half. The runner sees the flag at its next boundary and
        // announces; doing it here too would toast twice.
        cancelledRef.current = true;
        return;
      }

      const queue = syncOrderedSelection(order, selected);
      if (queue.length === 0) {
        // 4073-4076 — refuses, with a toast, before touching the manager.
        engine.toast(SEQUENTIAL_NONE_SELECTED, 'error');
        return;
      }

      const startedAt = now();
      const next = sequentialStart(stateRef.current, queue, startedAt);
      if (next === stateRef.current) return; // refused; nothing to do
      cancelledRef.current = false;
      runningRef.current = true;
      setState(next);
      stateRef.current = next;
      engine.setSelectionDisabled(true);
      void run(queue, startedAt);
    },
    [engine, now, run],
  );

  return {
    state,
    actions: sequentialActionsState(state, selectedCount, nameFor),
    locked: syncSelectionLocked(state),
    toggle,
  };
}
