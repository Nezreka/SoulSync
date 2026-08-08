/**
 * The sequential-sync state machine and the selection store — the vanilla's
 * `SequentialSyncManager` (core.js 1268-1418), `startSequentialSync`
 * (downloads.js 4059-4099) and `selectedPlaylists`.
 *
 * Pure on purpose. The vanilla keeps this state on a singleton whose methods
 * also write the DOM, so the rules and the rendering are the same code — which
 * is how `updateUI` ended up being the only place that knows what the button
 * says. Here the rules are values, the sidebar renders them, and the engine
 * calls are the hook's job.
 *
 * ORDER IS AN ARGUMENT, NOT A DOM QUERY. The vanilla builds its queue by
 * walking `document.querySelectorAll('.playlist-card')` and keeping the
 * selected ones (4079-4087), which makes React's render order a load-bearing
 * contract of the download engine — reorder the grid and the sync order
 * changes. `syncOrderedSelection` takes the order explicitly instead.
 */

import type { SyncActionsState } from './-sync.sidebar';

export interface SequentialSyncState {
  /** manager.isRunning — set SYNCHRONOUSLY by start(), which is what made the
   *  duplicate-listener bug bite (a second call saw it and cancelled). */
  running: boolean;
  /** manager.queue — the ordered ids, snapshotted at start. */
  queue: string[];
  /** manager.currentIndex — 0-based index of the playlist being synced. */
  currentIndex: number;
  /** manager.startTime, or null when idle. */
  startedAt: number | null;
}

/** The constructor's state (1230-1235), and what complete/cancel return to. */
export const SEQUENTIAL_IDLE: SequentialSyncState = {
  running: false,
  queue: [],
  currentIndex: 0,
  startedAt: null,
};

/**
 * 4078-4087, minus the DOM. `order` is the ids as the page lists them; the
 * result keeps that order and drops anything unselected. Selected ids that are
 * not in `order` are DROPPED, exactly as the vanilla drops them — it can only
 * queue what it can see a card for.
 */
export function syncOrderedSelection(
  order: readonly string[],
  selected: ReadonlySet<string>,
): string[] {
  return order.filter((id) => selected.has(id));
}

/** Click-to-select (sync-spotify.js 1800-1809): a plain toggle. */
export function syncToggleSelection(
  selected: ReadonlySet<string>,
  playlistId: string,
): Set<string> {
  const next = new Set(selected);
  if (!next.delete(playlistId)) next.add(playlistId);
  return next;
}

/**
 * start() 1237-1252. The guard is first and returns the state UNCHANGED — the
 * vanilla warns and returns, leaving the running sync alone.
 *
 * An EMPTY queue is refused too. The vanilla never reaches start() with one
 * (4073 bails earlier with a toast), and letting it through would set running
 * with nothing to run — a sidebar stuck on "Syncing 1/0" and a Cancel button
 * as the only way out.
 */
export function sequentialStart(
  state: SequentialSyncState,
  ids: readonly string[],
  now: number,
): SequentialSyncState {
  if (state.running) return state;
  if (ids.length === 0) return state;
  return { running: true, queue: [...ids], currentIndex: 0, startedAt: now };
}

/** syncNext's tail (1279): one finished, move on. */
export function sequentialAdvance(state: SequentialSyncState): SequentialSyncState {
  if (!state.running) return state;
  return { ...state, currentIndex: state.currentIndex + 1 };
}

/** 1255 — syncNext calls complete() once the index runs off the end. */
export function sequentialIsDone(state: SequentialSyncState): boolean {
  return state.running && state.currentIndex >= state.queue.length;
}

/**
 * complete() 1299-1318 and cancel() 1320-1338 reset IDENTICALLY; they differ
 * only in the toast and in cancel's `if (!this.isRunning) return` guard. One
 * function, and the caller decides which announcement to make.
 */
export function sequentialFinish(state: SequentialSyncState): SequentialSyncState {
  if (!state.running) return state;
  return SEQUENTIAL_IDLE;
}

/** 1300 — one decimal place, seconds. */
export function sequentialDurationSeconds(state: SequentialSyncState, now: number): string {
  if (state.startedAt === null) return '0.0';
  return ((now - state.startedAt) / 1000).toFixed(1);
}

/* ── The four announcements, verbatim ─────────────────────────────────────── */

/** 4074. Fires INSTEAD of starting, when nothing is selected. */
export const SEQUENTIAL_NONE_SELECTED = 'No playlists selected for sync';

/** 1334. */
export const SEQUENTIAL_CANCELLED = 'Sequential sync cancelled';

/** 1314. `count` is queue.length read BEFORE the reset. */
export function sequentialCompleteToast(count: number, duration: string): string {
  return `Sequential sync completed for ${count} playlists in ${duration}s`;
}

/** 1275. `name` falls back to the id when the playlist did not resolve. */
export function sequentialFailureToast(
  name: string | null | undefined,
  playlistId: string,
  message: string,
): string {
  return `Failed to sync "${name || playlistId}": ${message}`;
}

/**
 * The bridge to the sidebar. `nameFor` resolves the id at `currentIndex` — the
 * vanilla's `spotifyPlaylists.find(...)` (1365), injected so the lookup is not
 * baked in; the queue can hold ids from any source, and the vanilla's Spotify-
 * only lookup is exactly why the label falls back to 'Unknown'.
 */
export function sequentialActionsState(
  state: SequentialSyncState,
  selectedCount: number,
  nameFor: (playlistId: string) => string | null | undefined,
): SyncActionsState {
  return {
    running: state.running,
    selectedCount,
    currentIndex: state.currentIndex,
    queueLength: state.queue.length,
    currentName: state.running ? nameFor(state.queue[state.currentIndex] ?? '') : null,
  };
}

/**
 * disablePlaylistSelection (downloads.js 4101-4106) is `true` for the whole
 * run: the vanilla disables every checkbox at start (4098) and re-enables in
 * both complete() and cancel(). It is just `running`, named for what it means
 * at the call site.
 */
export function syncSelectionLocked(state: SequentialSyncState): boolean {
  return state.running;
}
