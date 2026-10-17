/**
 * Root-owned Auto Sync manager and pipeline bridge for classic dialogs.
 * The playlists page injects the same controller hook into its own manager.
 * Only classic card-state painting remains delegated to auto-sync.js.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { MirroredPipelineState } from '../-sync.pipeline';

import { useAutoSync, useAutoSyncActions } from '../-sync.use-autosync';
import { useMirroredPipeline } from '../-sync.use-pipeline';
import { AutoSyncModal } from './autosync-modal';

const noop = () => {};

export function AutoSyncHost() {
  const [open, setOpen] = useState(false);

  // the pipeline's reload wants autoSync.refresh, and autoSync wants the
  // pipeline's run. a ref breaks the loop.
  const refreshRef = useRef<() => void>(noop);
  const openRef = useRef(open);
  openRef.current = open;
  const reload = useCallback(() => {
    if (window.reloadMirroredTab) window.reloadMirroredTab();
    else void window.loadMirroredPlaylists?.();
    if (openRef.current) refreshRef.current();
  }, []);
  const onState = useCallback((playlistId: number, state: MirroredPipelineState) => {
    window.applyMirroredPipelineState?.(playlistId, state);
  }, []);
  const pipeline = useMirroredPipeline({ onState, reload });
  const autoSync = useAutoSync({ open, runPipeline: pipeline.run });
  refreshRef.current = () => void autoSync.refresh();
  const { boardActions, weeklyActions } = useAutoSyncActions(autoSync);

  useEffect(() => {
    const openManager = () => setOpen(true);
    const run = async (playlistId: number, name: string, skipWishlist = false) => {
      await pipeline.run(playlistId, name, { skipWishlist });
      if (openRef.current) refreshRef.current();
    };
    const previousOpen = window.openAutoSyncScheduleModal;
    const previousRun = window.runMirroredPlaylistPipeline;
    const previousPoll = window.pollMirroredPipelineStatus;
    window.openAutoSyncScheduleModal = openManager;
    window.runMirroredPlaylistPipeline = run;
    window.pollMirroredPipelineStatus = pipeline.resume;
    return () => {
      if (window.openAutoSyncScheduleModal === openManager) {
        window.openAutoSyncScheduleModal = previousOpen;
      }
      if (window.runMirroredPlaylistPipeline === run) {
        window.runMirroredPlaylistPipeline = previousRun;
      }
      if (window.pollMirroredPipelineStatus === pipeline.resume) {
        window.pollMirroredPipelineStatus = previousPoll;
      }
    };
  }, [pipeline]);

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
