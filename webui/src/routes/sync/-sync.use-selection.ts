/**
 * The playlist selection store — the vanilla's `selectedPlaylists`
 * (core.js 34).
 *
 * Deliberately tiny, because the vanilla's is. Every mutation in the whole app
 * is the add/delete pair inside `togglePlaylistSelection` (sync-spotify.js
 * 1804-1808); the other four references only READ `.size` or `.has`. There is
 * no clear, no prune, no bulk select.
 *
 * THAT MEANS THE SET IS NEVER EMPTIED, and two consequences follow that look
 * like bugs but are the shipped behaviour:
 *
 *  - A completed sync leaves everything still selected. The sidebar goes back
 *    to "3 playlists selected" and Start Sync is live again.
 *  - Refreshing the playlists does not prune ids whose cards are gone. A stale
 *    id simply never reaches a queue, because `syncOrderedSelection` keeps only
 *    what the page currently lists.
 *
 * Neither is corrected here. A `clear()` nobody calls would also be an export
 * no test can justify.
 *
 * SPOTIFY-ONLY, in practice: `togglePlaylistSelection` lives in sync-spotify.js
 * and no other tab calls it, so every id in here is a Spotify playlist id. The
 * store does not enforce that — it holds strings — but the queue's display
 * order comes from the Spotify rows for the same reason.
 */

import { useCallback, useMemo, useState } from 'react';

import { syncToggleSelection } from './-sync.sequential';

export interface SyncSelection {
  selected: ReadonlySet<string>;
  /** `selectedPlaylists.size` — what the sidebar's label and button read. */
  count: number;
  /** togglePlaylistSelection (1800-1810). In, then out. */
  toggle: (playlistId: string) => void;
}

export function useSyncSelection(): SyncSelection {
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());

  const toggle = useCallback((playlistId: string) => {
    setSelected((prev) => syncToggleSelection(prev, playlistId));
  }, []);

  return useMemo(() => ({ selected, count: selected.size, toggle }), [selected, toggle]);
}
