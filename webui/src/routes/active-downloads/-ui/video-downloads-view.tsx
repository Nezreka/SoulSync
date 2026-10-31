/**
 * The unified downloads page's VIDEO view.
 *
 * One list-component shell (VdlHeader / VdlList / VdlReviewPane /
 * VdlHistoryModal) + per-media data adapters (useVideoDownloads /
 * useVdlReview) hitting the existing /api/video/downloads/* endpoints.
 * The Clients pane reuses the shared AdlClientsTab — same /api/clients/*
 * blueprint the music side uses.
 */

import { useCallback, useEffect, useState } from 'react';

import type { VdlCardActions } from './vdl-list';

import { fetchVideoHistoryCount } from '../-vdl.api';
import { useVideoDownloads } from '../-vdl.use-downloads';
import { AdlClientsTab } from './adl-clients';
import { useReportActive } from './dl-header';
import { VdlHeader } from './vdl-header';
import { VdlHistoryModal } from './vdl-history';
import { VdlList } from './vdl-list';
import { VdlReviewPane, useVdlReview } from './vdl-review';

const toast = (message: string, type: string) => window.showToast?.(message, type);

export function VideoDownloadsView() {
  const dl = useVideoDownloads();
  // the Music | Video switch shows this side's live count
  useReportActive('video', dl.counts.active);
  const review = useVdlReview();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyCount, setHistoryCount] = useState<number | null>(null);

  const refreshHistoryCount = useCallback(async () => {
    setHistoryCount(await fetchVideoHistoryCount());
  }, []);

  /* header badges: cheap counts on mount, no poll */
  useEffect(() => {
    void review.loadBadge();
    void refreshHistoryCount();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* the review pane loads its data when opened, like the old onView('review') */
  useEffect(() => {
    if (dl.view === 'review') void review.loadReview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dl.view]);

  const cardActions: VdlCardActions = {
    onCancel: (id) => void dl.cancelOne(id),
    onRetry: (id) => void dl.retryOne(id),
    onBlock: (d) => void dl.blockOne(d),
    onRemove: (id) => void dl.removeOne(id),
    onToggle: dl.toggleExpanded,
  };

  const onCancelGroup = useCallback(
    (ids: number[]) => {
      void Promise.all(ids.map((id) => dl.cancelOne(id))).then(() => {
        toast(`Cancelled ${ids.length} download${ids.length === 1 ? '' : 's'}`, 'info');
        void dl.refresh();
      });
    },
    [dl],
  );

  return (
    <div className="adl-layout" data-vdl-root>
      <div className="adl-main">
        <div className="adl-container">
          <VdlHeader
            view={dl.view}
            onView={dl.setView}
            filter={dl.filter}
            onFilter={dl.setFilter}
            downloads={dl.downloads}
            counts={dl.counts}
            liveSpeedBps={dl.liveSpeedBps}
            hasRunningWork={dl.hasRunningWork}
            retryableCount={dl.retryableCount}
            reviewCount={review.binCount}
            historyCount={historyCount}
            onOpenHistory={() => setHistoryOpen(true)}
            onCancelAll={() => void dl.cancelAll()}
            onRetryAll={() => void dl.retryAllFailed()}
            onClearFinished={() => void dl.clearFinished()}
            onClearFailed={() => void dl.clearFailed()}
            retryAllPending={dl.retryAllPending}
          />

          {dl.view === 'clients' ? (
            <AdlClientsTab />
          ) : dl.view === 'review' ? (
            <VdlReviewPane review={review} onChanged={() => void review.loadReview()} />
          ) : (
            <VdlList
              downloads={dl.downloads}
              filter={dl.filter}
              loaded={dl.loaded}
              expanded={dl.expanded}
              collapsedGroups={dl.collapsedGroups}
              metaFor={dl.metaFor}
              ensureMeta={dl.ensureMeta}
              onToggleGroup={dl.toggleGroup}
              cardActions={cardActions}
              onCancelGroup={onCancelGroup}
            />
          )}

          <VdlHistoryModal
            open={historyOpen}
            onClose={() => setHistoryOpen(false)}
            onCountChange={setHistoryCount}
          />
        </div>
      </div>
    </div>
  );
}
