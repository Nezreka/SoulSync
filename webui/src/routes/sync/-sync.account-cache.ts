/**
 * Remembers the playlists an account tab has already fetched, across
 * navigation.
 *
 * Specialmed (Discord): "the playlists are not saved as they are saved in
 * Deezer. I always have to make a refresh, as they disappear."
 *
 * They never disappeared. The tab holds its rows in component state, and the
 * source verticals are hooks created at the sync page's top level — so leaving
 * /sync unmounts all of it and the rows are gone. Deezer only LOOKS persistent
 * because its tab re-fetches on mount and is fast enough that you cannot see
 * it happen. Tidal, whose fetch takes minutes, cannot hide that.
 *
 * Auto-fetching on mount is the wrong fix for Tidal specifically: its tab
 * deliberately waits for Refresh (a test pins the "Click 'Refresh' to load
 * your Tidal playlists." placeholder) precisely because a load costs a full
 * account fetch plus a background track crawl. Doing that on every visit to
 * the page is the very cost being complained about.
 *
 * So: keep the rows, don't repeat the request. Returning to the tab restores
 * what was already loaded, and Refresh stays the explicit "go fetch again".
 *
 * In memory rather than sessionStorage, on purpose. After the track crawl each
 * row carries its full track list, so a large account is megabytes — well into
 * sessionStorage's quota, where a failed write is silent and partial. This
 * cache lives as long as the tab (the browser one) does, which is exactly the
 * span the complaint is about; a hard reload legitimately starts fresh.
 */

import type { UrlTabPlaylist } from './-sync.url-tabs';

const remembered = new Map<string, UrlTabPlaylist[]>();

/** Store the rows a source has loaded. Storing an empty list is meaningful —
 * "this account genuinely has no playlists" is a real answer and should not
 * make the tab look unvisited. */
export function rememberAccountPlaylists(source: string, rows: UrlTabPlaylist[]): void {
  if (!source || !Array.isArray(rows)) return;
  remembered.set(source, rows);
}

/** The rows a source loaded earlier, or null if it never has.
 *
 * null and [] mean different things here and the caller depends on it: null is
 * "never loaded" (show the click-Refresh placeholder), [] is "loaded, empty"
 * (show the no-playlists-found message). */
export function recallAccountPlaylists(source: string): UrlTabPlaylist[] | null {
  if (!source) return null;
  const rows = remembered.get(source);
  return rows ?? null;
}

/** Drop one source's rows, or everything. For a real re-fetch and for tests —
 * a module-level cache that leaks between test cases produces failures that
 * depend on file order. */
export function forgetAccountPlaylists(source?: string): void {
  if (source) remembered.delete(source);
  else remembered.clear();
}
