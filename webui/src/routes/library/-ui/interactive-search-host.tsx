import { useSyncExternalStore } from 'react';

import { apiClient, readJson } from '@/app/api-client';
import {
  openDownloadTaskInspector,
  openWishlistInspector,
} from '@/features/downloads/inspector-modal';

import { InteractiveSearchModal } from './interactive-search';

/**
 * The library's Interactive Search, opened from outside the library (the
 * wishlist, a failed download). One window for the whole app: anything linked
 * to a library track gets the same search the library uses; the rest falls
 * back to the candidate inspector.
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

export function openWishlistSearch(track: {
  id: string;
  track: string;
  artist: string;
  album: string;
  lib2TrackId?: number;
}): void {
  if (!track.lib2TrackId) {
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

/** Open the library's search for the library track a download task is for;
 *  false when the task is for none (the caller keeps its own window). */
export async function openLibraryTaskSearch(taskId: string, label = ''): Promise<boolean> {
  let trackId: number | null = null;
  try {
    const payload = await readJson<{ track_id?: number | null }>(
      apiClient.get(`downloads/task/${encodeURIComponent(taskId)}/library-track`),
    );
    trackId = payload.track_id ?? null;
  } catch {
    // an unknown or finished task keeps the caller's window
  }
  if (!trackId) return false;
  openLibrarySearch({ trackId, label });
  return true;
}

/** A failed or not-found download: the library search when the task is for a
 *  library track, the inspector otherwise. */
export async function openDownloadTaskSearch(
  taskId: string,
  track: { name?: string; artist?: string; album?: string },
): Promise<void> {
  const label = [track.artist, track.name].filter(Boolean).join(' ');
  if (!(await openLibraryTaskSearch(taskId, label))) openDownloadTaskInspector(taskId, track);
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
