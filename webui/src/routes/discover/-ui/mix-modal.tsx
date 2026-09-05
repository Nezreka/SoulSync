import { useId } from 'react';

import { useAccessibleModal } from '@/components/dialog';

import type { CompactRow, DiscoverMix, MixAction } from '../-discover.mixes';

import {
  compactRows,
  mixActions,
  mixSelectionBar,
  mixSetAllSelected,
  mixStatusBase,
} from '../-discover.mixes';
import { SyncStatus } from './sync-status';

/**
 * The compact track list, and the mix modal built around it.
 *
 * Transcribed from discover.js 4692-4729 for the rows, 4931-5040 for the modal
 * shell, and 4776-4806 for the selection-bar behaviour (#1079).
 *
 * HISTORY. The first version of this modal was invented — `.discover-mix-modal`
 * on a playlist-modal skeleton, no Clear button, `action-button` classes — and
 * passed a 24/24 mutation pass, because its tests asserted the invention. The
 * vanilla modal is `.mix-modal` inside `#mix-modal-overlay`: an eyebrow
 * subtitle ABOVE the title, actions as `btn btn--sm` buttons in the header (the
 * Sync one carrying `${base}-sync-btn` so the live poller can find it), a
 * selection bar with ids `#mix-select-all` / `#mix-sel-count` /
 * `#mix-dl-selected`, and the track list in `#mix-modal-tracks`.
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
  onPlay?: (index: number) => void;
  /** the row whose play is still resolving, so a second tap can't double it. */
  playingIndex?: number | null;
}

export function CompactPlaylist({
  tracks,
  selectable = false,
  selected = [],
  onToggle,
  onPlay,
  playingIndex = null,
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
          onPlay={onPlay}
          busy={playingIndex === row.index}
        />
      ))}
    </div>
  );
}

