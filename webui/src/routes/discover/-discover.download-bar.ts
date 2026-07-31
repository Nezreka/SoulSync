/**
 * The discover download bar.
 *
 * Transcribed from `discoverDownloads` (11574), `addDiscoverDownload` (11579),
 * `monitorDiscoverDownload` (11610), `removeDiscoverDownload` (11711),
 * `updateDiscoverDownloadBar` (11722), `saveDiscoverDownloadSnapshot` (12191)
 * and `hydrateDiscoverDownloadsFromSnapshot` (12252) — read end to end first,
 * along with all four external consumers.
 *
 * ── This is a SHARED global, not page state ─────────────────────────────────
 *
 * `discoverDownloads` is read from three other files, two of them WITHOUT a
 * `typeof` guard:
 *
 *   downloads.js:954     reads it, then calls removeDiscoverDownload (956)
 *   downloads.js:1806    calls addDiscoverDownload
 *   shell-bridge.js:49   Object.keys(...).length — typeof-guarded
 *   wishlist-tools.js:7443  Object.keys(discoverDownloads)     — UNGUARDED
 *   wishlist-tools.js:7551  discoverDownloads[playlistId]      — UNGUARDED
 *   init.js:3541         calls hydrateDiscoverDownloadsFromSnapshot
 *
 * `Object.keys(undefined)` throws, so the two unguarded reads mean the dashboard
 * download section breaks outright if this store is not on `window` by the time
 * wishlist-tools runs. It is therefore published eagerly at module load, not
 * from a component effect — see `publishDownloadGlobals`.
 *
 * The vanilla REASSIGNS the binding during hydrate (12279) rather than clearing
 * it in place. That is safe because every consumer reads the name fresh; this
 * port keeps the same shape for the same reason.
 */

/** `setInterval(..., 2000)` (11705). */
export const MONITOR_INTERVAL_MS = 2000;
/** 5 checks × 2s = 10 seconds for a sync to appear before giving up (11612). */
export const MAX_NOT_FOUND_ATTEMPTS = 5;
/** Completed entries clear themselves after 30s (11629, 11663, 11689). */
export const AUTO_REMOVE_MS = 30000;
/** `setTimeout(..., 1000)` around the snapshot POST (12249). */
export const SNAPSHOT_DEBOUNCE_MS = 1000;

export const SNAPSHOT_ENDPOINT = '/api/discover_downloads/snapshot';
export const HYDRATE_ENDPOINT = '/api/discover_downloads/hydrate';

export type DownloadStatus = 'in_progress' | 'completed';

export interface DiscoverDownload {
  name: string;
  type: string;
  status: DownloadStatus;
  /** Always equal to the key (11587). Kept because the snapshot round-trips it. */
  virtualPlaylistId: string;
  imageUrl: string | null;
  startTime: Date;
}

export type DownloadState = Record<string, DiscoverDownload>;

export interface AddDownloadArgs {
  playlistId: string;
  playlistName: string;
  playlistType: string;
  imageUrl?: string | null;
  /** Injectable so the tests do not depend on the clock. */
  now?: Date;
}

/** New entries always start in progress (11586). */
export function addDownload(state: DownloadState, args: AddDownloadArgs): DownloadState {
  return {
    ...state,
    [args.playlistId]: {
      name: args.playlistName,
      type: args.playlistType,
      status: 'in_progress',
      virtualPlaylistId: args.playlistId,
      imageUrl: args.imageUrl ?? null,
      startTime: args.now ?? new Date(),
    },
  };
}

export function removeDownload(state: DownloadState, playlistId: string): DownloadState {
  if (!(playlistId in state)) return state;
  const next = { ...state };
  delete next[playlistId];
  return next;
}

/** Marking a missing entry is a no-op — the user may have dismissed it already. */
export function markCompleted(state: DownloadState, playlistId: string): DownloadState {
  const entry = state[playlistId];
  if (!entry) return state;
  return { ...state, [playlistId]: { ...entry, status: 'completed' } };
}

/**
 * The auto-remove timer re-checks before firing (11626, 11660, 11686).
 *
 * Thirty seconds is long enough for the entry to have been removed and a NEW
 * download re-added under the same playlist id; without this check the timer
 * would delete the new one.
 */
export function shouldAutoRemove(state: DownloadState, playlistId: string): boolean {
  return state[playlistId]?.status === 'completed';
}

// ── Monitoring ──────────────────────────────────────────────────────────────

/**
 * The websocket path accepts TWO terminal statuses (11619); the REST polling
 * path accepts only one (11677).
 *
 * That asymmetry is real, not a typo to tidy: the socket relays raw sync events,
 * which use 'finished', while `/api/sync/status` normalises to 'complete'.
 * Making the REST path also accept 'finished' would be harmless; making the
 * socket path reject it would strand every socket-delivered completion.
 */
export function socketStatusIsTerminal(status: string | undefined): boolean {
  return status === 'complete' || status === 'finished';
}

export function restStatusIsTerminal(status: string | undefined): boolean {
  return status === 'complete';
}

/** `notFoundCount >= maxNotFoundAttempts` (11696). */
export function shouldGiveUp(notFoundCount: number): boolean {
  return notFoundCount >= MAX_NOT_FOUND_ATTEMPTS;
}

/** Any successful response resets the counter (11673). */
export function nextNotFoundCount(current: number, responseOk: boolean, status: number): number {
  if (responseOk) return 0;
  if (status === 404) return current + 1;
  return current;
}

/**
 * Whether the REST poll runs at all (11669).
 *
 * With a live socket the poll returns before fetching — the socket handler owns
 * completion. The active-process check ABOVE it still runs either way, because
 * modal-driven downloads never produce sync events.
 */
export function restPollEnabled(socketConnected: boolean): boolean {
  return !socketConnected;
}

