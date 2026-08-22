import type { AdlFilter } from '../-adl.types';

import { ADL_FILTERS } from '../-adl.types';

/** The ⚠ pill's copy when the unverified queue can exist. */
const REVIEW_PILL = {
  label: '⚠ Unverified/Quarantine',
  title:
    'Review queue: imported-but-unconfirmed downloads (unverified / force-imported) and quarantined files that were never imported.',
};

/**
 * ...and when it cannot.
 *
 * Without an AcoustID key (or with require-verified on) nothing ever lands
 * unverified, so the pill is honest about being quarantine-only. The vanilla
 * achieved this by REWRITING the button's textContent and title after the
 * config fetch; here it is just derived state.
 */
const QUARANTINE_ONLY_PILL = {
  label: '🛡 Quarantine',
  title:
    'Files that failed import checks and were NOT imported. (AcoustID is not configured or require-verified is on, so there is no unverified review queue.)',
};

export interface AdlHeaderProps {
  filter: AdlFilter;
  counts: { active: number; queued: number; total: number; completedOrFailed: number };
  hasRunningWork: boolean;
  /** False when the review queue is quarantine-only. */
  acoustidEnabled: boolean;
  /**
   * How many files are waiting on a human, from the server. Null until the
   * first count lands, and the badge stays hidden until then rather than
   * flashing a 0 at someone who has 72 waiting.
   */
  reviewCount: number | null;
  onFilter: (filter: AdlFilter) => void;
  onCancelAll: () => void;
  onClearCompleted: () => void;
  cancelAllPending: boolean;
}

export function AdlHeader({
  filter,
  counts,
  hasRunningWork,
  acoustidEnabled,
  reviewCount,
  onFilter,
  onCancelAll,
  onClearCompleted,
  cancelAllPending,
}: AdlHeaderProps) {
  // "2 active / 1 queued / 40 total" — the leading parts drop out when zero so
  // an idle page reads simply "40 total".
  const countParts: string[] = [];
  if (counts.active > 0) countParts.push(`${counts.active} active`);
  if (counts.queued > 0) countParts.push(`${counts.queued} queued`);
  countParts.push(`${counts.total} total`);

  const reviewPill = acoustidEnabled ? REVIEW_PILL : QUARANTINE_ONLY_PILL;

  return (
    <div className="adl-header">
      <h2 className="adl-title">
        <svg
          width="22"
          height="22"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="7 10 12 15 17 10" />
          <line x1="12" y1="15" x2="12" y2="3" />
        </svg>{' '}
        Downloads
      </h2>
      <div className="adl-controls">
        <div className="adl-filter-pills" id="adl-filter-pills">
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
          <button
            type="button"
            className={`adl-pill${filter === 'unverified' ? ' active' : ''}`}
            data-filter="unverified"
            aria-pressed={filter === 'unverified'}
            title={reviewPill.title}
            onClick={() => onFilter('unverified')}
          >
            {reviewPill.label}
            {/* the pill said nothing about how much was waiting, so a full
                queue looked the same as an empty one until you clicked it. */}
            {reviewCount ? <span className="adl-pill-badge">{reviewCount}</span> : null}
          </button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span className="adl-count" id="adl-count">
            {countParts.join(' / ')}
          </span>
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
    </div>
  );
}
