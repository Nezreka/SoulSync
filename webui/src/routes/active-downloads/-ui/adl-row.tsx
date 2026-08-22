import { useRef, useState } from 'react';

import type { AdlDownload, AdlTaskDetail } from '../-adl.types';

import { fetchTaskDetail } from '../-adl.api';
import {
  batchColorIndex,
  liveDetailLines,
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

/**
 * The expanded detail panel (#1156).
 *
 * In-flight rows render the live narration straight off the 2s payload —
 * where it's searching, what it found, who it's pulling from. Terminal rows
 * render the fetched task detail (history-merged source/quality/AcoustID/
 * file), and history rows render the fields they already carry.
 */
function RowDetailPanel({
  dl,
  detail,
  terminal,
}: {
  dl: AdlDownload;
  detail: AdlTaskDetail | null;
  terminal: boolean;
}) {
  let lines: Array<[string, string]>;
  if (!terminal) {
    lines = liveDetailLines(dl);
    if (!lines.length) {
      return <div className="verif-quar-details">Waiting for the engine's next update…</div>;
    }
  } else if (detail) {
    lines = (
      [
        ['Source', dl.download_source || detail.source],
        ['Quality', detail.quality || dl.quality],
        ['AcoustID', detail.acoustid_result],
        ['File', detail.file_path],
        ['Reason', detail.status_kind === 'completed' ? '' : detail.reason],
        [
          'Expected',
          detail.expected?.title
            ? `${detail.expected.artist ? `${detail.expected.artist} — ` : ''}${detail.expected.title}`
            : '',
        ],
        [
          'Downloaded',
          detail.downloaded?.title
            ? `${detail.downloaded.artist ? `${detail.downloaded.artist} — ` : ''}${detail.downloaded.title}`
            : '',
        ],
      ] as Array<[string, string]>
    ).filter(([, value]) => Boolean(value));
  } else if (dl.is_persistent_history) {
    lines = (
      [
        ['Source', dl.download_source || dl.batch_name],
        ['Quality', dl.quality],
        ['File', dl.file_path || ''],
        ['Downloaded', dl.created_at || ''],
      ] as Array<[string, string]>
    ).filter(([, value]) => Boolean(value));
  } else {
    return <div className="verif-quar-details">Loading details…</div>;
  }
  return (
    <div className="verif-quar-details">
      {lines.map(([label, value]) => (
        <div key={label}>
          <span className="verif-detail-label">{label}:</span> {value}
        </div>
      ))}
    </div>
  );
}

export interface AdlRowProps {
  dl: AdlDownload;
  /**
   * Rendered only for cancellable rows with real cancel coordinates.
   *
   * May return a promise — the row locks its button until it settles.
   */
  onCancel?: (dl: AdlDownload) => void | Promise<void>;
  /** Set in the unverified view — makes the whole row open the audit trail. */
  onRowAudit?: (dl: AdlDownload) => void;
  /** The review action strip, supplied by the unverified view. */
  actions?: React.ReactNode;
}

export function AdlRow({ dl, onCancel, onRowAudit, actions }: AdlRowProps) {
  const cls = statusClass(dl.status);
  const label = statusLabel(dl.status);
  const badge = verificationBadge(dl);
  /**
   * Locks the cancel button while its request is in flight.
   *
   * Two clicks fire two cancels, and the second fails on a task the first
   * already took — the user sees an error for an action that worked. The
   * vanilla guarded it with a synchronous `dataset.cancelling` flag plus an
   * `adl-row-cancel-pending` class.
   *
   * Both halves are needed here and they are not redundant. `disabled` alone
   * only stops the SECOND event loop turn: two clicks dispatched in the same
   * tick both run before React re-renders, and both would read the same stale
   * `false` from state. The ref is the part that is actually synchronous —
   * it is the direct equivalent of the vanilla's dataset flag. State drives
   * the class and the disabled attribute, which a ref cannot do.
   *
   * Local state is safe here: rows are keyed by task_id, so it survives the
   * 2s poll re-render rather than resetting.
   */
  const cancellingRef = useRef(false);
  const [cancelling, setCancelling] = useState(false);

  /**
   * Row expansion (#1156). Local state survives the 2s poll because rows are
   * keyed by task_id. Terminal real-task rows fetch their merged detail once
   * on first open; history rows and in-flight rows render from what the row
   * already carries.
   */
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<AdlTaskDetail | null>(null);
  const detailRequested = useRef(false);
  const terminal = cls === 'completed' || cls === 'failed' || cls === 'cancelled';

  const toggleOpen = () => {
    const next = !open;
    setOpen(next);
    if (next && terminal && !dl.is_persistent_history && !detailRequested.current) {
      detailRequested.current = true;
      void fetchTaskDetail(dl.task_id).then(setDetail);
    }
  };

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
        : {
            onClick: toggleOpen,
            style: { cursor: 'pointer' },
            title: open
              ? 'Click to hide details'
              : 'Click to show details (source, search progress, file, quality)',
          })}
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
        {dl.batch_name || dl.download_source ? (
          <div className="adl-row-batch">
            {dl.batch_name}
            {position ? ` · Track ${position}` : ''}
            {/* #1156: live rows finally name their source; history rows
                already carry it as batch_name, so don't repeat it */}
            {dl.download_source && dl.download_source !== dl.batch_name
              ? `${dl.batch_name ? ' · ' : ''}${dl.download_source}`
              : ''}
          </div>
        ) : null}
        {dl.error ? <div className="adl-row-error">{dl.error}</div> : null}
        {open && !onRowAudit ? (
          <RowDetailPanel dl={dl} detail={detail} terminal={terminal} />
        ) : null}
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
          className={`adl-row-cancel${cancelling ? ' adl-row-cancel-pending' : ''}`}
          title="Cancel this download"
          aria-label="Cancel download"
          disabled={cancelling}
          onClick={(event) => {
            // The row itself may be an audit trigger in the review view.
            event.stopPropagation();
            if (cancellingRef.current) return;
            cancellingRef.current = true;
            setCancelling(true);
            void (async () => {
              try {
                await onCancel(dl);
              } catch {
                // The handler owns error reporting — it toasts. All the row
                // has to do is make sure a rejection cannot escape as an
                // unhandled rejection or leave the button stuck disabled.
              } finally {
                // On success the row is usually refetched away and this never
                // runs; when it stays, the button has to become usable again.
                cancellingRef.current = false;
                setCancelling(false);
              }
            })();
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
