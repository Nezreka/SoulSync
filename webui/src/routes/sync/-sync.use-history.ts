/**
 * Sync history — loading it, and re-running one row.
 *
 * The list half is ordinary: a page, a source filter, a delete. The re-sync
 * half is the modal's only live machinery, and it is why this is a hook and not
 * a render prop. Starting a re-sync creates a NEW sync under a synthetic
 * playlist id and then polls `/api/sync/status/<that id>` every 2s until it
 * finishes, is cancelled, or errors.
 *
 * WHY A SYNTHETIC ID. The re-sync is not the original run and must not collide
 * with it — the original's id may still be live, or may be reused by a
 * scheduled run starting in the same moment. `resync_<entryId>_<startedAt>`
 * cannot collide with either.
 *
 * Every interval is registered so unmount clears it. The vanilla kept its
 * intervals in a module-level map (`_activeSyncHistoryResyncs`), which survived
 * the modal closing and went on polling a screen that was gone.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { SyncHistoryEntry, SyncHistoryProgress } from './-sync.history';

import {
  cancelSync,
  deleteSyncHistoryEntry,
  fetchAccountSyncStatus,
  fetchSyncHistory,
  fetchSyncHistoryEntry,
  startSync,
} from './-sync.api';
import {
  syncHistoryProgress,
  syncHistoryResyncKind,
  syncHistoryResyncTracks,
  syncHistoryVisibleEntries,
} from './-sync.history';

const PAGE_SIZE = 20;
const POLL_MS = 2000;
/** How long a finished row keeps its result on screen before collapsing. */
const FINISHED_LINGER_MS = 5000;
const ENDED_LINGER_MS = 3000;

export interface SyncHistoryResync {
  /** The synthetic id the re-sync runs under. */
  syncPlaylistId: string;
  progress: SyncHistoryProgress;
}

export interface UseSyncHistoryOptions {
  /** Open, so it does not poll or fetch behind a closed modal. */
  active: boolean;
  toast?: (message: string, kind: string) => void;
  /** Injected for tests; the real one is the vanilla download modal. */
  openDownloadModal?: (entry: SyncHistoryEntry) => Promise<void> | void;
  /** Injected for tests. */
  now?: () => number;
}

