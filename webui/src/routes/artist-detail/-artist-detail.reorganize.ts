/**
 * Reorganize (library.js: showReorganizeModal 5845, loadReorganizePreview
 * 5983, executeReorganize 6087, reorganize-all 6174-6325, status panel
 * 6339-6781). The pure preview/outcome classification, the requests, and the
 * queue poll controller; the modal + panel UIs live in
 * -ui/reorganize-modal.tsx and -ui/reorganize-status-panel.tsx.
 *
 * The controller is module state, as the vanilla's was: the queue outlives any
 * one component, the per-album Reorganize buttons consult the last snapshot
 * before opening the modal, and executeReorganize wakes the panel from
 * outside it.
 */

export interface ReorganizePreviewTrack {
  track_number?: number | string;
  title?: string;
  current_path?: string;
  new_path?: string;
  reason?: string;
  unchanged?: boolean;
  file_exists?: boolean;
  collision?: boolean;
  matched?: boolean;
}

export interface PreviewRowView {
  rowClass: string;
  arrow: string;
  /** The "New Path" cell: a path, an italic reason, or nothing (no file). */
  newCell: { kind: 'path' | 'reason' | 'none'; text: string; collision: boolean };
  currentMissing: boolean;
}

/** Row classification, exactly the vanilla's flag precedence (6024-6055). */
export function classifyPreviewTrack(t: ReorganizePreviewTrack): PreviewRowView {
  const noFile = !t.file_exists;
  const collision = Boolean(t.collision);
  const unmatched = t.matched === false;
  const missingPath = !unmatched && !noFile && !t.new_path;

  const rowClass = collision
    ? 'reorganize-row-collision'
    : noFile || unmatched || missingPath
      ? 'reorganize-row-missing'
      : t.unchanged
        ? 'reorganize-row-unchanged'
        : 'reorganize-row-changed';

  const arrow = collision
    ? '!!'
    : t.unchanged
      ? '='
      : noFile || unmatched || missingPath
        ? '⊘'
        : '→';

  const newCell: PreviewRowView['newCell'] = noFile
    ? { kind: 'none', text: '', collision: false }
    : unmatched
      ? { kind: 'reason', text: t.reason || "Not in selected source's tracklist", collision: false }
      : missingPath
        ? {
            kind: 'reason',
            text: t.reason || "Couldn't compute destination path",
            collision: false,
          }
        : { kind: 'path', text: String(t.new_path), collision };

  return { rowClass, arrow, newCell, currentMissing: noFile };
}

export interface PreviewSummary {
  chips: { className: string; text: string }[];
  /** Movable tracks exist AND nothing collides (6078). */
  canApply: boolean;
}

/** The summary chip row + apply gate (6060-6078). */
export function summarizeReorganizePreview(tracks: ReorganizePreviewTrack[]): PreviewSummary {
  const changed = tracks.filter(
    (t) => !t.unchanged && t.file_exists && !t.collision && t.matched !== false && t.new_path,
  ).length;
  const skipped = tracks.filter((t) => t.unchanged).length;
  const missing = tracks.filter((t) => !t.file_exists).length;
  const collisions = tracks.filter((t) => t.collision).length;
  const unmatched = tracks.filter((t) => t.file_exists && t.matched === false).length;
  const noPath = tracks.filter(
    (t) => t.file_exists && t.matched !== false && !t.new_path && !t.collision,
  ).length;

  const chips: PreviewSummary['chips'] = [];
  if (changed > 0) chips.push({ className: 'changed', text: `${changed} will move` });
  if (skipped > 0) chips.push({ className: 'unchanged', text: `${skipped} unchanged` });
  if (unmatched > 0) {
    chips.push({
      className: 'missing',
      text: `${unmatched} not in source — try a different source`,
    });
  }
  if (noPath > 0)
    chips.push({ className: 'missing', text: `${noPath} couldn't compute destination` });
  if (missing > 0) chips.push({ className: 'missing', text: `${missing} missing on disk` });
  if (collisions > 0) {
    chips.push({
      className: 'collision',
      text: `${collisions} collision${collisions !== 1 ? 's' : ''} — likely a source data issue`,
    });
  }

  // hasChanges deliberately counts colliding rows (a collision IS a change) —
  // the gate then vetoes on hasCollisions, matching 6030/6078.
  const hasChanges = tracks.some(
    (t) => !t.unchanged && t.file_exists && t.matched !== false && Boolean(t.new_path),
  );
  return { chips, canApply: hasChanges && collisions === 0 };
}

/**
 * kettui PR #377 review: distinguish 'completed' from non-completed outcomes
 * so zero-failure skips don't get a green checkmark (6139).
 */