// ── The bar UI ──────────────────────────────────────────────────────────────

export const BUBBLE_ICON_COMPLETED = '✅';
export const BUBBLE_ICON_ACTIVE = '⏳';

/** The gradient used when a download has no cover art (11766). */
export const BUBBLE_FALLBACK_GRADIENT =
  'background: linear-gradient(135deg, rgba(29, 185, 84, 0.3) 0%, rgba(24, 156, 71, 0.2) 100%);';

export function bubbleBackground(imageUrl: string | null | undefined): string {
  return imageUrl ? `background-image: url('${imageUrl}');` : BUBBLE_FALLBACK_GRADIENT;
}

export interface DownloadBubble {
  playlistId: string;
  name: string;
  completed: boolean;
  icon: string;
  background: string;
  title: string;
}

export interface DownloadBarView {
  count: number;
  /** The whole sidebar hides at zero (11747). */
  hidden: boolean;
  bubbles: DownloadBubble[];
}

export function downloadBarView(state: DownloadState): DownloadBarView {
  const ids = Object.keys(state);
  return {
    count: ids.length,
    hidden: ids.length === 0,
    bubbles: ids.map((playlistId) => {
      const d = state[playlistId];
      const completed = d.status === 'completed';
      return {
        playlistId,
        name: d.name,
        completed,
        icon: completed ? BUBBLE_ICON_COMPLETED : BUBBLE_ICON_ACTIVE,
        background: bubbleBackground(d.imageUrl),
        title: `${d.name} - Click to view`,
      };
    }),
  };
}

// ── Snapshot persistence ────────────────────────────────────────────────────

export interface SnapshotEntry {
  name: string;
  type: string;
  status: DownloadStatus;
  virtualPlaylistId: string;
  imageUrl: string | null;
  /** ISO string — Dates do not survive JSON (12224). */
  startTime: string;
}

/**
 * An EMPTY state is never written (12208).
 *
 * That looks like it would leave a stale snapshot behind after the last
 * download is dismissed, but the hydrate handler deletes the whole snapshot
 * when no download process is active (web_server.py:28044) and marks any
 * process-less entry completed (28063), so the staleness is absorbed
 * server-side. Left as-is deliberately.
 */
export function snapshotPayload(state: DownloadState): Record<string, SnapshotEntry> | null {
  const ids = Object.keys(state);
  if (ids.length === 0) return null;
  const clean: Record<string, SnapshotEntry> = {};
  for (const id of ids) {
    const d = state[id];
    clean[id] = {
      name: d.name,
      type: d.type,
      status: d.status,
      virtualPlaylistId: d.virtualPlaylistId,
      imageUrl: d.imageUrl,
      startTime: d.startTime instanceof Date ? d.startTime.toISOString() : d.startTime,
    };
  }
  return clean;
}

export interface HydrateResult {
  state: DownloadState;
  /** Only in-progress entries get a monitor restarted (12295). */
  toMonitor: string[];
}

/**
 * Rebuild the state from the backend's live-status snapshot (12282-12298).
 *
 * The status comes from the SERVER, not the saved snapshot — it cross-references
 * the running processes, so a download that finished while the page was closed
 * hydrates as completed rather than spinning forever.
 *
 * The vanilla deliberately does NOT touch the UI here (12301): hydration runs at
 * app start from init.js, which may be on any page, and the bar updates when the
 * user navigates to discover.
 */
export function hydrateState(
  downloads: Record<string, Partial<SnapshotEntry>> | null | undefined,
): HydrateResult {
  const state: DownloadState = {};
  const toMonitor: string[] = [];
  for (const [playlistId, d] of Object.entries(downloads || {})) {
    state[playlistId] = {
      name: d.name ?? '',
      type: d.type ?? '',
      status: (d.status as DownloadStatus) ?? 'in_progress',
      virtualPlaylistId: d.virtualPlaylistId ?? playlistId,
      imageUrl: d.imageUrl ?? null,
      startTime: new Date(d.startTime as string),
    };
    if (d.status === 'in_progress') toMonitor.push(playlistId);
  }
  return { state, toMonitor };
}

// ── The window contract ─────────────────────────────────────────────────────

export interface DownloadGlobals {
  discoverDownloads: DownloadState;
  addDiscoverDownload: (
    playlistId: string,
    playlistName: string,
    playlistType: string,
    imageUrl?: string | null,
  ) => void;
  removeDiscoverDownload: (playlistId: string) => void;
  updateDiscoverDownloadBar: () => void;
  hydrateDiscoverDownloadsFromSnapshot: () => Promise<void>;
}

/**
 * The exact names the other files reference. Anything dropped from this list
 * breaks a caller silently or loudly; `test_vanilla_globals_resolve` is what
 * catches the loud half, and this list is what keeps the quiet half honest.
 */
export const REQUIRED_DOWNLOAD_GLOBALS = [
  'discoverDownloads',
  'addDiscoverDownload',
  'removeDiscoverDownload',
  'updateDiscoverDownloadBar',
  'hydrateDiscoverDownloadsFromSnapshot',
] as const;

/**
 * Publish the store on `window`.
 *
 * Called at MODULE LOAD, not from an effect — wishlist-tools.js reads
 * `discoverDownloads` unguarded while rendering the dashboard, which can happen
 * before any discover component has mounted.
 */
export function publishDownloadGlobals(
  target: Record<string, unknown>,
  api: DownloadGlobals,
): void {
  for (const key of REQUIRED_DOWNLOAD_GLOBALS) {
    target[key] = api[key];
  }
}

/** Keep `window.discoverDownloads` pointed at the current state after a change. */
export function syncDownloadState(target: Record<string, unknown>, state: DownloadState): void {
  target.discoverDownloads = state;
}