export function useSyncHistory(options: UseSyncHistoryOptions) {
  const { active } = options;
  const [entries, setEntries] = useState<SyncHistoryEntry[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [source, setSource] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [resyncs, setResyncs] = useState<Record<number, SyncHistoryResync>>({});

  const timers = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const toast = useCallback((message: string, kind: string) => {
    const fn = optionsRef.current.toast ?? ((m: string, k: string) => window.showToast?.(m, k));
    fn(message, kind);
  }, []);

  const stopPolling = useCallback((entryId: number) => {
    const timer = timers.current.get(entryId);
    if (timer !== undefined) clearInterval(timer);
    timers.current.delete(entryId);
  }, []);

  /** Clear every interval on unmount — see the note at the top of this file. */
  useEffect(() => {
    const registry = timers.current;
    return () => {
      for (const timer of registry.values()) clearInterval(timer);
      registry.clear();
    };
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchSyncHistory(page, PAGE_SIZE, source);
      setEntries(syncHistoryVisibleEntries(data.entries));
      // Stats describe the WHOLE history, so they hold across filters and the
      // tab strip does not reshuffle every time you pick a source.
      if (data.stats) setStats(data.stats);
      setTotal(data.total ?? 0);
    } catch {
      setError('Error loading sync history');
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [page, source]);

  useEffect(() => {
    if (!active) return;
    void load();
  }, [active, load]);

  /** Reopening starts at the top, as the vanilla did (3914-3915). */
  useEffect(() => {
    if (active) return;
    setPage(1);
    setSource(null);
  }, [active]);

  const selectSource = useCallback((next: string | null) => {
    setSource(next);
    setPage(1);
  }, []);

  const goToPage = useCallback((next: number) => {
    if (next < 1) return;
    setPage(next);
  }, []);

  const remove = useCallback(
    async (entryId: number) => {
      try {
        const data = await deleteSyncHistoryEntry(entryId);
        if (!data.success) {
          toast('Failed to delete entry', 'error');
          return;
        }
        // Dropped locally rather than refetched: a refetch would pull the next
        // page's first row up under the cursor mid-click.
        setEntries((prev) => prev.filter((e) => e.id !== entryId));
        setTotal((prev) => Math.max(0, prev - 1));
      } catch {
        toast('Failed to delete entry', 'error');
      }
    },
    [toast],
  );

  const poll = useCallback(
    (entryId: number, syncPlaylistId: string) => {
      const timer = setInterval(() => {
        void (async () => {
          try {
            const state = await fetchAccountSyncStatus(syncPlaylistId);
            const progress = syncHistoryProgress(state);
            setResyncs((prev) =>
              prev[entryId] ? { ...prev, [entryId]: { syncPlaylistId, progress } } : prev,
            );

            if (progress.phase === 'running') return;

            stopPolling(entryId);
            if (progress.phase === 'finished') {
              toast(`Re-sync complete: ${progress.matched}/${progress.total} matched`, 'success');
            }
            const linger = progress.phase === 'finished' ? FINISHED_LINGER_MS : ENDED_LINGER_MS;
            setTimeout(() => {
              setResyncs((prev) => {
                const next = { ...prev };
                delete next[entryId];
                return next;
              });
            }, linger);
          } catch {
            stopPolling(entryId);
            setResyncs((prev) => {
              const next = { ...prev };
              delete next[entryId];
              return next;
            });
          }
        })();
      }, POLL_MS);
      timers.current.set(entryId, timer);
    },
    [stopPolling, toast],
  );

  const resync = useCallback(
    async (entryId: number) => {
      let entry: SyncHistoryEntry | undefined;
      try {
        const data = await fetchSyncHistoryEntry(entryId);
        if (!data.success || !data.entry) {
          toast('Failed to load sync data', 'error');
          return;
        }
        entry = data.entry;
      } catch {
        toast('Error loading sync data', 'error');
        return;
      }

      // Two different actions, not two flavours of one: see syncHistoryResyncKind.
      if (syncHistoryResyncKind(entry) === 'download') {
        await optionsRef.current.openDownloadModal?.(entry);
        return;
      }

      const nowFn = optionsRef.current.now ?? Date.now;
      const syncPlaylistId = `resync_${entryId}_${nowFn()}`;
      setResyncs((prev) => ({
        ...prev,
        [entryId]: {
          syncPlaylistId,
          progress: {
            percent: 0,
            step: 'Starting sync…',
            matched: 0,
            failed: 0,
            total: 0,
            phase: 'running',
          },
        },
      }));

      try {
        const result = await startSync({
          playlist_id: syncPlaylistId,
          playlist_name: entry.playlist_name ?? '',
          tracks: syncHistoryResyncTracks(entry.tracks),
        });
        if (!result.success) {
          toast(`Sync failed: ${result.error || 'Unknown error'}`, 'error');
          setResyncs((prev) => {
            const next = { ...prev };
            delete next[entryId];
            return next;
          });
          return;
        }
        poll(entryId, syncPlaylistId);
      } catch {
        toast('Failed to start sync', 'error');
        setResyncs((prev) => {
          const next = { ...prev };
          delete next[entryId];
          return next;
        });
      }
    },
    [poll, toast],
  );

  const cancel = useCallback(
    async (entryId: number) => {
      const running = resyncs[entryId];
      if (!running) return;
      try {
        await cancelSync(running.syncPlaylistId);
        // The row says "Cancelling…" and the poll reports the real end state;
        // tearing it down here would hide a cancel the server declined.
        setResyncs((prev) =>
          prev[entryId]
            ? {
                ...prev,
                [entryId]: {
                  ...prev[entryId],
                  progress: { ...prev[entryId].progress, step: 'Cancelling…' },
                },
              }
            : prev,
        );
      } catch {
        toast('Failed to cancel sync', 'error');
      }
    },
    [resyncs, toast],
  );

  return {
    entries,
    stats,
    total,
    page,
    pageSize: PAGE_SIZE,
    source,
    loading,
    error,
    resyncs,
    selectSource,
    goToPage,
    reload: load,
    remove,
    resync,
    cancel,
  };
}
