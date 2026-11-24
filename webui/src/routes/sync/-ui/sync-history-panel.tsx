/**
 * The sync history list.
 *
 * A straight port of the vanilla modal's body (wishlist-tools.js 3981-4055),
 * with one behavioural change: the live progress drawer is driven by state
 * rather than by writing into six ids per row. The vanilla's approach meant a
 * re-render — a delete, a page change — silently orphaned a running poll's
 * targets, and the bar stopped moving while the sync carried on.
 */

import { useState } from 'react';

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

interface SyncHistoryTrackResult {
  name?: string;
  title?: string;
  artist?: string;
  matched_title?: string;
  matched_artist?: string;
  status?: string;
  outcome?: string;
  error?: string;
  reason?: string;
}

function formatRunDuration(started?: string | null, completed?: string | null): string | null {
  if (!started || !completed) return null;
  const s = Date.parse(started);
  const e = Date.parse(completed);
  if (!Number.isFinite(s) || !Number.isFinite(e) || e < s) return null;
  const secs = Math.round((e - s) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  return rem ? `${mins}m ${rem}s` : `${mins}m`;
}

function normalizeTrackOutcome(
  tr: SyncHistoryTrackResult,
): 'matched' | 'failed' | 'unmatched' | 'skipped' {
  const st = String(tr.status || tr.outcome || '').toLowerCase();
  if (st.includes('fail') || st.includes('error')) return 'failed';
  if (st.includes('skip') || st.includes('cool')) return 'skipped';
  if (st.includes('wish') || st.includes('unmatch') || st.includes('miss')) return 'unmatched';
  return 'matched';
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
  const [expanded, setExpanded] = useState(false);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [trackResults, setTrackResults] = useState<SyncHistoryTrackResult[] | null>(null);
  const [outcomeFilter, setOutcomeFilter] = useState<
    'all' | 'matched' | 'failed' | 'unmatched' | 'skipped'
  >('all');

  const rawTrigger = (entry as unknown as { source_page?: string }).source_page || '';
  const isScheduled = rawTrigger === 'scheduled' || rawTrigger === 'automation';
  const durationLabel = formatRunDuration(
    entry.started_at,
    (entry as unknown as { completed_at?: string | null }).completed_at,
  );

  const toggleDetails = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && trackResults === null && !loadingDetails) {
      setLoadingDetails(true);
      try {
        const resp = await fetch(`/api/sync/history/${entry.id}`);
        if (resp.ok) {
          const data = (await resp.json()) as {
            entry?: { track_results?: SyncHistoryTrackResult[]; tracks?: SyncHistoryTrackResult[] };
            track_results?: SyncHistoryTrackResult[];
          };
          const list = data.track_results || data.entry?.track_results || data.entry?.tracks || [];
          setTrackResults(Array.isArray(list) ? list : []);
        } else {
          setTrackResults([]);
        }
      } catch {
        setTrackResults([]);
      } finally {
        setLoadingDetails(false);
      }
    }
  };

  const filteredTracks = (trackResults || []).filter(
    (tr) => outcomeFilter === 'all' || normalizeTrackOutcome(tr) === outcomeFilter,
  );

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
          <div className="sync-history-entry-meta">
            {isScheduled ? 'Scheduled' : 'Manual'}
            {durationLabel ? ` · ${durationLabel}` : ''}
          </div>
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
          className="sync-history-resync-btn"
          title="Show track-level execution details"
          aria-expanded={expanded}
          onClick={() => void toggleDetails()}
        >
          {expanded ? 'Hide details' : 'Details'}
        </button>

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
          title="Push the same tracks as this sync to your media server again"
          disabled={Boolean(resync)}
          onClick={() => onResync(entry.id)}
        >
          {resync ? 'Pushing…' : 'Push again'}
        </button>
      </div>

      {expanded && (
        <div
          className="sync-history-details"
          style={{ padding: '10px 14px', borderTop: '1px solid rgba(255,255,255,0.08)' }}
        >
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '8px' }}>
            {(['all', 'matched', 'unmatched', 'failed', 'skipped'] as const).map((f) => (
              <button
                key={f}
                type="button"
                className={`sync-history-tab${outcomeFilter === f ? ' active' : ''}`}
                onClick={() => setOutcomeFilter(f)}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
          {loadingDetails ? (
            <div className="sync-history-loading">Loading track results…</div>
          ) : filteredTracks.length === 0 ? (
            <div className="sync-history-empty">No track details for this filter.</div>
          ) : (
            <div
              style={{
                maxHeight: '240px',
                overflowY: 'auto',
                display: 'flex',
                flexDirection: 'column',
                gap: '4px',
              }}
            >
              {filteredTracks.map((tr, idx) => {
                const outcome = normalizeTrackOutcome(tr);
                return (
                  <div
                    key={`${entry.id}-tr-${idx}`}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      gap: '8px',
                      fontSize: '12px',
                      padding: '4px 0',
                      borderBottom: '1px solid rgba(255,255,255,0.04)',
                    }}
                  >
                    <span>
                      <strong>{tr.name || tr.title || 'Unknown track'}</strong>
                      {tr.artist ? ` — ${tr.artist}` : ''}
                      {tr.matched_title ? ` → ${tr.matched_title}` : ''}
                      {tr.error || tr.reason ? ` (${tr.error || tr.reason})` : ''}
                    </span>
                    <span className={`sync-history-stat ${outcome}`}>{outcome}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

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
