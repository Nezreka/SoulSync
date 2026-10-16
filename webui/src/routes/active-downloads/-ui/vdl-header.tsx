/**
 * The video downloads header: stat hero + action buttons + the view switcher
 * (Downloads / Review / Clients) + the status filter pills.
 *
 * Same .adl-* markup vocabulary as the old vanilla subpage — the existing
 * CSS applies unchanged.
 */

import type { VdlFilter, VdlView } from '../-vdl.types';

import { vdlFmtSpeed } from '../-vdl.helpers';
import { VDL_FILTERS } from '../-vdl.types';

export interface VdlHeaderProps {
  view: VdlView;
  onView: (view: VdlView) => void;
  filter: VdlFilter;
  onFilter: (filter: VdlFilter) => void;
  counts: { active: number; completed: number; failed: number; total: number };
  liveSpeedBps: number;
  hasRunningWork: boolean;
  retryableCount: number;
  reviewCount: number | null;
  historyCount: number | null;
  onOpenHistory: () => void;
  onCancelAll: () => void;
  onRetryAll: () => void;
  onClearFinished: () => void;
  retryAllPending: boolean;
}

const REVIEW_TITLE = 'Recycle bin + release blocklist';
const CLIENTS_TITLE = 'Your download clients — torrent, usenet, slskd — in one pane';

export function VdlHeader({
  view,
  onView,
  filter,
  onFilter,
  counts,
  liveSpeedBps,
  hasRunningWork,
  retryableCount,
  reviewCount,
  historyCount,
  onOpenHistory,
  onCancelAll,
  onRetryAll,
  onClearFinished,
  retryAllPending,
}: VdlHeaderProps) {
  const speedText = vdlFmtSpeed(liveSpeedBps);
  const subParts: string[] = [];
  if (counts.active) subParts.push(`${counts.active} active`);
  if (counts.completed) subParts.push(`${counts.completed} done`);
  if (counts.failed) subParts.push(`${counts.failed} failed`);

  return (
    <div className="adl-header">
      <div className="adl-hero">
        <div className="adl-hero-stats">
          <div className={`adl-stat${counts.active > 0 ? ' adl-stat-live' : ''}`}>
            <span className="adl-stat-num">{counts.active}</span>
            <span className="adl-stat-label">active</span>
            {speedText ? <span className="adl-stat-sub">{speedText}</span> : null}
          </div>
          <div className="adl-stat">
            <span className="adl-stat-num">{counts.completed}</span>
            <span className="adl-stat-label">done</span>
          </div>
          <div className={`adl-stat${counts.failed > 0 ? ' adl-stat-bad' : ''}`}>
            <span className="adl-stat-num">{counts.failed}</span>
            <span className="adl-stat-label">failed</span>
          </div>
          <div className="adl-stat">
            <span className="adl-stat-num">{counts.total}</span>
            <span className="adl-stat-label">total</span>
          </div>
        </div>
        <div className="adl-hero-actions">
          {subParts.length > 0 ? <span className="adl-count">{subParts.join('  ·  ')}</span> : null}
          <button
            type="button"
            className="adl-history-btn"
            title="Download history"
            onClick={onOpenHistory}
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M3 3v5h5" />
              <path d="M3.05 13A9 9 0 1 0 6 5.3L3 8" />
              <path d="M12 7v5l4 2" />
            </svg>
            History{' '}
            {historyCount ? (
              <span className="adl-history-n" data-vdh-count>
                {historyCount}
              </span>
            ) : null}
          </button>
          {retryableCount >= 2 ? (
            <button
              type="button"
              className="adl-clear-btn"
              title="Re-grab every failed download (same releases)"
              disabled={retryAllPending}
              onClick={onRetryAll}
            >
              Retry Failed
            </button>
          ) : null}
          {hasRunningWork ? (
            <button type="button" className="adl-cancel-all-btn" onClick={onCancelAll}>
              Cancel All
            </button>
          ) : null}
          {counts.completed + counts.failed > 0 ? (
            <button type="button" className="adl-clear-btn" onClick={onClearFinished}>
              Clear Finished
            </button>
          ) : null}
        </div>
      </div>

      <div className="adl-nav">
        <div className="adl-view-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={view === 'downloads'}
            className={`adl-view-tab${view === 'downloads' ? ' active' : ''}`}
            onClick={() => onView('downloads')}
          >
            Downloads
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'review'}
            className={`adl-view-tab${view === 'review' ? ' active' : ''}`}
            title={REVIEW_TITLE}
            onClick={() => onView('review')}
          >
            Review
            {reviewCount ? (
              <span className="adl-pill-badge" data-vdpg-review-n>
                {reviewCount}
              </span>
            ) : null}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'clients'}
            className={`adl-view-tab${view === 'clients' ? ' active' : ''}`}
            title={CLIENTS_TITLE}
            onClick={() => onView('clients')}
          >
            Clients
          </button>
        </div>

        {view === 'downloads' ? (
          <div className="adl-status-chips">
            {VDL_FILTERS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={`adl-pill${filter === option.value ? ' active' : ''}`}
                aria-pressed={filter === option.value}
                onClick={() => onFilter(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
