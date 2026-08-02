import {
  syncButtonState,
  syncCompletedId,
  syncFailedId,
  syncPendingId,
  syncPercentageId,
  syncProgress,
  syncStatusId,
} from '../-discover.playlist-sync';

/**
 * The shared sync-status panel and its Sync button.
 *
 * Transcribed from index.html 4745-4760 and discover.js 11378-11383 /
 * 11452-11456.
 *
 * Every syncable thing on the page — the seasonal playlist, each decade, each
 * mix — renders this same panel with ids derived from its own key. The ids are
 * not decoration: the vanilla's poller writes into them by id, and this is what
 * PR 2's cleanup has to keep matching when the vanilla's own copy is deleted.
 */

export interface SyncStatusProps {
  /**
   * The id prefix — a playlist type, a decade's status base, or a mix's.
   * Everything else on the panel is derived from it.
   */
  statusBase: string;
  progress?: { total_tracks?: number; matched_tracks?: number; failed_tracks?: number };
  /** Hidden until a sync is actually running or just finished. */
  visible: boolean;
}

export function SyncStatus({ statusBase, progress, visible }: SyncStatusProps) {
  if (!visible) return null;
  const p = syncProgress(progress);
  return (
    <div className="discover-sync-status" id={syncStatusId(statusBase)}>
      <div className="sync-status-content">
        <div className="sync-status-label">
          <span className="sync-icon">⟳</span>
          <span>Syncing to media server...</span>
        </div>
        <div className="sync-status-stats">
          <span className="sync-stat">
            ✓ <span id={syncCompletedId(statusBase)}>{p.matched}</span>
          </span>
          {/*
            Pending is total minus PROCESSED, and processed counts failures as
            well as matches — a failed track is finished, not pending. Without
            that, a sync where everything fails sits at 0% looking hung.
          */}
          <span className="sync-stat">
            ⏳ <span id={syncPendingId(statusBase)}>{p.pending}</span>
          </span>
          <span className="sync-stat">
            ✗ <span id={syncFailedId(statusBase)}>{p.failed}</span>
          </span>
          <span className="sync-stat">
            (<span id={syncPercentageId(statusBase)}>{p.percentage}</span>%)
          </span>
        </div>
      </div>
    </div>
  );
}

export interface SyncButtonProps {
  id: string;
  running: boolean;
  label?: string;
  onClick: () => void;
}

/**
 * The Sync button.
 *
 * Its three inline styles move together in the vanilla, so they move together
 * here: a disabled button that still looks and behaves clickable is worse than
 * one that plainly cannot be pressed.
 */
export function SyncButton({ id, running, label = 'Sync', onClick }: SyncButtonProps) {
  const state = syncButtonState(running);
  return (
    <button
      type="button"
      className="action-button primary"
      id={id}
      disabled={state.disabled}
      style={{ opacity: state.opacity, cursor: state.cursor }}
      onClick={onClick}
    >
      {label}
    </button>
  );
}
