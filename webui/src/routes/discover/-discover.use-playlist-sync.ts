import { useCallback, useEffect, useRef, useState } from 'react';

import type { DiscoverMix } from './-discover.mixes';
import type { SyncProgress } from './-discover.playlist-sync';

import { decadeSyncCompleteToast, decadeTrackToSpotify } from './-discover.decade-shelf';
import {
  noTracksToast,
  playlistDisplayName,
  SYNC_POLL_MS,
  SYNC_STATUS_HIDE_MS,
  syncCompleteToast,
  syncIsFinished,
  syncProgress,
  toSyncTracks,
  virtualPlaylistId,
} from './-discover.playlist-sync';

/**
 * The discover sync controller.
 *
 * Transcribed from `startDiscoverPlaylistSync` (11940-…) and `startDecadeSync`
 * + its poller (2718-2860). The engine itself — matching tracks against the
 * media server — stays in the vanilla's downloads.js; the hook seeds a virtual
 * playlist through the `window.startDiscoverVirtualSync` bridge (core.js, the
 * same lexical-scope reason `reopenActiveDownloadModal` lives there) and then
 * POLLS `/api/sync/status/<id>` every 500ms. The vanilla also subscribes via
 * WebSocket when available, with the poll as belt-and-braces; the poll alone is
 * the guaranteed path (`syncPollAlwaysRuns`) and is what this hook keeps.
 *
 * Progress is keyed by the mix's STATUS BASE (syncKey with underscores →
 * hyphens, or the decade's own base), which is what the modal's SyncStatus
 * component reads. On finish: the vanilla's per-type success toast, and the
 * status block clears after the 3s linger.
 *
 * NOT here yet: `checkForActiveDiscoverSyncs` (the reload-mid-sync resume,
 * 320-380). It belongs to this hook and lands with the page composition,
 * where there is a mount to probe from.
 */

export type SyncToast = { message: string; level: 'success' | 'warning' };

export interface PlaylistSyncController {
  /** Live progress by status base; undefined = not syncing. */
  progressFor: (statusBase: string) => SyncProgress | undefined;
  syncingKeys: string[];
  /** Convert + seed + start + poll. Returns the early-out toast, if any. */
  startMixSync: (mix: DiscoverMix, tracks: unknown[] | undefined) => SyncToast | null;
}

/** decade_1980 → { year: 1980 }, or null for every other mix. */
function decadeOf(mix: DiscoverMix): number | null {
  const m = /^decade_(\d+)$/.exec(mix.key);
  return m ? Number(m[1]) : null;
}

export function usePlaylistSync(onToast: (toast: SyncToast) => void): PlaylistSyncController {
  const [progress, setProgress] = useState<Record<string, SyncProgress>>({});
  const timers = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  const lingers = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
  const toastRef = useRef(onToast);
  toastRef.current = onToast;

  useEffect(
    () => () => {
      for (const t of Object.values(timers.current)) clearInterval(t);
      for (const t of lingers.current) clearTimeout(t);
    },
    [],
  );

  const poll = useCallback((statusBase: string, virtualId: string, doneToast: string) => {
    // One poller per base — restarting a sync replaces its poller (2788).
    if (timers.current[statusBase]) clearInterval(timers.current[statusBase]);
    timers.current[statusBase] = setInterval(() => {
      void (async () => {
        try {
          const res = await fetch(`/api/sync/status/${virtualId}`);
          if (!res.ok) return;
          const data = (await res.json()) as { status?: string; progress?: unknown };
          const p = syncProgress(data.progress as never);
          setProgress((prev) => ({ ...prev, [statusBase]: p }));
          if (syncIsFinished(data.status)) {
            clearInterval(timers.current[statusBase]);
            delete timers.current[statusBase];
            toastRef.current({ message: doneToast, level: 'success' });
            // The status block lingers, then clears (3000ms, 2815). Tracked
            // so an unmount mid-linger cancels it.
            const linger = setTimeout(() => {
              lingers.current.delete(linger);
              setProgress((prev) => {
                const next = { ...prev };
                delete next[statusBase];
                return next;
              });
            }, SYNC_STATUS_HIDE_MS);
            lingers.current.add(linger);
          }
        } catch {
          /* a missed poll is just the next poll's problem */
        }
      })();
    }, SYNC_POLL_MS);
  }, []);

  const startMixSync = useCallback(
    (mix: DiscoverMix, tracks: unknown[] | undefined): SyncToast | null => {
      const decade = decadeOf(mix);
      if (!tracks || tracks.length === 0) {
        // 2721 / 11952 — different wording per family, both 'warning'.
        return decade !== null
          ? { message: 'No tracks available for this decade', level: 'warning' }
          : {
              message: noTracksToast(playlistDisplayName(mix.syncKey ?? mix.key)),
              level: 'warning',
            };
      }
      const rows = tracks as Record<string, unknown>[];
      const spotifyTracks =
        decade !== null ? rows.map((t) => decadeTrackToSpotify(t, true)) : toSyncTracks(rows);
      const virtualId =
        decade !== null ? `discover_decade_${decade}` : virtualPlaylistId(mix.syncKey ?? mix.key);
      const name = decade !== null ? `${decade}s Classics` : mix.title;
      const statusBase = mix.statusBase ?? (mix.syncKey ? mix.syncKey.replace(/_/g, '-') : mix.key);
      const doneToast =
        decade !== null
          ? decadeSyncCompleteToast(decade)
          : syncCompleteToast(mix.syncKey ?? mix.key);

      void window.startDiscoverVirtualSync?.(virtualId, name, spotifyTracks as unknown[]);
      // Visible immediately, before the first poll answers (2768).
      setProgress((prev) => ({
        ...prev,
        [statusBase]: { total: 0, matched: 0, failed: 0, processed: 0, pending: 0, percentage: 0 },
      }));
      poll(statusBase, virtualId, doneToast);
      return null;
    },
    [poll],
  );

  return {
    progressFor: (statusBase) => progress[statusBase],
    syncingKeys: Object.keys(progress),
    startMixSync,
  };
}