export function classifyReorganizeOutcome(state: {
  result_status?: string;
  failed?: number;
}): 'success' | 'warning' {
  if (state.result_status && state.result_status !== 'completed') return 'warning';
  if ((state.failed || 0) > 0) return 'warning';
  return 'success';
}

export function formatReorganizeResultMessage(state: {
  result_status?: string;
  moved?: number;
  skipped?: number;
  failed?: number;
  errors?: { error?: string }[];
}): string {
  const status = state.result_status;
  if (status === 'no_source_id') {
    return 'Reorganize skipped — album has no metadata source ID. Run enrichment first.';
  }
  if (status === 'no_album') return 'Reorganize skipped — album not found in DB.';
  if (status === 'no_tracks') return 'Reorganize skipped — album has no tracks.';
  if (status === 'setup_failed') return "Reorganize failed — couldn't create staging directory.";
  if (status === 'error') return 'Reorganize failed — see server logs for details.';
  let msg = `Reorganized: ${state.moved || 0} moved`;
  if ((state.skipped || 0) > 0) msg += `, ${state.skipped} skipped`;
  if ((state.failed || 0) > 0) msg += `, ${state.failed} failed`;
  if ((state.failed || 0) > 0 && state.errors && state.errors.length > 0) {
    msg += ` (${state.errors[0].error})`;
  }
  return msg;
}

// ---- Mode persistence (#592) ----

const MODE_KEY = 'soulsync-reorganize-mode';

export function readReorganizeMode(): string {
  try {
    return localStorage.getItem(MODE_KEY) || 'api';
  } catch {
    return 'api';
  }
}

export function writeReorganizeMode(mode: string): void {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* localStorage unavailable, ignore */
  }
}

// ---- Requests ----

export interface ReorganizeSource {
  source: string;
  label?: string;
}

export async function fetchAlbumReorganizeSources(albumId: unknown): Promise<ReorganizeSource[]> {
  const response = await fetch(`/api/library/album/${albumId}/reorganize/sources`);
  if (!response.ok) return [];
  const data = await response.json();
  return data.sources || [];
}

export async function fetchGlobalReorganizeSources(): Promise<ReorganizeSource[]> {
  const response = await fetch('/api/library/reorganize/sources');
  if (!response.ok) return [];
  const data = await response.json();
  return data.sources || [];
}

export async function fetchReorganizePreview(
  albumId: unknown,
  source: string,
  mode: string,
): Promise<{ tracks: ReorganizePreviewTrack[]; error?: string }> {
  const response = await fetch(`/api/library/album/${albumId}/reorganize/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, mode }),
  });
  const result = await response.json();
  if (!result.success) return { tracks: [], error: result.error || 'Preview failed' };
  return { tracks: result.tracks || [] };
}

/** Queue one album; returns the vanilla's toast line (6113-6120). Throws on failure. */
export async function queueReorganizeRequest(
  albumId: unknown,
  albumTitle: string,
  options: { source: string; mode: string; renameOnly: boolean },
): Promise<string> {
  const response = await fetch(`/api/library/album/${albumId}/reorganize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source: options.source,
      mode: options.mode,
      rename_only: options.renameOnly,
    }),
  });
  const result = await response.json();
  if (!result.success) throw new Error(result.error);
  if (result.queued) {
    const posLabel =
      result.position && result.position > 1 ? ` (#${result.position} in queue)` : '';
    return `Queued: ${albumTitle}${posLabel}`;
  }
  if (result.reason === 'already_queued') return `Already queued: ${albumTitle}`;
  return 'Reorganize queued';
}

/** Queue every album for an artist; toast line combos from 6305-6315. */
export async function queueReorganizeAllRequest(
  artistId: unknown,
  artistName: string,
  options: { source: string; mode: string },
): Promise<{ message: string; tone: 'info' | 'warning' }> {
  const response = await fetch(`/api/library/artist/${artistId}/reorganize-all`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source: options.source, mode: options.mode }),
  });
  const result = await response.json();
  if (!result.success) throw new Error(result.error || 'Queue request failed');
  const enqueued = result.enqueued || 0;
  const already = result.already_queued || 0;
  if (enqueued > 0 && already > 0) {
    return {
      message: `Queued ${enqueued} album${enqueued !== 1 ? 's' : ''}; ${already} already in queue`,
      tone: 'info',
    };
  }
  if (enqueued > 0) {
    return {
      message: `Queued ${enqueued} album${enqueued !== 1 ? 's' : ''} for ${artistName}`,
      tone: 'info',
    };
  }
  if (already > 0) {
    return {
      message: `All ${already} album${already !== 1 ? 's' : ''} already in queue`,
      tone: 'info',
    };
  }
  return { message: 'No albums to queue', tone: 'warning' };
}

