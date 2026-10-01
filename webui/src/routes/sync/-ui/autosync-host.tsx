/**
 * the auto-sync manager for everywhere that isn't the playlists page.
 *
 * the dashboard's quick actions tile and sync band call
 * window.openAutoSyncScheduleModal, which was still the vanilla modal in
 * auto-sync.js. so the same manager looked and behaved differently depending
 * on where you opened it (no drag glow, old cards, two refresh buttons).
 * mounted at the root, this takes that global over and opens the react one.
 *
 * the react bundle is a module script, so it runs after every classic vanilla
 * script, and this effect runs after that. auto-sync.js can't take it back.
 * the playlists page keeps its own copy, wired to its cards and pipeline.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { useAutoSync, useAutoSyncActions } from '../-sync.use-autosync';
import { useMirroredPipeline } from '../-sync.use-pipeline';
import { AutoSyncModal } from './autosync-modal';

const noop = () => {};

export function AutoSyncHost() {
  const [open, setOpen] = useState(false);

  // the pipeline's reload wants autoSync.refresh, and autoSync wants the
  // pipeline's run. a ref breaks the loop.
  const refreshRef = useRef<() => void>(noop);
  const reload = useCallback(() => refreshRef.current(), []);
  // no cards to paint a phase onto out here, the modal's monitor reads the
  // pipeline state off its own refresh
  const pipeline = useMirroredPipeline({ onState: noop, reload });
  const autoSync = useAutoSync({ open, runPipeline: pipeline.run });
  refreshRef.current = () => void autoSync.refresh();
  const { boardActions, weeklyActions } = useAutoSyncActions(autoSync);

  useEffect(() => {
    const openManager = () => setOpen(true);
    const previous = window.openAutoSyncScheduleModal;
    window.openAutoSyncScheduleModal = openManager;
    return () => {
      if (window.openAutoSyncScheduleModal === openManager) {
        window.openAutoSyncScheduleModal = previous;
      }
    };
  }, []);

  if (!open) return null;
  return (
    <AutoSyncModal
      state={autoSync.state}
      loading={autoSync.loading}
      loadError={autoSync.loadError}
      now={autoSync.now}
      historyFilter={autoSync.historyFilter}
      onHistoryFilterChange={autoSync.setHistoryFilter}
      onLoadMoreHistory={autoSync.loadMoreHistory}
      onRefresh={autoSync.refresh}
      onClose={() => setOpen(false)}
      boardActions={boardActions}
      weeklyActions={weeklyActions}
      onBulkSchedule={autoSync.bulkSchedule}
      onBulkUnschedule={autoSync.bulkUnschedule}
      // the vanilla mirrored modal, still alive for exactly this kind of caller
      onOpenDetails={(playlistId) => void window.openMirroredPlaylistModal?.(playlistId)}
      onRunAgain={(playlistId) => autoSync.runNow(playlistId)}
      onDraggingChange={autoSync.setDragging}
    />
  );
}
