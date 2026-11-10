import { useSyncExternalStore } from 'react';

import { apiClient, readJson } from '@/app/api-client';
import {
  openDownloadTaskInspector,
  openWishlistInspector,
} from '@/features/downloads/inspector-modal';

import { InteractiveSearchModal } from './interactive-search';

/**
 * The library's Interactive Search, opened from the wishlist. A row linked to
 * a library track gets the search the library uses when this profile may grab
 * from it (the admin, or a profile in a library of its own); everyone else,
 * and every row without a library track, gets the wishlist inspector, which
 * only needs download permission (3.6.1).
 */

interface LibrarySearchTarget {
  trackId: number;
  /** Shown while nothing was searched yet; the server picks the queries. */
  label: string;
}

let current: (LibrarySearchTarget & { key: number }) | null = null;
let opened = 0;
const listeners = new Set<() => void>();

function openLibrarySearch(target: LibrarySearchTarget) {
  opened += 1;
  current = { ...target, key: opened };
  for (const listener of listeners) listener();
}

function closeLibrarySearch() {
  current = null;
  for (const listener of listeners) listener();
}

async function mayGrabFromLibrarySearch(): Promise<boolean> {
  try {
    const payload = await readJson<{ can_grab?: boolean }>(apiClient.get('library/v2/enabled'));
    return payload.can_grab === true;
  } catch {
    return false;
  }
}

export async function openWishlistSearch(track: {
  id: string;
  track: string;
  artist: string;
  album: string;
  lib2TrackId?: number;
}): Promise<void> {
  if (!track.lib2TrackId || !(await mayGrabFromLibrarySearch())) {
    openWishlistInspector({
      id: track.id,
      name: track.track,
      artist: track.artist,
      album: track.album,
    });
    return;
  }
  openLibrarySearch({ trackId: track.lib2TrackId, label: `${track.artist} ${track.track}` });
}

/** A failed or not-found download: its own task inspector, always. Its pick
 *  retries that task inside its batch (playlist folder, batch totals, the
 *  wishlist cleanup tied to it); a grab from the library search was a
 *  separate download the batch never saw (3.6.1 opened the inspector). */
export async function openDownloadTaskSearch(
  taskId: string,
  track: { name?: string; artist?: string; album?: string },
): Promise<void> {
  openDownloadTaskInspector(taskId, track);
}

/** Render once inside the app's providers. */
export function LibrarySearchHost() {
  const open = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
  if (!open) return null;
  // The server decides whether this profile may grab; a refusal shows on the row.
  return (
    <InteractiveSearchModal
      key={open.key}
      canWrite
      initialQuery={open.label}
      entity={{ trackId: open.trackId }}
      onClose={closeLibrarySearch}
    />
  );
}
