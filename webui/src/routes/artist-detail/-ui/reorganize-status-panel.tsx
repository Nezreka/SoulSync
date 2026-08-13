import { useEffect, useState } from 'react';

import type { ReorganizeQueueItem, ReorganizeSnapshot } from '../-artist-detail.reorganize';

import {
  cancelReorganizeQueueItemRequest,
  classifyReorganizeOutcome,
  clearReorganizeQueueRequest,
  formatReorganizeResultMessage,
  isCrossArtist,
  paintQueuedAlbumButtons,
  reorgDisplayLabel,
  startReorganizeQueuePolling,
  stopReorganizeQueuePolling,
} from '../-artist-detail.reorganize';

/**
 * The live reorganize-queue panel (library.js:6349-6658). Sits first in the
 * artist meta panel's action row; visible only when there is an active item,
 * queued items, or a completion within the last 20 seconds. Fast/slow polling
 * and the debounced view reload live in the controller — this component just
 * starts it on mount, stops it on unmount, and renders snapshots.
 */
export function ReorganizeStatusPanel({
  artistId,
  onReload,
}: {
  artistId: unknown;
  /** The enhanced view re-fetches after a batch for THIS artist finishes. */
  onReload: () => void;
}) {
  const [snapshot, setSnapshot] = useState<ReorganizeSnapshot | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    startReorganizeQueuePolling(artistId, { onSnapshot: setSnapshot, onReload });
    return () => stopReorganizeQueuePolling();
    // Remounted per artist; the callbacks never change meaningfully.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artistId]);

  // The per-album Reorganize buttons show queued/running state; repainted on
  // every snapshot, exactly as the vanilla repainted on every poll tick.
  useEffect(() => {
    paintQueuedAlbumButtons(snapshot);
  }, [snapshot]);

  if (!snapshot) return null;
  const active = snapshot.active;
  const queued = snapshot.queued || [];
  const recent = snapshot.recent || [];
  const cutoffSec = Date.now() / 1000 - 20;
  const recentVisible = recent.filter((r) => (r.finished_at || 0) >= cutoffSec);

  if (!active && queued.length === 0 && recentVisible.length === 0) return null;

  const failedCount = recentVisible.filter((r) => r.status === 'failed').length;
  const doneCount = recentVisible.filter((r) => r.status === 'done').length;

  return (
    <div className="reorganize-status-panel" id="reorganize-status-panel">
      <div className="reorg-panel-compact" onClick={() => setExpanded((open) => !open)}>
        <div className="reorg-panel-compact-left">
          {active ? (
            <>
              <span className="reorg-panel-spinner" />
              <span className="reorg-panel-active-text">
                Reorganizing <strong>{reorgDisplayLabel(active)}</strong>
                {(active.progress_total || 0) > 0
                  ? ` (${active.progress_processed || 0}/${active.progress_total} · ${Math.round(((active.progress_processed || 0) / (active.progress_total || 1)) * 100)}%)`
                  : ''}
                {active.current_track ? ` — ${active.current_track}` : ''}
              </span>
            </>
          ) : queued.length > 0 ? (
            <>
              <span className="reorg-panel-spinner" />
              <span className="reorg-panel-active-text">Reorganize queue starting…</span>
            </>
          ) : (
            <>
              <span
                className={`reorg-panel-recent-icon ${failedCount > 0 ? 'recent-warn' : 'recent-ok'}`}
              />
              <span className="reorg-panel-active-text">
                {[
                  doneCount > 0 ? `${doneCount} reorganized` : '',
                  failedCount > 0 ? `${failedCount} failed` : '',
                ]
                  .filter(Boolean)
                  .join(', ') || 'Recent activity'}
              </span>
            </>
          )}
        </div>
        <div className="reorg-panel-compact-right">
          {queued.length > 0 ? (
            <span className="reorg-panel-queue-badge" title={`${queued.length} waiting in queue`}>
              +{queued.length} queued
            </span>
          ) : null}
          <span className="reorg-panel-chevron">{expanded ? '▾' : '▸'}</span>
        </div>
      </div>

      {expanded ? (
        <div className="reorg-panel-expanded">
          {active ? <ActiveCard active={active} /> : null}

          {queued.length > 0 ? (
            <>
              <div className="reorg-panel-section-header">
                <span>Queued ({queued.length})</span>
                <button
                  className="reorg-panel-clear-btn"
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void clearReorganizeQueueRequest();
                  }}
                >
                  Cancel All
                </button>
              </div>
              <div className="reorg-panel-list">
                {queued.map((item, index) => (
                  <QueuedRow item={item} position={index + 1} key={item.queue_id ?? index} />
                ))}
              </div>
            </>
          ) : null}

          {recentVisible.length > 0 ? (
            <>
              <div className="reorg-panel-section-header">
                <span>Recent</span>
              </div>
              <div className="reorg-panel-list">
                {recentVisible.slice(0, 6).map((item, index) => (
                  <RecentRow item={item} key={item.queue_id ?? index} />
                ))}
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ActiveCard({ active }: { active: ReorganizeQueueItem }) {
  const total = active.progress_total || 0;
  const done = active.progress_processed || 0;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return (
    <div className="reorg-panel-active-card">
      <div className="reorg-panel-active-title">
        {active.album_title || 'Unknown album'}
        {isCrossArtist(active) ? (
          <span className="reorg-panel-cross-artist"> {active.artist_name || 'other artist'}</span>
        ) : null}
      </div>
      <div className="reorg-panel-progress-track">
        <div className="reorg-panel-progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="reorg-panel-active-meta">
        {total > 0 ? (
          <span>
            {done}/{total}
          </span>
        ) : null}
        {active.current_track ? (
          <span className="reorg-panel-current-track">{active.current_track}</span>
        ) : null}
        <span className="reorg-panel-counters">
          <span className="ok">{active.moved || 0} moved</span>
          {(active.skipped || 0) > 0 ? (
            <span className="warn">{active.skipped} skipped</span>
          ) : null}
          {(active.failed || 0) > 0 ? <span className="fail">{active.failed} failed</span> : null}
        </span>
      </div>
    </div>
  );
}

function QueuedRow({ item, position }: { item: ReorganizeQueueItem; position: number }) {
  return (
    <div className="reorg-panel-row queued-row">
      <span className="reorg-panel-row-pos">#{position}</span>
      <div className="reorg-panel-row-body">
        <div className="reorg-panel-row-title">{item.album_title || 'Unknown album'}</div>
        {isCrossArtist(item) ? (
          <div className="reorg-panel-row-sub">{item.artist_name || 'other artist'}</div>
        ) : item.source ? (
          <div className="reorg-panel-row-sub">via {item.source}</div>
        ) : null}
      </div>
      <button
        className="reorg-panel-cancel-btn"
        title="Cancel"
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          if (item.queue_id) void cancelReorganizeQueueItemRequest(item.queue_id);
        }}
      >
        ×
      </button>
    </div>
  );
}

function RecentRow({ item }: { item: ReorganizeQueueItem }) {
  const tone = classifyReorganizeOutcome({
    result_status: item.result_status,
    failed: item.failed,
  });
  const cls = item.status === 'cancelled' ? 'cancelled' : tone;
  let sub =
    item.status === 'cancelled'
      ? 'Cancelled'
      : formatReorganizeResultMessage({
          result_status: item.result_status,
          moved: item.moved,
          skipped: item.skipped,
          failed: item.failed,
          errors: item.error ? [{ error: item.error }] : [],
        });
  if (isCrossArtist(item)) sub = `${item.artist_name || 'other artist'} — ${sub}`;
  return (
    <div className={`reorg-panel-row recent-row ${cls}`}>
      <span className={`reorg-panel-row-icon ${cls}`} />
      <div className="reorg-panel-row-body">
        <div className="reorg-panel-row-title">{item.album_title || 'Unknown album'}</div>
        <div className="reorg-panel-row-sub">{sub}</div>
      </div>
    </div>
  );
}
