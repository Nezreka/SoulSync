/**
 * The unified downloads page's VIDEO view.
 *
 * One list-component shell (VdlHeader / VdlList / VdlReviewPane /
 * VdlHistoryModal) + per-media data adapters (useVideoDownloads /
 * useVdlReview) hitting the existing /api/video/downloads/* endpoints.
 * The Clients pane reuses the shared AdlClientsTab — same /api/clients/*
 * blueprint the music side uses.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { DialogFrame, DialogHeader } from '@/components/dialog';

import type { VdlDownload } from '../-vdl.types';
import type { VdlCardActions } from './vdl-list';

import { fetchVideoHistoryCount, type VdlRemoveOptions } from '../-vdl.api';
import { vdlCanBlock, vdlIsFail } from '../-vdl.helpers';
import { useVideoDownloads } from '../-vdl.use-downloads';
import { AdlClientsTab } from './adl-clients';
import { useReportActive } from './dl-header';
import { useOpenRecycleBin } from './use-open-recycle-bin';
import { VdlHeader } from './vdl-header';
import { VdlHistoryModal } from './vdl-history';
import { VdlList, useVdlCanImport } from './vdl-list';
import { VdlReviewPane, showVideoRecycleBin, useVdlReview } from './vdl-review';

const toast = (message: string, type: string) => window.showToast?.(message, type);

type DismissTarget =
  | { mode: 'one'; row: VdlDownload }
  | { mode: 'selected'; rows: VdlDownload[] }
  | { mode: 'all'; rows: VdlDownload[] };

function DismissFailedDialog({
  target,
  onCancel,
  onConfirm,
}: {
  target: DismissTarget;
  onCancel: () => void;
  onConfirm: (opts: VdlRemoveOptions) => void;
}) {
  const rows = target.mode === 'one' ? [target.row] : target.rows;
  const hasFiles = rows.some((r) => Boolean(r.dest_path));
  const hasBlockable = rows.some((r) => vdlCanBlock(r));
  const [deleteFiles, setDeleteFiles] = useState(false);
  const [blockRelease, setBlockRelease] = useState(false);

  const heading =
    target.mode === 'all'
      ? `Clear all ${rows.length} failed downloads?`
      : target.mode === 'one'
        ? 'Remove failed download?'
        : `Remove ${rows.length} failed download${rows.length === 1 ? '' : 's'}?`;

  return (
    <DialogFrame
      open
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
      aria-label={heading}
    >
      <DialogHeader title={heading} />
      <div style={{ padding: '12px 20px 20px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted, rgba(255,255,255,0.68))' }}>
          This clears the failed queue entries only. Your requests, wishlist entries, monitoring and
          download history stay untouched.
        </p>
        {hasFiles ? (
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={deleteFiles}
              onChange={(e) => setDeleteFiles(e.target.checked)}
            />
            <span>Also delete associated downloaded files on disk</span>
          </label>
        ) : null}
        {hasBlockable ? (
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={blockRelease}
              onChange={(e) => setBlockRelease(e.target.checked)}
            />
            <span>Block {rows.length === 1 ? 'this release' : 'these releases'} so they aren&apos;t grabbed again</span>
          </label>
        ) : null}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
          <button type="button" className="dlc-btn" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="dlc-btn dlc-btn-danger"
            onClick={() =>
              onConfirm({
                include_import_failed: true,
                delete_files: deleteFiles,
                block_release: blockRelease,
              })
            }
          >
            {target.mode === 'all' ? 'Clear all failed' : 'Remove'}
          </button>
        </div>
      </div>
    </DialogFrame>
  );
}

export function VideoDownloadsView() {
  const dl = useVideoDownloads();
  // blocking writes the household's blocklist, and clearing the list, the
  // recycle bin and the history are the household's too: admin only, like
  // the server. a member's list is the grabs they started
  const canBlockAll = useVdlCanImport();
  const isAdmin = canBlockAll;
  // the Music | Video switch shows this side's live count
  useReportActive('video', dl.counts.active);
  const review = useVdlReview();
  const [historyOpen, setHistoryOpen] = useState(false);
  // #2128: the Library's recycle bin button: Review, on the bin
  useOpenRecycleBin(() => {
    if (!isAdmin) return;
    showVideoRecycleBin();
    dl.setView('review');
  });
  // #2029: only one video library's downloads; offered once two show up
  const [library, setLibrary] = useState('');
  const libraryNames = useMemo(
    () =>
      Array.from(
        new Set(dl.downloads.map((d) => d.video_library_name).filter((n): n is string => !!n)),
      ).sort(),
    [dl.downloads],
  );
  const shownLibrary = libraryNames.includes(library) ? library : '';
  const listed = shownLibrary
    ? dl.downloads.filter((d) => d.video_library_name === shownLibrary)
    : dl.downloads;
  const [historyCount, setHistoryCount] = useState<number | null>(null);
  const [selectedFailed, setSelectedFailed] = useState<Set<number>>(() => new Set());
  const [dismissTarget, setDismissTarget] = useState<DismissTarget | null>(null);

  const failedRows = useMemo(() => listed.filter((d) => vdlIsFail(d.status)), [listed]);
  const activeSelectedIds = useMemo(() => {
    const valid = new Set(failedRows.map((d) => d.id));
    const out = new Set<number>();
    selectedFailed.forEach((id) => {
      if (valid.has(id)) out.add(id);
    });
    return out;
  }, [failedRows, selectedFailed]);

  const toggleSelectFailed = useCallback((id: number) => {
    setSelectedFailed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

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
    onRemove: (id) => {
      const row = dl.downloads.find((d) => d.id === id);
      if (row && (row.status === 'import_failed' || row.dest_path)) {
        setDismissTarget({ mode: 'one', row });
        return;
      }
      void dl.removeOne(id);
    },
    onToggle: dl.toggleExpanded,
    onSelectFailed: isAdmin ? toggleSelectFailed : undefined,
  };

  const handleClearFailed = useCallback(() => {
    const allFailed = dl.downloads.filter((d) => vdlIsFail(d.status));
    if (allFailed.some((d) => d.status === 'import_failed' || d.dest_path)) {
      setDismissTarget({ mode: 'all', rows: allFailed });
      return;
    }
    void dl.clearFailed();
  }, [dl]);

  const handleConfirmDismiss = useCallback(
    (opts: VdlRemoveOptions) => {
      const target = dismissTarget;
      setDismissTarget(null);
      if (!target) return;
      if (target.mode === 'one') {
        void dl.removeOne(target.row.id, opts);
      } else if (target.mode === 'selected') {
        const ids = target.rows.map((r) => r.id);
        setSelectedFailed(new Set());
        void dl.removeBulk(ids, opts);
      } else {
        setSelectedFailed(new Set());
        void dl.clearFailed(opts);
      }
    },
    [dismissTarget, dl],
  );

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
    <div className="pgf-page adl-layout" data-vdl-root>
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
            blockableFailedCount={canBlockAll ? dl.blockableFailedCount : 0}
            onBlockRetryAll={() => void dl.blockAndRetryAllFailed()}
            onClearFinished={() => void dl.clearFinished()}
            onClearFailed={handleClearFailed}
            onClearEverything={() => void dl.clearEverything()}
            retryAllPending={dl.retryAllPending}
            canManage={isAdmin}
          />

          {dl.view === 'clients' ? (
            <AdlClientsTab />
          ) : dl.view === 'review' && isAdmin ? (
            <VdlReviewPane review={review} onChanged={() => void review.loadReview()} />
          ) : (
            <>
              {libraryNames.length > 1 ? (
                <label className="vdl-library-filter">
                  Library{' '}
                  <select
                    aria-label="Show one library's downloads"
                    value={shownLibrary}
                    onChange={(event) => setLibrary(event.target.value)}
                  >
                    <option value="">All</option>
                    {libraryNames.map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              {isAdmin && failedRows.length > 0 && (dl.filter === 'failed' || activeSelectedIds.size > 0) ? (
                <div
                  className="vdl-failed-bar"
                  data-vdl-failed-bar
                  style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    alignItems: 'center',
                    gap: 8,
                    padding: '10px 14px',
                    marginBottom: 12,
                    borderRadius: 'var(--radius-md)',
                    background: 'var(--white-a04)',
                    border: '1px solid var(--white-a08)',
                    fontSize: 13,
                  }}
                >
                  <span style={{ fontWeight: 600 }}>
                    {activeSelectedIds.size > 0
                      ? `${activeSelectedIds.size} of ${failedRows.length} failed selected`
                      : `${failedRows.length} failed download${failedRows.length === 1 ? '' : 's'}`}
                  </span>
                  <button
                    type="button"
                    className="dlc-btn"
                    onClick={() =>
                      setSelectedFailed(
                        activeSelectedIds.size === failedRows.length
                          ? new Set()
                          : new Set(failedRows.map((r) => r.id)),
                      )
                    }
                  >
                    {activeSelectedIds.size === failedRows.length ? 'Clear selection' : 'Select all failed'}
                  </button>
                  {activeSelectedIds.size > 0 ? (
                    <button
                      type="button"
                      className="dlc-btn dlc-btn-danger"
                      onClick={() =>
                        setDismissTarget({
                          mode: 'selected',
                          rows: failedRows.filter((r) => activeSelectedIds.has(r.id)),
                        })
                      }
                    >
                      Remove selected ({activeSelectedIds.size})
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="dlc-btn dlc-btn-danger"
                    style={{ marginLeft: 'auto' }}
                    onClick={() => setDismissTarget({ mode: 'all', rows: failedRows })}
                  >
                    Clear all failed ({failedRows.length})
                  </button>
                </div>
              ) : null}
              <VdlList
                downloads={listed}
                filter={dl.filter}
                loaded={dl.loaded}
                expanded={dl.expanded}
                collapsedGroups={dl.collapsedGroups}
                metaFor={dl.metaFor}
                ensureMeta={dl.ensureMeta}
                onToggleGroup={dl.toggleGroup}
                cardActions={cardActions}
                onCancelGroup={onCancelGroup}
                selectedFailed={activeSelectedIds}
              />
            </>
          )}

          {dismissTarget ? (
            <DismissFailedDialog
              target={dismissTarget}
              onCancel={() => setDismissTarget(null)}
              onConfirm={handleConfirmDismiss}
            />
          ) : null}

          <VdlHistoryModal
            open={historyOpen}
            onClose={() => setHistoryOpen(false)}
            onCountChange={setHistoryCount}
            canManage={isAdmin}
          />
        </div>
      </div>
    </div>
  );
}
