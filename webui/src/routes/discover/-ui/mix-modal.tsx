import type { CompactRow, DiscoverMix, MixAction } from '../-discover.mixes';

import {
  compactRows,
  mixActions,
  mixSelectionBar,
  mixSetAllSelected,
  MIX_SEL_IDLE_LABEL,
} from '../-discover.mixes';

/**
 * The compact track list, and the mix modal built around it.
 *
 * Transcribed from discover.js 4692-4729 for the rows and 4772-4806 for the
 * selection bar (#1079).
 *
 * `selectable` is opt-in per call and it is not cosmetic: it adds the checkbox
 * AND the per-row preview button AND the `has-select` class the grid reflows on.
 * The plain playlist renderers pass nothing and get none of it.
 */

export interface CompactPlaylistProps {
  tracks: unknown[];
  selectable?: boolean;
  selected?: number[];
  onToggle?: (index: number) => void;
  onPreview?: (index: number) => void;
}

export function CompactPlaylist({
  tracks,
  selectable = false,
  selected = [],
  onToggle,
  onPreview,
}: CompactPlaylistProps) {
  const rows = compactRows(tracks, selectable);
  const chosen = new Set(selected);
  return (
    <div className="discover-playlist-tracks-compact">
      {rows.map((row) => (
        <CompactTrackRow
          key={row.index}
          row={row}
          checked={chosen.has(row.index)}
          onToggle={onToggle}
          onPreview={onPreview}
        />
      ))}
    </div>
  );
}

function CompactTrackRow({
  row,
  checked,
  onToggle,
  onPreview,
}: {
  row: CompactRow;
  checked: boolean;
  onToggle?: (index: number) => void;
  onPreview?: (index: number) => void;
}) {
  return (
    <div
      className={
        row.selectable
          ? 'discover-playlist-track-compact has-select'
          : 'discover-playlist-track-compact'
      }
      data-track-index={row.index}
    >
      {row.selectable && (
        <div className="track-compact-select">
          <input
            type="checkbox"
            className="track-compact-check"
            data-track-index={row.index}
            checked={checked}
            aria-label={`Select ${row.name}`}
            onChange={() => onToggle?.(row.index)}
            // The row itself is clickable; without this, ticking a box would
            // also fire whatever the row does.
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
      <div className="track-compact-number">{row.position}</div>
      <div className="track-compact-image">
        <img src={row.cover} alt={row.album} loading="lazy" />
        {row.selectable && (
          <button
            type="button"
            className="track-compact-play"
            title="Preview"
            onClick={(e) => {
              e.stopPropagation();
              onPreview?.(row.index);
            }}
          >
            ▶
          </button>
        )}
      </div>
      <div className="track-compact-info">
        <div className="track-compact-name">{row.name}</div>
        <div className="track-compact-artist">{row.artist}</div>
      </div>
      <div className="track-compact-album">{row.album}</div>
      {/* EMPTY for an unknown length — "0:00" claims a fact we do not have. */}
      <div className="track-compact-duration">{row.duration}</div>
    </div>
  );
}

// ── The selection bar ────────────────────────────────────────────────────────

export interface MixSelectionBarProps {
  total: number;
  selected: number[];
  onSelectAll: (indices: number[]) => void;
  onDownloadSelected: () => void;
}

export function MixSelectionBarView({
  total,
  selected,
  onSelectAll,
  onDownloadSelected,
}: MixSelectionBarProps) {
  const bar = mixSelectionBar(selected.length, total);
  return (
    <div className="mix-selection-bar">
      <label className="mix-select-all">
        <input
          type="checkbox"
          // total > 0 as well as count === total: without it an empty list
          // shows select-all ticked, because 0 === 0.
          checked={bar.selectAllChecked}
          aria-label="Select all tracks"
          onChange={(e) => onSelectAll(mixSetAllSelected(total, e.target.checked))}
        />
        <span className="mix-select-count">{bar.countLabel}</span>
      </label>
      <button
        type="button"
        className="mix-download-selected"
        disabled={bar.downloadDisabled}
        onClick={onDownloadSelected}
      >
        {bar.downloadLabel}
      </button>
    </div>
  );
}

// ── The modal ────────────────────────────────────────────────────────────────

export interface MixModalProps {
  mix: DiscoverMix;
  tracks: unknown[];
  selected: number[];
  /** The live sync panel, when this mix has one. */
  syncStatus?: React.ReactNode;
  onClose: () => void;
  onAction: (action: MixAction) => void;
  onSelectAll: (indices: number[]) => void;
  onToggleTrack: (index: number) => void;
  onPreviewTrack: (index: number) => void;
  onDownloadSelected: () => void;
}

export function MixModal({
  mix,
  tracks,
  selected,
  syncStatus,
  onClose,
  onAction,
  onSelectAll,
  onToggleTrack,
  onPreviewTrack,
  onDownloadSelected,
}: MixModalProps) {
  const actions = mixActions(mix);
  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-container playlist-modal discover-mix-modal">
        <div className="playlist-modal-header">
          <div className="playlist-header-content" style={{ width: '100%' }}>
            <h2>{mix.title}</h2>
            {mix.subtitle && <div className="playlist-quick-info">{mix.subtitle}</div>}
          </div>
          <button
            type="button"
            className="playlist-modal-close"
            aria-label="Close"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <div className="playlist-modal-body">
          {/* A mix with neither its own actions nor a syncKey gets NO buttons.
              That is the vanilla's behaviour, not an oversight — there is
              nothing for Download or Sync to act on. */}
          {actions.length > 0 && (
            <div className="mix-modal-actions">
              {actions.map((action) => (
                <button
                  type="button"
                  key={action.label}
                  className={action.primary ? 'action-button primary' : 'action-button'}
                  onClick={() => onAction(action)}
                >
                  {action.label}
                </button>
              ))}
            </div>
          )}
          {syncStatus}
          <MixSelectionBarView
            total={tracks.length}
            selected={selected}
            onSelectAll={onSelectAll}
            onDownloadSelected={onDownloadSelected}
          />
          <CompactPlaylist
            tracks={tracks}
            selectable
            selected={selected}
            onToggle={onToggleTrack}
            onPreview={onPreviewTrack}
          />
        </div>
      </div>
    </div>
  );
}

export { MIX_SEL_IDLE_LABEL };
