/**
 * user playlists: the ones people make inside soulsync.
 *
 * on the server they are mirrored playlists with source 'soulsync', so rename,
 * delete, identify, sync and auto-sync all go through the mirrored routes.
 * this file is only the parts a mirror never had: make one, and add / remove /
 * reorder its tracks. it lives under features/ because every page with a
 * track on it will call addToUserPlaylist, not just the playlists page.
 */

export const USER_PLAYLIST_SOURCE = 'soulsync';

/** what a page hands over. artist + title is the whole contract; album and
 *  duration help identify pick the right version when the page has them. */
export interface UserPlaylistTrack {
  track_name: string;
  artist_name: string;
  album_name?: string;
  duration_ms?: number;
  image_url?: string | null;
}

export interface UserPlaylistSummary {
  id: number;
  name: string;
  track_count: number;
  image_url?: string | null;
}

export interface AddTracksResult {
  added: number;
  duplicates: { track_name: string; artist_name: string }[];
  track_count: number;
}

async function send<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  let data: (T & { error?: string }) | null = null;
  try {
    data = (await response.json()) as T & { error?: string };
  } catch {
    data = null;
  }
  if (!response.ok || !data || data.error) {
    throw new Error(data?.error || `Request failed (${response.status})`);
  }
  return data;
}

function json(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

export function isUserPlaylist(row: { source?: string | null } | null | undefined): boolean {
  return row?.source === USER_PLAYLIST_SOURCE;
}

export async function fetchUserPlaylists(): Promise<UserPlaylistSummary[]> {
  const data = await send<{ playlists: UserPlaylistSummary[] }>('/api/user-playlists');
  return data.playlists ?? [];
}

/** make a playlist, and optionally drop the first tracks in on the same trip. */
export async function createUserPlaylist(
  name: string,
  tracks: readonly UserPlaylistTrack[] = [],
): Promise<{ id: number } & Partial<AddTracksResult>> {
  return send('/api/user-playlists', json('POST', { name, tracks }));
}

/** a song already in the playlist comes back in `duplicates` and is not added,
 *  unless allowDuplicates. */
export async function addToUserPlaylist(
  playlistId: number,
  tracks: readonly UserPlaylistTrack[],
  allowDuplicates = false,
): Promise<AddTracksResult> {
  return send(
    `/api/user-playlists/${playlistId}/tracks`,
    json('POST', { tracks, allow_duplicates: allowDuplicates }),
  );
}

/** position is 1-based, as the detail payload numbers them. */
export async function removeFromUserPlaylist(
  playlistId: number,
  position: number,
): Promise<{ track_count: number }> {
  return send(`/api/user-playlists/${playlistId}/tracks/${position}`, { method: 'DELETE' });
}

/** every current 1-based position, once each, in the new order. */
export async function reorderUserPlaylist(
  playlistId: number,
  order: readonly number[],
): Promise<void> {
  await send(`/api/user-playlists/${playlistId}/order`, json('PUT', { order }));
}

/** move one item, for drag-to-reorder: the position list after dragging
 *  `from` onto `to` (both 0-based indexes into the current list). */
export function movedOrder(count: number, from: number, to: number): number[] {
  const order = Array.from({ length: count }, (_, i) => i + 1);
  if (from === to || from < 0 || to < 0 || from >= count || to >= count) return order;
  const [item] = order.splice(from, 1);
  order.splice(to, 0, item);
  return order;
}
