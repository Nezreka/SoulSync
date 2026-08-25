import { useNavigate } from '@tanstack/react-router';
import { useCallback, useState } from 'react';

import type { AdlBatch, AdlDeletedEntry, AdlDownload, AdlQuarantineEntry } from '../-adl.types';
import type { ReviewActionHandlers } from './adl-review';

import { cancelBatch, cancelTask, clearCompleted, setDeletedRetention } from '../-adl.api';
import { verificationHistoryId, unverifiedKey } from '../-adl.helpers';
import { useAdlDownloads } from '../-adl.use-downloads';
import { groupQuarantine, useAdlVerification } from '../-adl.use-verification';
import {
  approveAllQuarantine,
  approveAllUnverified,
  cleanOrphans,
  clearAllQuarantine,
  deleteAllUnverified,
  emptyDeletedBin,
  purgeDeletedEntry,
  restoreAllDeleted,
  restoreDeletedEntry,
  quarantineApproveEntry,
  quarantineAudit,
  quarantineCompare,
  quarantineDeleteEntry,
  quarantinePlayEntry,
  quarantineRecoverEntry,
  reviewableHistoryIds,
  unverifiedApprove,
  unverifiedAudit,
  unverifiedCompare,
  unverifiedDelete,
  unverifiedPlay,
} from '../-adl.verif-actions';
import { AdlBatchPanel } from './adl-batch-panel';
import { AdlHeader } from './adl-header';
import { AdlList, ADL_EMPTY_TEXT, BatchFilterBanner } from './adl-list';
import {
  AdlDeletedList,
  AdlQuarantineList,
  AdlReviewBanner,
  AdlReviewExplainer,
  AdlUnverifiedRow,
} from './adl-review';

const toast = (message: string, type: string) => window.showToast?.(message, type);

