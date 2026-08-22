/**
 * The sync history list.
 *
 * A straight port of the vanilla modal's body (wishlist-tools.js 3981-4055),
 * with one behavioural change: the live progress drawer is driven by state
 * rather than by writing into six ids per row. The vanilla's approach meant a
 * re-render — a delete, a page change — silently orphaned a running poll's
 * targets, and the bar stopped moving while the sync carried on.
 */

import type { SyncHistoryEntry } from '../-sync.history';
import type { SyncHistoryResync } from '../-sync.use-history';

import {
  syncHistoryMeta,
  syncHistoryPageCount,
  syncHistorySourceIcon,
  syncHistorySourceTabs,
  syncHistoryStats,
} from '../-sync.history';
import { timeAgo } from '../-sync.mirrored';

export interface SyncHistoryPanelProps {
  entries: SyncHistoryEntry[];
  stats: Record<string, number>;
  total: number;
  page: number;
  pageSize: number;
  source: string | null;
  loading: boolean;
  error: string;
  resyncs: Record<number, SyncHistoryResync>;
  onSelectSource: (source: string | null) => void;
  onPage: (page: number) => void;
  onResync: (entryId: number) => void;
  onCancel: (entryId: number) => void;
  onDelete: (entryId: number) => void;
  now: number;
}

export function SyncHistoryPanel({
  entries,
  stats,
  total,
  page,
  pageSize,
  source,
  loading,
  error,
  resyncs,
  onSelectSource,
  onPage,
  onResync,
  onCancel,
  onDelete,
  now,
}: SyncHistoryPanelProps) {
  const tabs = syncHistorySourceTabs(stats, source);
  const pages = syncHistoryPageCount(total, pageSize);

  return (
    <div className="sync-history-panel">
      <div className="sync-history-tabs" role="tablist" aria-label="Filter by source">
        {tabs.map((tab) => (
          <button
            key={tab.source ?? 'all'}
            type="button"
            role="tab"
            aria-selected={tab.active}
            className={`sync-history-tab${tab.active ? ' active' : ''}`}
            onClick={() => onSelectSource(tab.source)}
          >
            {tab.label} <span className="sync-history-tab-count">{tab.count}</span>
          </button>
        ))}
      </div>

      <div className="sync-history-list">
        {loading ? (
          <div className="sync-history-loading">Loading…</div>
        ) : error ? (
          <div className="sync-history-empty">{error}</div>
        ) : entries.length === 0 ? (
          <div className="sync-history-empty">
            No sync history yet. Completed syncs will appear here.
          </div>
        ) : (
          entries.map((entry) => (
            <SyncHistoryRow
              key={entry.id}
              entry={entry}
              resync={resyncs[entry.id]}
              onResync={onResync}
              onCancel={onCancel}
              onDelete={onDelete}
              now={now}
            />
          ))
        )}
      </div>

      {pages > 1 && (
        <div className="sync-history-pagination">
          <button
            type="button"
            className="sync-history-page-btn"
            disabled={page <= 1}
            onClick={() => onPage(page - 1)}
          >
            Prev
          </button>
          <span className="sync-history-page-info">
            Page {page} of {pages}
          </span>
          <button
            type="button"
            className="sync-history-page-btn"
            disabled={page >= pages}
            onClick={() => onPage(page + 1)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}

function SyncHistoryRow({
  entry,
  resync,
  onResync,
  onCancel,
  onDelete,
  now,
}: {
  entry: SyncHistoryEntry;
  resync?: SyncHistoryResync;
  onResync: (entryId: number) => void;
  onCancel: (entryId: number) => void;
  onDelete: (entryId: number) => void;
  now: number;
}) {
  const name = entry.playlist_name || 'Unknown';
  const meta = syncHistoryMeta(entry);
  const running = resync?.progress.phase === 'running';

  return (
    <div className={`sync-history-entry-wrapper${resync ? ' syncing' : ''}`}>
      <div className="sync-history-entry">
        {entry.thumb_url ? (
          <img src={entry.thumb_url} className="sync-history-thumb" loading="lazy" alt="" />
        ) : (
          <div className="sync-history-thumb-placeholder">
            {syncHistorySourceIcon(entry.source)}
          </div>
        )}

        <div className="sync-history-entry-text">
          <div className="sync-history-entry-title" title={name}>
            {name}
          </div>
          {meta && <div className="sync-history-entry-meta">{meta}</div>}
        </div>

        <span className={`sync-history-source-badge ${entry.source ?? ''}`}>{entry.source}</span>

        <div className="sync-history-stats">
          {syncHistoryStats(entry).map((stat) => (
            <span key={stat.label} className={`sync-history-stat ${stat.kind}`}>
              {stat.label}
            </span>
          ))}
        </div>

        <div className="sync-history-entry-time">{timeAgo(entry.started_at, now)}</div>

        <button
          type="button"
          className="sync-history-delete-btn"
          title="Delete this entry"
          aria-label={`Delete ${name} from history`}
          onClick={() => onDelete(entry.id)}
        >
          &times;
        </button>
        <button
          type="button"
          className="sync-history-resync-btn"
          title="Re-sync this playlist"
          disabled={Boolean(resync)}
          onClick={() => onResync(entry.id)}
        >
          {resync ? 'Syncing…' : 'Re-sync'}
        </button>
      </div>

      {resync && (
        <div className="sync-history-live-progress">
          <div className="sync-history-progress-bar-container">
            <div
              className="sync-history-progress-bar-fill"
              style={{ width: `${resync.progress.percent}%` }}
            />
          </div>
          <div className="sync-history-progress-text">
            <span className="sync-history-progress-step">{resync.progress.step}</span>
            <div className="sync-history-progress-stats">
              <span className="matched">{resync.progress.matched} matched</span>
              <span className="failed">{resync.progress.failed} failed</span>
            </div>
            {/* Gone once the run has ended — there is nothing left to cancel. */}
            {running && (
              <button
                type="button"
                className="sync-history-cancel-btn"
                onClick={() => onCancel(entry.id)}
              >
                Cancel
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
