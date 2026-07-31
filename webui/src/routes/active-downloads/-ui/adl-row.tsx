import type { AdlDownload } from '../-adl.types';

import {
  batchColorIndex,
  qualityChipTitle,
  showQualityChip,
  statusClass,
  statusLabel,
  verificationBadge,
} from '../-adl.helpers';

/** Album art, or the empty placeholder that keeps the row's grid aligned. */
export function RowArt({ artwork }: { artwork: string }) {
  if (!artwork) return <div className="adl-row-art adl-row-art-empty" />;
  return (
    <img
      className="adl-row-art"
      src={artwork}
      alt=""
      // Art URLs go stale (a source rotates them, a file moves). Hiding the
      // broken image keeps the row readable instead of showing a torn icon.
      onError={(event) => {
        event.currentTarget.style.display = 'none';
      }}
    />
  );
}

/**
 * The retry chip.
 *
 * Only while a row is still in flight — a finished row's retry count is
 * history, not status. The shield is appended when the retry was triggered by
 * AcoustID, because that means the previous candidate was quarantined rather
 * than merely failing to download.
 */
function RetryChip({ dl }: { dl: AdlDownload }) {
  const cls = statusClass(dl.status);
  if (!dl.retry_info || (cls !== 'active' && cls !== 'queued')) return null;

  const acoustidTriggered = ['acoustid', 'acoustid_unverified'].includes(
    String(dl.retry_trigger ?? ''),
  );
  const because = dl.retry_trigger
    ? acoustidTriggered
      ? ' — previous candidate quarantined (AcoustID)'
      : ` — previous candidate triggered by ${dl.retry_trigger}`
    : '';

  return (
    <>
      {' '}
      <span
        className="adl-retry-info"
        title={`Retry engine: trying the next-best candidate (attempt ${dl.retry_info}${because})`}
      >
        🔁 {dl.retry_info}
        {acoustidTriggered ? ' 🛡' : ''}
      </span>
    </>
  );
}

export interface AdlRowProps {
  dl: AdlDownload;
  /** Rendered only for cancellable rows with real cancel coordinates. */
  onCancel?: (dl: AdlDownload) => void;
  /** Set in the unverified view — makes the whole row open the audit trail. */
  onRowAudit?: (dl: AdlDownload) => void;
  /** The review action strip, supplied by the unverified view. */
  actions?: React.ReactNode;
}

export function AdlRow({ dl, onCancel, onRowAudit, actions }: AdlRowProps) {
  const cls = statusClass(dl.status);
  const label = statusLabel(dl.status);
  const badge = verificationBadge(dl);

  const meta = [dl.artist, dl.album].filter(Boolean).join(' · ');
  // "3 of 19" — only meaningful for a real multi-track batch.
  const position = dl.batch_total > 1 ? `${(dl.track_index || 0) + 1} of ${dl.batch_total}` : '';
  const colorIdx = batchColorIndex(dl.batch_id);

  /**
   * Cancel needs (playlist_id, track_index), not task_id — and a terminal row
   * has nothing to cancel. `track_index` is checked against undefined rather
   * than falsiness because 0 is a valid index.
   */
  const cancellable =
    (cls === 'active' || cls === 'queued') &&
    Boolean(dl.playlist_id) &&
    dl.track_index !== undefined;

  return (
    <div
      className={`adl-row adl-row-${cls}`}
      data-task-id={dl.task_id}
      data-batch-id={dl.batch_id || ''}
      {...(onRowAudit
        ? {
            onClick: () => onRowAudit(dl),
            style: { cursor: 'pointer' },
            title: 'Click to show download details (audit trail, embedded tags, lyrics)',
          }
        : {})}
    >
      {colorIdx >= 0 ? (
        <div
          className="adl-row-batch-color"
          style={{ background: `rgba(var(--batch-color-${colorIdx}),0.6)` }}
        />
      ) : null}

      <RowArt artwork={dl.artwork} />

      <div className="adl-row-info">
        <div className="adl-row-title">{dl.title || 'Unknown Track'}</div>
        {meta ? <div className="adl-row-meta">{meta}</div> : null}
        {dl.batch_name ? (
          <div className="adl-row-batch">
            {dl.batch_name}
            {position ? ` · Track ${position}` : ''}
          </div>
        ) : null}
        {dl.error ? <div className="adl-row-error">{dl.error}</div> : null}
      </div>

      <div className={`adl-row-status ${cls}`}>
        <span className={`adl-status-dot ${cls}`} />
        {label.spinner ? <span className="adl-spinner" /> : null}
        {label.text}
        {badge ? (
          <>
            {' '}
            <span className={badge.className} title={badge.title}>
              {badge.glyph}
            </span>
          </>
        ) : null}
        {showQualityChip(dl) ? (
          <>
            {' '}
            <span className="adl-quality-chip" title={qualityChipTitle()}>
              {dl.quality}
            </span>
          </>
        ) : null}
        <RetryChip dl={dl} />
      </div>

      {actions}

      {cancellable && onCancel ? (
        <button
          type="button"
          className="adl-row-cancel"
          title="Cancel this download"
          aria-label="Cancel download"
          onClick={(event) => {
            // The row itself may be an audit trigger in the review view.
            event.stopPropagation();
            onCancel(dl);
          }}
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      ) : null}
    </div>
  );
}
