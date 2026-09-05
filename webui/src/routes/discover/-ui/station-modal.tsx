import { useId } from 'react';

import { useAccessibleModal } from '@/components/dialog';

import type { StationSnapshot } from '../-discover.stations';

import { mixSelectionBar, mixSetAllSelected } from '../-discover.mixes';
import {
  stationAcquisitionNote,
  stationScopeCopy,
  stationSelectionOf,
  stationSyncCopy,
  stationTitle,
} from '../-discover.stations';
import { CompactPlaylist } from './mix-modal';
import { SyncStatus } from './sync-status';

/**
 * The station preview dialog.
 *
 * It reuses the mix modal's compact track list and selection bar rather than
 * inventing a second one — the selection semantics are identical and the
 * checkbox/row behaviour has already been fought over once.
 *
 * What it does NOT reuse is the mix's operation identity. A station's download
 * and sync are keyed by station + snapshot revision, so they can never collide
 * with a Daily Mix's key, and a retry of the same revision addresses the same
 * destination playlist instead of creating a second one.
 *
 * The copy states the finite scope on purpose: "Sync these 40" is what this
 * actually does. "Sync endless radio" would be a promise nothing keeps.
 */

export interface StationModalProps {
  snapshot: StationSnapshot | null;
  stationName: string;
  loading?: boolean;
  error?: string | null;
  selected: number[];
  syncing?: boolean;
  syncStatusBase?: string;
  syncProgress?: Parameters<typeof SyncStatus>[0]['progress'];
  onClose: () => void;
  onRefresh: () => void;
  onToggleTrack: (index: number) => void;
  /** Play ONE row. The compact list renders a play control per row, and a
   *  control that does nothing is worse than no control. */
  onPlayTrack: (index: number) => void;
  /** The row whose play is still resolving, so a second tap cannot double it. */
  playingIndex?: number | null;
  onSelectAll: (indices: number[]) => void;
  onClearSelection: () => void;
  onPlaySelected: () => void;
  onDownloadSelected: () => void;
  onSyncSelected: () => void;
}

export function StationModal({
  snapshot,
  stationName,
  loading,
  error,
  selected,
  syncing,
  syncStatusBase,
  syncProgress,
  onClose,
  onRefresh,
  onToggleTrack,
  onPlayTrack,
  playingIndex = null,
  onSelectAll,
  onClearSelection,
  onPlaySelected,
  onDownloadSelected,
  onSyncSelected,
}: StationModalProps) {
  const titleId = useId();
  // escape, focus trap, initial focus, focus restore, scroll lock
  const { ref, onBackdropClick } = useAccessibleModal<HTMLDivElement>(onClose);

  const tracks = snapshot?.tracks ?? [];
  const total = tracks.length;
  const bar = mixSelectionBar(selected.length, total);
  const chosen = stationSelectionOf(snapshot, selected);
  const unavailable = snapshot?.counts?.unavailable ?? 0;
  const disabled = selected.length === 0;

  return (
    <div className="modal-overlay" id="station-modal-overlay" onClick={onBackdropClick}>
      <div
        className="mix-modal station-modal"
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="mix-modal-header">
          <div>
            <div className="mix-modal-subtitle">Station preview</div>
            <h2 className="mix-modal-title" id={titleId}>
              {snapshot ? stationTitle(snapshot) : `${stationName} Station`}
            </h2>
            <div className="mix-modal-meta">
              {snapshot ? stationScopeCopy(snapshot) : ''}
              {snapshot?.message ? ` — ${snapshot.message}` : ''}
            </div>
          </div>
          <div className="mix-modal-actions">
            <button
              type="button"
              className="btn btn--sm btn--secondary"
              onClick={onRefresh}
              disabled={loading}
              aria-busy={loading || undefined}
            >
              {loading ? 'Refreshing…' : 'Refresh'}
            </button>
            <button type="button" className="mix-modal-close" aria-label="Close" onClick={onClose}>
              ✕
            </button>
          </div>
        </div>

        {syncStatusBase ? (
          <SyncStatus
            statusBase={syncStatusBase}
            progress={syncProgress}
            visible={Boolean(syncing)}
          />
        ) : null}

        {error ? (
          <p className="station-modal-error" role="alert">
            {error}
          </p>
        ) : null}

        {unavailable > 0 ? (
          <p className="station-modal-note" role="status">
            {unavailable} of {total} are referenced by the library but missing from disk.
          </p>
        ) : null}

        {total > 0 ? (
          <div className="mix-modal-selbar" id="station-modal-selbar" style={{ display: 'flex' }}>
            <label className="mix-selbar-all">
              <input
                type="checkbox"
                id="station-select-all"
                checked={bar.selectAllChecked}
                onChange={(e) => onSelectAll(mixSetAllSelected(total, e.target.checked))}
              />{' '}
              Select all
            </label>
            <span className="mix-sel-count" id="station-sel-count">
              {bar.countLabel}
            </span>
            <span className="mix-selbar-spacer" />
            <button type="button" className="btn btn--sm btn--secondary" onClick={onClearSelection}>
              Clear
            </button>
            <button
              type="button"
              className="btn btn--sm btn--secondary"
              disabled={disabled}
              onClick={onPlaySelected}
            >
              ▶ Play selected
            </button>
            <button
              type="button"
              className="btn btn--sm btn--secondary"
              id="station-dl-selected"
              disabled={disabled}
              onClick={onDownloadSelected}
            >
              Download selected
            </button>
            <button
              type="button"
              className="btn btn--sm btn--primary"
              id="station-sync-selected"
              disabled={disabled || Boolean(syncing)}
              aria-busy={syncing || undefined}
              onClick={onSyncSelected}
            >
              {/* the finite scope, in the label itself */}
              {snapshot && selected.length === total
                ? stationSyncCopy(snapshot)
                : `Sync selected (${selected.length})`}
            </button>
          </div>
        ) : null}

        {chosen.length > 0 ? (
          <p className="station-modal-note" role="status">
            {stationAcquisitionNote(chosen)}
          </p>
        ) : null}

        <div className="mix-modal-body" id="station-modal-tracks">
          {loading && !snapshot ? (
            <div className="discover-empty">
              <p>Building this station…</p>
            </div>
          ) : snapshot && snapshot.status !== 'ok' ? (
            <div className="discover-empty">
              <p>{snapshot.message || 'This station has nothing to preview.'}</p>
            </div>
          ) : (
            <CompactPlaylist
              tracks={tracks}
              selectable
              selected={selected}
              onToggle={onToggleTrack}
              onPlay={onPlayTrack}
              playingIndex={playingIndex}
            />
          )}
        </div>
      </div>
    </div>
  );
}
