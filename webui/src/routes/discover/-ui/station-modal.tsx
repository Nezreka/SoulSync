import { useId } from 'react';

import { useAccessibleModal } from '@/components/dialog';

import type { StationSnapshot } from '../-discover.stations';

import { compactRows, mixCoverArt, mixSelectionBar, mixSetAllSelected } from '../-discover.mixes';
import {
  stationAcquisitionNote,
  stationScopeCopy,
  stationSelectionOf,
  stationSyncCopy,
  stationTitle,
} from '../-discover.stations';
import { CompactPlaylist, MixModalHeader, PlayIcon, TrackListHead } from './mix-modal';
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
  const listed =
    total > 0 && (!snapshot || snapshot.status === 'ok' || snapshot.status === 'partial');
  const artistImage = snapshot?.station?.image_url;

  return (
    <div className="modal-overlay" id="station-modal-overlay" onClick={onBackdropClick}>
      <div
        className={
          selected.length > 0 ? 'mix-modal station-modal has-selection' : 'mix-modal station-modal'
        }
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <MixModalHeader
          titleId={titleId}
          eyebrow="Station preview"
          title={snapshot ? stationTitle(snapshot) : `${stationName} Station`}
          meta={
            <>
              {snapshot ? stationScopeCopy(snapshot) : ''}
              {snapshot?.message ? ` — ${snapshot.message}` : ''}
            </>
          }
          covers={artistImage ? [artistImage] : mixCoverArt(compactRows(tracks))}
          onClose={onClose}
          actions={
            <button
              type="button"
              className="btn btn--sm btn--secondary"
              onClick={onRefresh}
              disabled={loading}
              aria-busy={loading || undefined}
            >
              {loading ? 'Refreshing…' : 'Refresh'}
            </button>
          }
        />

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

        {chosen.length > 0 ? (
          <p className="station-modal-note" role="status">
            {stationAcquisitionNote(chosen)}
          </p>
        ) : null}

        {listed ? (
          <TrackListHead
            selectAll={{
              id: 'station-select-all',
              checked: bar.selectAllChecked,
              onChange: (checked) => onSelectAll(mixSetAllSelected(total, checked)),
            }}
          />
        ) : null}

        <div className="mix-modal-body" id="station-modal-tracks">
          {loading && !snapshot ? (
            <div className="discover-empty">
              <p>Building this station…</p>
            </div>
          ) : snapshot && snapshot.status !== 'ok' && snapshot.status !== 'partial' ? (
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

        {/* every action here works on the selection, so the bar only floats in
            once there is one */}
        {listed && selected.length > 0 ? (
          <div
            className="mix-modal-selbar"
            id="station-modal-selbar"
            role="region"
            aria-label="Selection"
          >
            <span className="mix-sel-count" id="station-sel-count">
              {bar.countLabel}
            </span>
            <button
              type="button"
              className="btn btn--sm mix-selbar-clear"
              onClick={onClearSelection}
            >
              Clear
            </button>
            <button type="button" className="btn btn--sm btn--secondary" onClick={onPlaySelected}>
              <PlayIcon />
              Play
            </button>
            <button
              type="button"
              className="btn btn--sm btn--secondary"
              id="station-dl-selected"
              onClick={onDownloadSelected}
            >
              Download
            </button>
            <button
              type="button"
              className="btn btn--sm btn--primary"
              id="station-sync-selected"
              disabled={Boolean(syncing)}
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
      </div>
    </div>
  );
}
