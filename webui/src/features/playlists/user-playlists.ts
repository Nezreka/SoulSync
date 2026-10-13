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
  /** each song that looked already there, and the copy it matched (which can
   *  be spelled differently: "Alright" vs "Alright (Remastered)") */
  duplicates: {
    track_name: string;
    artist_name: string;
    existing_track_name?: string;
    existing_artist_name?: string;
  }[];
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

/** the short blurb under the playlist's name. blank clears it. */
export async function setPlaylistDescription(
  playlistId: number,
  description: string,
): Promise<{ description: string }> {
  return send(`/api/user-playlists/${playlistId}`, json('PATCH', { description }));
}

/** copy the whole playlist under a new id: tracks, cover and description. */
export async function duplicateUserPlaylist(
  playlistId: number,
  name?: string,
): Promise<{ id: number; name: string }> {
  return send(
    `/api/user-playlists/${playlistId}/duplicate`,
    json('POST', name === undefined ? {} : { name }),
  );
}

/** upload a photo; the server re-encodes it to a square webp. */
export async function uploadPlaylistCover(
  playlistId: number,
  file: File,
): Promise<{ image_url: string }> {
  const form = new FormData();
  form.append('file', file);
  return send(`/api/user-playlists/${playlistId}/cover`, { method: 'POST', body: form });
}

/** point the cover at a remote http(s) image. */
export async function setPlaylistCoverUrl(
  playlistId: number,
  imageUrl: string,
): Promise<{ image_url: string }> {
  return send(`/api/user-playlists/${playlistId}/cover`, json('PUT', { image_url: imageUrl }));
}

/** borrow a track's artwork. fromTrack is 1-based, as the detail numbers them. */
export async function setPlaylistCoverFromTrack(
  playlistId: number,
  fromTrack: number,
): Promise<{ image_url: string }> {
  return send(`/api/user-playlists/${playlistId}/cover`, json('PUT', { from_track: fromTrack }));
}

/** back to no cover: the views fall back to track art. */
export async function clearPlaylistCover(playlistId: number): Promise<void> {
  await send(`/api/user-playlists/${playlistId}/cover`, { method: 'DELETE' });
}