function CompactTrackRow({
  row,
  checked,
  onToggle,
  onPlay,
  busy,
}: {
  row: CompactRow;
  checked: boolean;
  onToggle?: (index: number) => void;
  onPlay?: (index: number) => void;
  busy?: boolean;
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
            // it plays the whole track through the shared player, so it says
            // Play. calling it Preview promised something we never built, and
            // for months the handler was empty anyway.
            title={`Play ${row.name}`}
            aria-label={`Play ${row.name}`}
            disabled={busy}
            aria-busy={busy || undefined}
            onClick={(e) => {
              e.stopPropagation();
              onPlay?.(row.index);
            }}
          >
            {busy ? '…' : '▶'}
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
  onClearSelection: () => void;
  onDownloadSelected: () => void;
}

/**
 * The selection bar (4998-5004, behaviour 4792-4805).
 *
 * The ids are load-bearing: `_updateMixSelBar` writes count and label into them
 * by id, and the vanilla's own handlers will keep doing so until PR 2 deletes
 * them. Clear unticks every row AND the select-all box — it is not the same as
 * select-all(false), which only exists once something is ticked.
 */
export function MixSelectionBarView({
  total,
  selected,
  onSelectAll,
  onClearSelection,
  onDownloadSelected,
}: MixSelectionBarProps) {
  const bar = mixSelectionBar(selected.length, total);
  return (
    <div className="mix-modal-selbar" id="mix-modal-selbar" style={{ display: 'flex' }}>
      <label className="mix-selbar-all">
        <input
          type="checkbox"
          id="mix-select-all"
          // total > 0 as well as count === total: without it an empty list
          // shows select-all ticked, because 0 === 0.
          checked={bar.selectAllChecked}
          onChange={(e) => onSelectAll(mixSetAllSelected(total, e.target.checked))}
        />{' '}
        Select all
      </label>
      <span className="mix-sel-count" id="mix-sel-count">
        {bar.countLabel}
      </span>
      <span className="mix-selbar-spacer" />
      <button type="button" className="btn btn--sm btn--secondary" onClick={onClearSelection}>
        Clear
      </button>
      <button
        type="button"
        className="btn btn--sm btn--primary"
        id="mix-dl-selected"
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
  /** Undefined while a lazy mix (decades, ListenBrainz) is still fetching. */
  tracks?: unknown[];
  loading?: boolean;
  error?: boolean;
  selected: number[];
  /**
   * A section's own status markup, which REPLACES the generic block. The
   * ListenBrainz playlists use -sync-total/-sync-matched spans instead of the
   * generic -sync-completed/-sync-pending, and the poller writes by those ids.
   */
  syncStatusOverride?: React.ReactNode;
  syncing?: boolean;
  syncProgress?: Parameters<typeof SyncStatus>[0]['progress'];
  onClose: () => void;
  onAction: (action: MixAction) => void;
  onSelectAll: (indices: number[]) => void;
  onClearSelection: () => void;
  onToggleTrack: (index: number) => void;
  onPlayTrack: (index: number) => void;
  /** the row whose play is still resolving. */
  playingIndex?: number | null;
  /** the mix's own Play action is resolving against the library. */
  playing?: boolean;
  onDownloadSelected: () => void;
}

export function MixModal({
  mix,
  tracks,
  loading,
  error,
  selected,
  syncStatusOverride,
  syncing,
  syncProgress,
  onClose,
  onAction,
  onSelectAll,
  onClearSelection,
  onToggleTrack,
  onPlayTrack,
  playingIndex = null,
  playing = false,
  onDownloadSelected,
}: MixModalProps) {
  const actions = mixActions(mix);
  const base = mixStatusBase(mix);
  const hasTracks = Boolean(tracks && tracks.length > 0);
  const titleId = useId();
  // escape, focus trap, initial focus, focus restore, scroll lock. none of it
  // was here: escape did nothing and the thing was a plain div.
  const { ref, onBackdropClick } = useAccessibleModal<HTMLDivElement>(onClose);

  return (
    <div className="modal-overlay" id="mix-modal-overlay" onClick={onBackdropClick}>
      <div
        className="mix-modal"
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="mix-modal-header">
          <div>
            {/* The EYEBROW sits above the title, falling back to 'Mix' (4979). */}
            <div className="mix-modal-subtitle">{mix.subtitle || 'Mix'}</div>
            <h2 className="mix-modal-title" id={titleId}>
              {mix.title}
            </h2>
            {/* Empty, not '0 tracks', until a lazy mix has fetched (4981). */}
            <div className="mix-modal-meta">{tracks ? `${tracks.length} tracks` : ''}</div>
          </div>
          <div className="mix-modal-actions">
            {actions.map((action) => {
              // Resolving a 50-track mix against the library takes a couple of
              // seconds and this button used to say nothing for all of it.
              const busy = playing && action.onclick.startsWith('play');
              return (
                <button
                  type="button"
                  key={action.label}
                  className={
                    action.primary ? 'btn btn--sm btn--primary' : 'btn btn--sm btn--secondary'
                  }
                  // The live sync poller re-enables the button BY THIS ID (5035).
                  id={action.isSync && base ? `${base}-sync-btn` : undefined}
                  disabled={busy}
                  aria-busy={busy || undefined}
                  onClick={() => onAction(action)}
                >
                  {busy ? 'Starting…' : action.label}
                </button>
              );
            })}
            <button type="button" className="mix-modal-close" aria-label="Close" onClick={onClose}>
              ✕
            </button>
          </div>
        </div>

        {syncStatusOverride ??
          (base ? (
            <SyncStatus statusBase={base} progress={syncProgress} visible={Boolean(syncing)} />
          ) : null)}

        {/* Hidden until there are tracks to select (5011-5014). */}
        {hasTracks && (
          <MixSelectionBarView
            total={(tracks as unknown[]).length}
            selected={selected}
            onSelectAll={onSelectAll}
            onClearSelection={onClearSelection}
            onDownloadSelected={onDownloadSelected}
          />
        )}

        <div className="mix-modal-body" id="mix-modal-tracks">
          {loading ? (
            <div className="discover-empty">
              <p>Loading tracks…</p>
            </div>
          ) : error ? (
            <div className="discover-empty">
              <p>Failed to load tracks</p>
            </div>
          ) : (
            tracks && (
              <CompactPlaylist
                tracks={tracks}
                selectable
                selected={selected}
                onToggle={onToggleTrack}
                onPlay={onPlayTrack}
                playingIndex={playingIndex}
              />
            )
          )}
        </div>
      </div>
    </div>
  );
}
