/**
 * The bulk Sync / Sync & download run, kept outside any component (#2320).
 *
 * The run used to live in the Mirrored tab's state, so the panel went with
 * the tab: click away and it was gone, with no way back to it while the
 * playlists kept syncing. Here it lives for the page session. Closing the
 * panel only hides it, switching tabs or leaving the Playlists page and
 * coming back finds the same run, and a pill on the page reopens it.
 */

import { useSyncExternalStore } from 'react';

import {
  bulkSummary,
  runInTurn,
  type BulkDeps,
  type BulkMode,
  type BulkRunState,
} from './-sync.bulk-run';

export interface BulkStoreState {
  run: BulkRunState | null;
  /** the panel is showing */
  open: boolean;
  /** the run ended a moment ago, so the pill still says how it went */
  justFinished: boolean;
  /** bumps each time a run ends, so the page can reload its list once */
  finishedCount: number;
}

/** how long the pill keeps saying "Done" after a run ends */
export const BULK_DONE_PILL_MS = 8000;

const initial: BulkStoreState = { run: null, open: false, justFinished: false, finishedCount: 0 };

let state: BulkStoreState = initial;
let stopRequested = false;
let doneTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function set(patch: Partial<BulkStoreState>) {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export function subscribeBulk(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getBulkState(): BulkStoreState {
  return state;
}

export function isBulkRunning(): boolean {
  return Boolean(state.run?.running);
}

/**
 * Start a run over these playlists and show the panel. Does nothing (and
 * says false) when one is already going: one run at a time.
 */
export function startBulkRun(
  mode: BulkMode,
  rows: { id: number; name: string }[],
  deps: Partial<BulkDeps> = {},
): boolean {
  if (!rows.length || isBulkRunning()) return false;
  stopRequested = false;
  if (doneTimer) clearTimeout(doneTimer);
  doneTimer = null;
  set({ open: true, justFinished: false });
  void runInTurn(
    mode,
    rows,
    (run) => set({ run: { ...run, stopping: run.running && stopRequested } }),
    () => stopRequested,
    deps,
  ).then((done) => {
    set({ justFinished: true, finishedCount: state.finishedCount + 1 });
    window.showToast?.(
      bulkSummary(done),
      done.items.some((i) => i.state === 'failed') ? 'warning' : 'success',
    );
    doneTimer = setTimeout(() => {
      doneTimer = null;
      set({ justFinished: false });
    }, BULK_DONE_PILL_MS);
  });
  return true;
}

/** let the playlist in progress finish, then start no more */
export function stopBulkRun(): void {
  if (!isBulkRunning() || stopRequested) return;
  stopRequested = true;
  if (state.run) set({ run: { ...state.run, stopping: true } });
}

export function openBulkPanel(): void {
  if (state.run) set({ open: true });
}

/** hide the panel; the run carries on and the pill brings it back */
export function hideBulkPanel(): void {
  if (state.open) set({ open: false });
}

/** close a finished run for good: panel and pill both go */
export function dismissBulkRun(): void {
  if (isBulkRunning()) {
    hideBulkPanel();
    return;
  }
  if (doneTimer) clearTimeout(doneTimer);
  doneTimer = null;
  set({ run: null, open: false, justFinished: false });
}

/** what the pill says: "Syncing 9 of 26", then "Done · 26 synced, 1 failed" */
export function bulkPillLabel(run: BulkRunState): string {
  if (run.running) return bulkSummary(run);
  const count = (s: string) => run.items.filter((i) => i.state === s).length;
  const bits = [`${count('done')} synced`];
  if (count('failed')) bits.push(`${count('failed')} failed`);
  if (count('stopped')) bits.push(`${count('stopped')} not started`);
  return `Done · ${bits.join(', ')}`;
}

export function useBulkRun(): BulkStoreState {
  return useSyncExternalStore(subscribeBulk, getBulkState, getBulkState);
}

/** tests only: forget everything */
export function resetBulkStoreForTests(): void {
  if (doneTimer) clearTimeout(doneTimer);
  doneTimer = null;
  stopRequested = false;
  state = initial;
  for (const l of listeners) l();
}
