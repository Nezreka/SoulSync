import { useSyncExternalStore } from 'react';

import { openWishlistInspector } from '@/features/downloads/inspector-modal';

import { InteractiveSearchModal } from './interactive-search';

/**
 * The library's Interactive Search, opened from outside the library (the
 * wishlist). One window for the whole app: a wish linked to a library track
 * gets the same search the library uses; any other falls back to the
 * candidate inspector.
 */

interface LibrarySearchTarget {
  trackId: number;
  /** Shown while nothing was searched yet; the server picks the queries. */
  label: string;
}

let current: (LibrarySearchTarget & { key: number }) | null = null;
let opened = 0;
const listeners = new Set<() => void>();

function setCurrent(next: typeof current) {
  current = next;
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
  opened += 1;
  setCurrent({ trackId: track.lib2TrackId, label: `${track.artist} ${track.track}`, key: opened });
}

/** Render once inside the page's providers. */
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
      onClose={() => setCurrent(null)}
    />
  );
}