export function ActiveDownloadsPage() {
  const navigate = useNavigate();
  const verification = useAdlVerification();
  const [cancelAllPending, setCancelAllPending] = useState(false);

  // The downloads poll drives the quarantine refresh on its 7th tick, so the
  // review queue picks up entries created mid-batch without a click.
  const downloads = useAdlDownloads({
    onQuarantineRefresh: () => void verification.loadQuarantine(true),
  });

  const { state, visible, counts, hasRunningWork } = downloads;
  const reviewing = state.filter === 'unverified';

  const refresh = useCallback(() => void downloads.refresh(), [downloads]);
  const refreshQuarantine = useCallback(
    () => void verification.loadQuarantine(true),
    [verification],
  );
  const refreshDeleted = useCallback(() => void verification.loadDeleted(true), [verification]);
  const onKeepDays = useCallback(
    async (days: number) => {
      try {
        const data = await setDeletedRetention(days);
        if (data.success) {
          toast(days ? `Auto-delete after ${days} days` : 'Keeping deleted files forever', 'success');
        } else {
          toast(data.error || 'Could not save retention', 'error');
        }
      } catch {
        toast('Could not save retention', 'error');
      }
      refreshDeleted();
    },
    [refreshDeleted],
  );
  const deletedHandlers = useCallback(
    (entry: AdlDeletedEntry) => ({
      onRestore: () => void restoreDeletedEntry(entry, refreshDeleted),
      onPurge: () => void purgeDeletedEntry(entry, refreshDeleted),
    }),
    [refreshDeleted],
  );

  const onCancelRow = useCallback(
    async (dl: AdlDownload) => {
      if (!dl.playlist_id || dl.track_index === undefined || dl.track_index === null) {
        toast('Cannot cancel — missing task coordinates', 'error');
        return;
      }
      try {
        const data = await cancelTask(dl.playlist_id, dl.track_index);
        if (data.success) {
          toast(`Cancelled "${data.task_info?.track_name || 'Track'}"`, 'info');
          refresh();
        } else toast(data.error || 'Cancel failed', 'error');
      } catch {
        toast('Cancel request failed', 'error');
      }
    },
    [refresh],
  );

  const onClearCompleted = useCallback(async () => {
    // This also wipes the persisted history tail and the review queue, so it
    // is confirmed rather than instant.
    const confirmed = await window.showConfirmDialog?.({
      title: 'Clear Completed',
      message:
        'Remove ALL completed and failed downloads from the list and history? ' +
        'This also clears unverified items from the verification queue. ' +
        'Your files stay in the library — only the download-history rows are removed.',
      confirmText: 'Clear',
      destructive: true,
    });
    if (!confirmed) return;
    try {
      const data = await clearCompleted();
      if (data.success) {
        toast(`Cleared ${data.total_cleared ?? data.cleared ?? 0} downloads`, 'success');
        refresh();
      }
    } catch {
      toast('Failed to clear downloads', 'error');
    }
  }, [refresh]);

  const onCancelBatch = useCallback(
    async (batch: AdlBatch) => {
      const confirmed = await window.showConfirmDialog?.({
        title: 'Cancel Batch',
        message: `Cancel "${batch.batch_name || 'this batch'}"? All active and queued downloads in this batch will be stopped.`,
        confirmText: 'Cancel Batch',
        destructive: true,
      });
      if (!confirmed) return;
      try {
        const data = await cancelBatch(batch.batch_id);
        if (data.success) {
          toast(`Cancelled ${data.cancelled_tasks ?? 0} downloads`, 'info');
          refresh();
        } else toast(data.error || 'Failed to cancel batch', 'error');
      } catch {
        toast('Failed to cancel batch', 'error');
      }
    },
    [refresh],
  );

  /**
   * Cancel every batch with work in it.
   *
   * Sequential on purpose: cancel_batch takes a server-side lock, so parallel
   * calls would serialise anyway while making the failure reporting muddier.
   */
  const onCancelAll = useCallback(async () => {
    const running = state.batches.filter((b) => (b.active || 0) > 0 || (b.queued || 0) > 0);
    if (!running.length) {
      toast('No active batches to cancel', 'info');
      return;
    }
    const totalTasks = running.reduce((sum, b) => sum + (b.active || 0) + (b.queued || 0), 0);
    const confirmed = await window.showConfirmDialog?.({
      title: 'Cancel All Downloads',
      message: `Cancel ${totalTasks} ${totalTasks === 1 ? 'task' : 'tasks'} across ${running.length} ${running.length === 1 ? 'batch' : 'batches'}? Active and queued downloads will be stopped and added to the wishlist.`,
      confirmText: 'Cancel All',
      destructive: true,
    });
    if (!confirmed) return;

    setCancelAllPending(true);
    let cancelled = 0;
    let failed = 0;
    for (const batch of running) {
      try {
        const data = await cancelBatch(batch.batch_id);
        if (data.success) cancelled += data.cancelled_tasks ?? 0;
        else failed += 1;
      } catch {
        failed += 1;
      }
    }
    setCancelAllPending(false);

    if (cancelled > 0 && failed === 0) toast(`Cancelled ${cancelled} downloads`, 'success');
    else if (cancelled > 0)
      toast(`Cancelled ${cancelled} downloads (${failed} batches failed)`, 'info');
    else toast('Failed to cancel any downloads', 'error');
    refresh();
  }, [state.batches, refresh]);

  /** Handlers for one unverified row, or null when it has no id to act on. */
  const unverifiedHandlers = useCallback(
    (dl: AdlDownload): ReviewActionHandlers | null => {
      const historyId = verificationHistoryId(dl);
      if (!historyId) return null;
      return {
        onPlay: () => void unverifiedPlay(dl, historyId),
        onCompare: (setBusy) => void unverifiedCompare(historyId, setBusy),
        onAudit: () => void unverifiedAudit(historyId),
        onApprove: () => void unverifiedApprove(historyId, refresh),
        onDelete: () => void unverifiedDelete(historyId, refresh),
      };
    },
    [refresh],
  );

  const quarantineHandlers = useCallback(
    (entry: AdlQuarantineEntry): ReviewActionHandlers => ({
      onPlay: () => void quarantinePlayEntry(entry),
      onCompare: (setBusy) => void quarantineCompare(entry, setBusy),
      onAudit: () => void quarantineAudit(entry),
      // Legacy sidecars have no context to re-import from; recovery is it.
      onApprove: () =>
        void (entry.has_full_context
          ? quarantineApproveEntry(entry, refreshQuarantine)
          : quarantineRecoverEntry(entry, refreshQuarantine)),
      onDelete: () => void quarantineDeleteEntry(entry, refreshQuarantine),
    }),
    [refreshQuarantine],
  );

  const filteredBatch = state.filterBatchId
    ? state.batches.find((b) => b.batch_id === state.filterBatchId)
    : null;

  const showQuarantine = reviewing && verification.state.subView === 'quarantine';
  const showDeleted = reviewing && verification.state.subView === 'deleted';
  const quarantineGroups = showQuarantine ? groupQuarantine(verification.state.quarantine) : [];
  const deletedEntries = verification.state.deleted?.entries ?? [];

  return (
    <div className="adl-layout">
      <div className="adl-main">
        <div className="adl-container">
          <AdlHeader
            filter={state.filter}
            counts={counts}
            hasRunningWork={hasRunningWork}
            acoustidEnabled={verification.acoustidEnabled}
            reviewCount={verification.state.summary?.total ?? null}
            onFilter={downloads.setFilter}
            onCancelAll={() => void onCancelAll()}
            onClearCompleted={() => void onClearCompleted()}
            cancelAllPending={cancelAllPending}
          />

          {state.filterBatchId ? (
            <BatchFilterBanner
              batchId={state.filterBatchId}
              batchName={filteredBatch?.batch_name || 'Unknown batch'}
              onClear={() => downloads.toggleBatchFilter(state.filterBatchId as string)}
            />
          ) : null}

          {reviewing ? (
            <AdlReviewBanner
              subView={verification.state.subView}
              acoustidEnabled={verification.acoustidEnabled}
              unverifiedCount={visible.length}
              quarantineCount={
                verification.state.summary?.quarantine ?? verification.state.quarantine.length
              }
              quarantineLoaded={
                verification.state.quarantineLoaded || verification.state.summary !== null
              }
              onSubView={verification.setSubView}
              onApproveAll={() =>
                void approveAllUnverified(reviewableHistoryIds(state.downloads), refresh)
              }
              onCleanOrphans={() => void cleanOrphans(refresh)}
              onDeleteAll={() =>
                void deleteAllUnverified(reviewableHistoryIds(state.downloads), refresh)
              }
              onQuarantineApproveAll={() =>
                void approveAllQuarantine(verification.state.quarantine, refreshQuarantine)
              }
              onQuarantineClearAll={() =>
                void clearAllQuarantine(verification.state.quarantine, refreshQuarantine)
              }
              deletedCount={verification.state.deletedLoaded ? deletedEntries.length : null}
              onRestoreAllDeleted={() => void restoreAllDeleted(deletedEntries, refreshDeleted)}
              onEmptyDeleted={() => void emptyDeletedBin(deletedEntries.length, refreshDeleted)}
            />
          ) : null}

          {reviewing ? <AdlReviewExplainer subView={verification.state.subView} /> : null}

          {showDeleted ? (
            <div className="adl-list" id="adl-list">
              <AdlDeletedList
                entries={deletedEntries}
                totalSize={verification.state.deleted?.total_size ?? 0}
                loaded={verification.state.deletedLoaded}
                keepDays={verification.state.deleted?.keep_days ?? 0}
                handlersFor={deletedHandlers}
                onKeepDays={(days) => void onKeepDays(days)}
              />
            </div>
          ) : showQuarantine ? (
            <div className="adl-list" id="adl-list">
              {!verification.state.quarantineLoaded ? (
                <div className="adl-section-header">Loading quarantine…</div>
              ) : quarantineGroups.length === 0 ? (
                <div className="adl-empty" id="adl-empty">
                  {ADL_EMPTY_TEXT}
                </div>
              ) : (
                <AdlQuarantineList
                  groups={quarantineGroups}
                  openDetails={verification.state.openQuarantine}
                  openGroups={verification.state.openGroups}
                  onToggleDetails={verification.toggleQuarantine}
                  onToggleGroup={verification.toggleGroup}
                  handlersFor={quarantineHandlers}
                />
              )}
            </div>
          ) : reviewing ? (
            <div className="adl-list" id="adl-list">
              {visible.length === 0 ? (
                <div className="adl-empty" id="adl-empty">
                  {ADL_EMPTY_TEXT}
                </div>
              ) : (
                visible.map((dl) => (
                  <AdlUnverifiedRow
                    key={unverifiedKey(dl)}
                    dl={dl}
                    open={verification.state.openUnverified.has(unverifiedKey(dl))}
                    onToggle={() => verification.toggleUnverified(unverifiedKey(dl))}
                    handlers={unverifiedHandlers(dl)}
                  />
                ))
              )}
            </div>
          ) : (
            // Passed through, not wrapped in `void`: the row awaits it to keep
            // its cancel button locked until the request settles.
            <AdlList rows={visible} filter={state.filter} onCancel={onCancelRow} />
          )}
        </div>
      </div>

      <AdlBatchPanel
        batches={downloads.visibleBatches}
        downloads={state.downloads}
        history={state.batchHistory}
        expandedBatches={state.expandedBatches}
        filterBatchId={state.filterBatchId}
        batchOpacity={downloads.batchOpacity}
        samplesFor={downloads.rateSamplesFor}
        onToggleBatch={downloads.toggleBatchExpanded}
        onFilterBatch={downloads.toggleBatchFilter}
        onCancelBatch={(batch) => void onCancelBatch(batch)}
        onOpenBatchModal={(batch) =>
          window.openDownloadBatchModal?.(
            batch.batch_id,
            batch.playlist_id || '',
            batch.batch_name || 'Download',
          )
        }
        onOpenFullHistory={() => window.openLibraryHistoryModal?.()}
        // The empty-panel links are in-app routes; the router owns those,
        // not the legacy shell navigator.
        onNavigate={(page) => void navigate({ to: `/${page}` })}
      />
    </div>
  );
}
