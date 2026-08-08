/**
 * The sync sidebar's pure core — index.html 3301-3315, plus the two writers
 * that drive it and the log area's scroll rule.
 *
 * THE SIDEBAR HAS TWO WRITERS IN THE VANILLA, saying the same things.
 * `SequentialSyncManager.updateUI` (core.js 1340-1369) owns both the running
 * and the idle case; `updateSyncActionsUI` (sync-spotify.js 1812-1830) handles
 * selection changes and DELEGATES to updateUI whenever a sync is running
 * (1814-1817). Their idle branches are the same three strings, so one function
 * covers both — the only real difference is that updateSyncActionsUI never
 * touches the button's LABEL, which is safe precisely because it only runs
 * when the label is already 'Start Sync'.
 *
 * THE PROGRESS BAR AND TEXT ARE WIRED HERE, and were not in the vanilla.
 * `#sync-progress-bar` and `#sync-progress-text` have no writer anywhere in
 * the vanilla — the bar sits at width:0% and the text reads 'Ready to sync...'
 * for the life of the page. The numbers they want are the ones
 * `SequentialSyncManager` already computes for the selection line, so this
 * port feeds them rather than transcribing two permanently-dead elements into
 * new code. Declared in SYNC_PORT_AUDIT.md; the idle strings are unchanged, so
 * a page that never starts a sync looks exactly as it does today.
 */

export interface SyncActionsState {
  /** SequentialSyncManager.isRunning. */
  running: boolean;
  /** selectedPlaylists.size. */
  selectedCount: number;
  /** manager.currentIndex — 0-based; the label shows it 1-based. */
  currentIndex: number;
  /** manager.queue.length. */
  queueLength: number;
  /** Name of the playlist at currentIndex, if it resolved. */
  currentName?: string | null;
}

/** 1350-1355 idle, 1362-1367 running. */
export function syncSelectionLabel(state: SyncActionsState): string {
  if (state.running) {
    // 1366 interpolates `currentPlaylist?.name || 'Unknown'` — the lookup is
    // `spotifyPlaylists.find(...)` and misses for any queued id that is not a
    // Spotify playlist.
    return `Syncing ${state.currentIndex + 1}/${state.queueLength}: ${state.currentName || 'Unknown'}`;
  }
  const count = state.selectedCount;
  if (count === 0) return 'Select playlists to sync';
  return `${count} playlist${count > 1 ? 's' : ''} selected`;
}

/** 1347 / 1359. */
export function syncStartLabel(running: boolean): string {
  return running ? 'Cancel Sequential Sync' : 'Start Sync';
}

/**
 * 1348 / 1360. Running means the button is a CANCEL, which is always live —
 * that is what makes the vanilla handler a toggle.
 */
export function syncStartDisabled(running: boolean, selectedCount: number): boolean {
  return running ? false : selectedCount === 0;
}

/**
 * Percent for `#sync-progress-bar`. Completed playlists over the queue, so it
 * reads 0% at the start of the first and 100% only once the last has finished.
 */
export function syncProgressPercent(state: SyncActionsState): number {
  if (!state.running || state.queueLength <= 0) return 0;
  const done = Math.min(Math.max(state.currentIndex, 0), state.queueLength);
  return Math.round((done / state.queueLength) * 100);
}

/** The markup's own initial text (3312), kept for the idle case. */
export const SYNC_PROGRESS_IDLE = 'Ready to sync...';

/** Text for `#sync-progress-text`. */
export function syncProgressLabel(state: SyncActionsState): string {
  if (!state.running || state.queueLength <= 0) return SYNC_PROGRESS_IDLE;
  return `${Math.min(state.currentIndex + 1, state.queueLength)} of ${state.queueLength} playlists`;
}

/** The textarea's initial content (3313), before any log frame arrives. */
export const SYNC_LOG_PLACEHOLDER = 'Waiting for sync to start...';

/**
 * updateLogsFromData 1129-1134. A frame with no `logs` array is DROPPED, not
 * rendered as empty — the vanilla returns before touching the textarea, so a
 * malformed push must leave the last good text in place.
 */
export function syncLogText(frame: unknown): string | null {
  const logs = (frame as { logs?: unknown } | null | undefined)?.logs;
  if (!Array.isArray(logs)) return null;
  return logs.join('\n');
}

/**
 * updateLogsFromData 1137-1147, transcribed exactly — including the part that
 * looks like a mistake.
 *
 * `wasUserScrolled` is true when the view is NOT at the bottom, so the guard
 * `wasAtTop || !wasUserScrolled` jumps back to the top both when you were
 * already at the top AND when you were pinned to the bottom. Only a reader
 * parked strictly in the middle keeps their position. Newest entries are at
 * the top (the server reverses the feed), which is why top is the resting
 * place; the bottom case is incidental and harmless.
 */
export function syncLogShouldScrollTop(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number,
): boolean {
  const wasAtTop = scrollTop <= 10;
  const wasUserScrolled = scrollTop < scrollHeight - clientHeight - 10;
  return wasAtTop || !wasUserScrolled;
}

/**
 * showSyncSidebar / hideSyncSidebar (downloads.js 4041-4057) and the tab
 * handler's unconditional re-hide (sync-services.js 3747-3753).
 *
 * The vanilla state is: hidden by CSS at rest, shown when a sequential sync
 * starts, hidden again on completion, cancellation, or ANY tab switch —
 * including one made mid-sync, which drops the progress panel until the next
 * run. Transcribed rather than corrected; it is visible behaviour, not a
 * latent defect.
 *
 * The >1300px half of showSyncSidebar is NOT reproduced here. It is already
 * enforced in CSS (`@media (max-width:1300px) { .sync-sidebar { display: none
 * !important } }`, style.css 14722-14727), and leaving it to the stylesheet
 * fixes a small vanilla wart for free: the vanilla samples innerWidth once, at
 * start, so a sync begun on a narrow window keeps the sidebar hidden even
 * after the window is widened. Declared in the dossier.
 */
export function syncSidebarVisible(running: boolean, hiddenByTabSwitch: boolean): boolean {
  return running && !hiddenByTabSwitch;
}
