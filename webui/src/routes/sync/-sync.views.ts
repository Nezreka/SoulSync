/**
 * The Playlists page's three views — the intent-based IA.
 *
 * The old shell organized by DATA SOURCE (Mirrored / Server Playlists /
 * Beatport tabs) and by TOOL (seven equal header buttons). This organizes by
 * what the user is here to do:
 *
 *   overview  mission control — what is syncing now, what is next, what needs
 *             you. The default landing.
 *   library   the collection — mirrored playlists and server playlists, with
 *             every filter, sort, select and card action the old page had.
 *   discover  acquisition — the curation pipeline (discovered, charts, review
 *             pools) as stages, not buttons.
 *
 * Pure recomposition: every panel, hook and vanilla seam the old shell
 * rendered still exists and is reachable from exactly one of these views.
 */

export type SyncViewId = 'overview' | 'library' | 'discover';

export interface SyncView {
  id: SyncViewId;
  label: string;
}

export const SYNC_VIEWS: readonly SyncView[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'library', label: 'Library' },
  { id: 'discover', label: 'Discover' },
];

export const SYNC_DEFAULT_VIEW: SyncViewId = 'overview';

const VIEW_IDS = new Set<string>(SYNC_VIEWS.map((v) => v.id));

/** Unknown ids fall back to the default rather than rendering nothing. */
export function normalizeSyncView(view: string | null | undefined): SyncViewId {
  return VIEW_IDS.has(view as string) ? (view as SyncViewId) : SYNC_DEFAULT_VIEW;
}