export async function cancelReorganizeQueueItemRequest(queueId: string): Promise<void> {
  try {
    const response = await fetch(
      `/api/library/reorganize/queue/${encodeURIComponent(queueId)}/cancel`,
      { method: 'POST' },
    );
    const data = await response.json();
    if (data.cancelled) {
      window.showToast?.('Cancelled queued item', 'info');
    } else if (data.reason === 'running_cant_cancel') {
      window.showToast?.('Already running — too late to cancel', 'warning');
    } else {
      window.showToast?.('Could not cancel item', 'warning');
    }
  } catch (error) {
    window.showToast?.(`Cancel failed: ${(error as Error).message}`, 'error');
  }
  void refreshReorganizeQueue();
}

/** Cancel All: confirm (destructive), then clear; the running item continues. */
export async function clearReorganizeQueueRequest(): Promise<void> {
  const queued = lastSnapshot?.queued?.length || 0;
  if (queued === 0) return;
  const confirmed = await window.showConfirmDialog?.({
    title: 'Cancel All Queued',
    message: `Cancel ${queued} queued reorganize${queued !== 1 ? 's' : ''}? The currently-running item will continue.`,
    confirmText: 'Cancel All',
    destructive: true,
  });
  if (!confirmed) return;
  try {
    const response = await fetch('/api/library/reorganize/queue/clear', { method: 'POST' });
    const data = await response.json();
    if (data.success) {
      window.showToast?.(
        `Cancelled ${data.cancelled} queued item${data.cancelled !== 1 ? 's' : ''}`,
        'info',
      );
    }
  } catch (error) {
    window.showToast?.(`Clear failed: ${(error as Error).message}`, 'error');
  }
  void refreshReorganizeQueue();
}

// ---- Queue poll controller ----

export interface ReorganizeQueueItem {
  queue_id?: string;
  album_id?: unknown;
  album_title?: string;
  artist_id?: unknown;
  artist_name?: string;
  source?: string;
  status?: string;
  result_status?: string;
  moved?: number;
  skipped?: number;
  failed?: number;
  error?: string;
  finished_at?: number;
  progress_total?: number;
  progress_processed?: number;
  current_track?: string;
}

export interface ReorganizeSnapshot {
  active?: ReorganizeQueueItem | null;
  queued?: ReorganizeQueueItem[];
  recent?: ReorganizeQueueItem[];
}

const FAST_MS = 1500;
const SLOW_MS = 8000;

let pollTimer: ReturnType<typeof setTimeout> | null = null;
let polling = false;
let inflight = false;
let lastSnapshot: ReorganizeSnapshot | null = null;
let panelArtistId: string | null = null;
let onSnapshotCb: ((snapshot: ReorganizeSnapshot | null) => void) | null = null;
let onReloadCb: (() => void) | null = null;

/**
 * The panel component starts polling on mount and stops on unmount — that
 * replaces the vanilla's document.body.contains unmount detection.
 */
export function startReorganizeQueuePolling(
  artistId: unknown,
  callbacks: { onSnapshot: (snapshot: ReorganizeSnapshot | null) => void; onReload: () => void },
): void {
  stopReorganizeQueuePolling();
  polling = true;
  panelArtistId = artistId == null ? null : String(artistId);
  onSnapshotCb = callbacks.onSnapshot;
  onReloadCb = callbacks.onReload;
  void refreshReorganizeQueue();
}

export function stopReorganizeQueuePolling(): void {
  polling = false;
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
  lastSnapshot = null;
  panelArtistId = null;
  onSnapshotCb = null;
  onReloadCb = null;
  if (reloadTimer) clearTimeout(reloadTimer);
  reloadTimer = null;
  pendingReload = false;
  lastActiveId = null;
}

/**
 * One poll: fetch, keep the last snapshot on a network blip, notify, then
 * reschedule fast while there is work in flight and slowly when idle
 * (refreshReorganizeStatusPanel, 6394). Also the exported "wake" the modal
 * calls right after queueing so the new item shows before the next tick.
 */
export async function refreshReorganizeQueue(): Promise<void> {
  if (!polling || inflight) return;
  inflight = true;

  let snapshot: ReorganizeSnapshot | null = null;
  try {
    const response = await fetch('/api/library/reorganize/queue');
    if (response.ok) {
      const data = await response.json();
      if (data.success !== false) snapshot = data;
    } else {
      console.warn('Reorganize queue snapshot HTTP', response.status);
    }
  } catch (error) {
    console.warn('Reorganize queue snapshot failed:', error);
  } finally {
    inflight = false;
  }

  if (!polling) return;
  if (snapshot) lastSnapshot = snapshot;
  onSnapshotCb?.(lastSnapshot);
  maybeReloadAfterCompletion(lastSnapshot);

  const active = lastSnapshot?.active;
  const queuedCount = lastSnapshot?.queued?.length || 0;
  const next = active || queuedCount > 0 ? FAST_MS : SLOW_MS;
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(() => {
    pollTimer = null;
    void refreshReorganizeQueue();
  }, next);
}

