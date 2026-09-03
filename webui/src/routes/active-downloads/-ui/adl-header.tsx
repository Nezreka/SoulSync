import type { AdlFilter } from '../-adl.types';

import { ADL_FILTERS } from '../-adl.types';

/** The Review tab's copy when the unverified queue can exist. */
const REVIEW_TAB = {
  label: 'Review',
  title:
    'Review queue: imported-but-unconfirmed downloads (unverified / force-imported) and quarantined files that were never imported.',
};

/**
 * ...and when it cannot.
 *
 * Without an AcoustID key (or with require-verified on) nothing ever lands
 * unverified, so the tab is honest about being quarantine-only.
 */
const QUARANTINE_ONLY_TAB = {
  label: 'Quarantine',
  title:
    'Files that failed import checks and were NOT imported. (AcoustID is not configured or require-verified is on, so there is no unverified review queue.)',
};

/** Which of the three top-level views a filter value belongs to. */
export function viewOf(filter: AdlFilter): 'downloads' | 'review' | 'clients' {
  if (filter === 'unverified') return 'review';
  if (filter === 'clients') return 'clients';
  return 'downloads';
}

export interface AdlHeaderProps {
  filter: AdlFilter;
  counts: {
    active: number;
    queued: number;
    failed: number;
    total: number;
    completedOrFailed: number;
  };
  hasRunningWork: boolean;
  /** False when the review queue is quarantine-only. */
  acoustidEnabled: boolean;
  /**
   * How many files are waiting on a human, from the server. Null until the
   * first count lands, and the badge stays hidden until then rather than
   * flashing a 0 at someone who has 72 waiting.
   */
  reviewCount: number | null;
  /** Combined live download speed, pre-formatted ("1.2 MB/s"); '' when unknown. */
  speedText: string;
  /** Combined ETA from the batch rate samples ("~4m left"); '' when unknown. */
  etaText: string;
  onFilter: (filter: AdlFilter) => void;
  onCancelAll: () => void;
  onClearCompleted: () => void;
  cancelAllPending: boolean;
}

/**
 * The page header: a stat hero instead of a title (the sidebar already says
 * Downloads), then the navigation split the old single pill bar never made —
 * a view switcher for the three actual views, and status chips that exist
 * only inside the Downloads view.
 */
export function AdlHeader({
  filter,
  counts,
  hasRunningWork,
  acoustidEnabled,
  reviewCount,
  speedText,
  etaText,
  onFilter,
  onCancelAll,
  onClearCompleted,
  cancelAllPending,
}: AdlHeaderProps) {
  const view = viewOf(filter);
  const reviewTab = acoustidEnabled ? REVIEW_TAB : QUARANTINE_ONLY_TAB;
  const activeSub = speedText || etaText;

  return (
    <div className="adl-header">
      <div className="adl-hero">
        <div className="adl-hero-stats" id="adl-count">
          <div className={`adl-stat${counts.active > 0 ? ' adl-stat-live' : ''}`}>
            <span className="adl-stat-num">{counts.active}</span>
            <span className="adl-stat-label">active</span>
            {counts.active > 0 && activeSub ? (
              <span className="adl-stat-sub">
                {speedText}
                {speedText && etaText ? ' · ' : ''}
                {etaText}
              </span>
            ) : null}
          </div>
          <div className="adl-stat">
            <span className="adl-stat-num">{counts.queued}</span>
            <span className="adl-stat-label">queued</span>
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
          {hasRunningWork ? (
            <button
              type="button"
              className={`adl-cancel-all-btn${cancelAllPending ? ' adl-cancel-all-pending' : ''}`}
              id="adl-cancel-all-btn"
              title="Cancel all active and queued downloads"
              disabled={cancelAllPending}
              onClick={onCancelAll}
            >
              Cancel All
            </button>
          ) : null}
          {counts.completedOrFailed > 0 ? (
            <button
              type="button"
              className="adl-clear-btn"
              id="adl-clear-btn"
              onClick={onClearCompleted}
            >
              Clear Completed
            </button>
          ) : null}
        </div>
      </div>

      <div className="adl-nav">
        <div className="adl-view-tabs" id="adl-view-tabs" role="tablist">
          <button
            type="button"
            className={`adl-view-tab${view === 'downloads' ? ' active' : ''}`}
            role="tab"
            aria-selected={view === 'downloads'}
            // data-filter="all" so "back to the list" automation keeps one selector
            data-filter="all"
            onClick={() => onFilter('all')}
          >
            Downloads
          </button>
          <button
            type="button"
            className={`adl-view-tab${view === 'review' ? ' active' : ''}`}
            role="tab"
            aria-selected={view === 'review'}
            data-filter="unverified"
            title={reviewTab.title}
            onClick={() => onFilter('unverified')}
          >
            {reviewTab.label}
            {/* the old pill said nothing about how much was waiting, so a full
                queue looked the same as an empty one until you clicked it. */}
            {reviewCount ? <span className="adl-pill-badge">{reviewCount}</span> : null}
          </button>
          <button
            type="button"
            className={`adl-view-tab${view === 'clients' ? ' active' : ''}`}
            role="tab"
            aria-selected={view === 'clients'}
            data-filter="clients"
            title="Your external download clients — slskd, torrent, usenet — in one pane"
            onClick={() => onFilter('clients')}
          >
            Clients
          </button>
        </div>

        {view === 'downloads' ? (
          <div className="adl-status-chips" id="adl-filter-pills">
            {ADL_FILTERS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={`adl-pill${filter === option.value ? ' active' : ''}`}
                data-filter={option.value}
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
