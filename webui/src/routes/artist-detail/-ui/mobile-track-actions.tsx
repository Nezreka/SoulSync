import type { EnhancedTrack } from '../-artist-detail.enhanced';

import { BodyPortal } from './portal';

/**
 * The mobile action sheet (_showMobileTrackActions, library.js:2905): the
 * per-track actions as a bottom popover, since the action columns are hidden
 * on small screens. Each action closes the sheet first, as the vanilla did,
 * then runs the SAME handler its desktop button uses — the row owns those.
 */
export function MobileTrackActions({
  track,
  isAdmin,
  onPlay,
  onQueue,
  onTagPreview,
  onSourceInfo,
  onRedownload,
  onDelete,
  onClose,
}: {
  track: EnhancedTrack;
  isAdmin: boolean;
  onPlay: () => void;
  onQueue: () => void;
  onTagPreview: () => void;
  onSourceInfo: () => void;
  onRedownload: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const run = (action: () => void) => () => {
    onClose();
    action();
  };
  const hasFile = Boolean(track.file_path);

  // Body-level, as the vanilla appended it: the sheet is position:fixed and
  // must not depend on the table's stacking context.
  return (
    <BodyPortal>
      <div className="mobile-popover-overlay" onClick={onClose} />
      <div className="enhanced-mobile-actions-popover">
        <div className="popover-title">{String(track.title || 'Track')}</div>
        {hasFile ? (
          <>
            <button type="button" onClick={run(onPlay)}>
              <span className="popover-icon">▶</span>Play
            </button>
            <button type="button" onClick={run(onQueue)}>
              <span className="popover-icon">+</span>Add to Queue
            </button>
          </>
        ) : null}
        {isAdmin && hasFile ? (
          <button type="button" onClick={run(onTagPreview)}>
            <span className="popover-icon">✎</span>Write Tags
          </button>
        ) : null}
        {isAdmin ? (
          <>
            <button type="button" onClick={run(onSourceInfo)}>
              <span className="popover-icon">ℹ</span>Source Info
            </button>
            <button type="button" onClick={run(onRedownload)}>
              <span className="popover-icon">↻</span>Redownload Track
            </button>
            <button type="button" className="popover-delete" onClick={run(onDelete)}>
              <span className="popover-icon">✕</span>Delete Track
            </button>
          </>
        ) : null}
        <button type="button" className="popover-cancel" onClick={onClose}>
          Cancel
        </button>
      </div>
    </BodyPortal>
  );
}