/** The modal short-circuit: is this album already running or queued? (6542) */
export function reorganizeStateForAlbum(albumId: unknown): 'running' | 'queued' | null {
  if (!lastSnapshot) return null;
  const id = String(albumId);
  if (lastSnapshot.active && String(lastSnapshot.active.album_id) === id) return 'running';
  if ((lastSnapshot.queued || []).some((q) => String(q.album_id) === id)) return 'queued';
  return null;
}

/** Cross-artist hint: "Album (other artist)" when the item isn't the page's (6574). */
export function reorgDisplayLabel(item: ReorganizeQueueItem | null | undefined): string {
  if (!item) return '';
  if (panelArtistId && item.artist_id && String(item.artist_id) !== panelArtistId) {
    return `${item.album_title || 'Unknown album'} (${item.artist_name || 'other artist'})`;
  }
  return item.album_title || 'Unknown album';
}

export function isCrossArtist(item: ReorganizeQueueItem): boolean {
  return Boolean(panelArtistId && item.artist_id && String(item.artist_id) !== panelArtistId);
}

/**
 * Mark the per-album Reorganize buttons running/queued (6551). Deliberately a
 * DOM paint, like the vanilla: the buttons live deep inside album panels that
 * know nothing about the queue. A React re-render can wipe the classes, and
 * the next poll tick repaints them — the same self-heal the vanilla's own full
 * re-renders relied on.
 */
export function paintQueuedAlbumButtons(snapshot: ReorganizeSnapshot | null): void {
  const queuedIds = new Set<string>();
  const runningIds = new Set<string>();
  if (snapshot?.active) runningIds.add(String(snapshot.active.album_id));
  for (const q of snapshot?.queued || []) queuedIds.add(String(q.album_id));

  document.querySelectorAll('.enhanced-reorganize-album-btn[data-album-id]').forEach((btn) => {
    const el = btn as HTMLElement;
    const id = el.dataset.albumId || '';
    if (runningIds.has(id)) {
      el.classList.add('reorg-state-running');
      el.classList.remove('reorg-state-queued');
      el.title = 'Reorganize already running for this album';
    } else if (queuedIds.has(id)) {
      el.classList.add('reorg-state-queued');
      el.classList.remove('reorg-state-running');
      el.title = 'Album already queued for reorganize';
    } else {
      el.classList.remove('reorg-state-queued', 'reorg-state-running');
      el.title = 'Reorganize album files using your configured download template';
    }
  });
}

// ---- Debounced enhanced-view reload after a completion (6713) ----

let lastActiveId: string | undefined | null = null;
let pendingReload = false;
let reloadTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Reload the enhanced view when a completion lands for the page's artist, but
 * only once the queue is idle FOR THAT ARTIST, debounced 1.5s — so a 20-album
 * batch triggers one reload at the end, not twenty mid-flight, and a
 * cross-artist batch never triggers one at all.
 */
function maybeReloadAfterCompletion(snapshot: ReorganizeSnapshot | null): void {
  const active = snapshot?.active;
  const recent = snapshot?.recent || [];
  const queued = snapshot?.queued || [];

  if (active) {
    lastActiveId = active.queue_id;
  } else if (lastActiveId && recent.length > 0) {
    const recentTop = recent[0];
    if (recentTop.queue_id === lastActiveId) {
      const finishedRecently = (recentTop.finished_at || 0) >= Date.now() / 1000 - 10;
      const sameArtist =
        panelArtistId && recentTop.artist_id && String(recentTop.artist_id) === panelArtistId;
      if (finishedRecently && sameArtist) pendingReload = true;
      lastActiveId = null;
    }
  }

  if (!pendingReload) return;

  const stillBusyForOurArtist =
    active && panelArtistId && active.artist_id && String(active.artist_id) === panelArtistId;
  const queuedForOurArtist = queued.some(
    (q) => panelArtistId && q.artist_id && String(q.artist_id) === panelArtistId,
  );

  if (stillBusyForOurArtist || queuedForOurArtist) {
    if (reloadTimer) {
      clearTimeout(reloadTimer);
      reloadTimer = null;
    }
    return;
  }

  if (reloadTimer) clearTimeout(reloadTimer);
  reloadTimer = setTimeout(() => {
    reloadTimer = null;
    pendingReload = false;
    onReloadCb?.();
  }, 1500);
}

/** Test hook: module timers and state must not leak between tests. */
export function _resetReorganizePolling(): void {
  stopReorganizeQueuePolling();
}
