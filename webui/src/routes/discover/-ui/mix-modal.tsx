import type { ReactNode } from 'react';

import { useId } from 'react';

import { useAccessibleModal } from '@/components/dialog';
import { AddToPlaylistButton, openAddToPlaylist } from '@/features/playlists/add-to-playlist';
import addStyles from '@/features/playlists/add-to-playlist.module.css';

import type { CompactRow, DiscoverMix, MixAction } from '../-discover.mixes';

import {
  compactRows,
  MIX_COVER_PLACEHOLDER,
  mixActions,
  mixCoverArt,
  mixMetaLine,
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
 * oct 2026 redesign: the ids and the poller hooks all stayed, the look did
 * not. the header is cover art + eyebrow + title + one filled Play, select-all
 * sits in the column head over the boxes, and the bar with the selection
 * actions only floats in once something is ticked. the station preview and the
 * search playlist preview share MixModalHeader and TrackListHead.
 *
 * `selectable` is opt-in per call and it is not cosmetic: it adds the checkbox
 * and the `has-select` class the grid reflows on. A supplied onPlay handler
 * also enables playback without requiring selection controls.
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
      className={[
        'discover-playlist-track-compact',
        row.selectable && 'has-select',
        checked && 'is-selected',
      ]
        .filter(Boolean)
        .join(' ')}
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
        {(row.selectable || onPlay) && (
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
      <AddToPlaylistButton
        track={{
          track_name: row.name,
          artist_name: row.artist,
          album_name: row.album,
          image_url: row.cover === MIX_COVER_PLACEHOLDER ? null : row.cover,
        }}
        className={`track-compact-add ${addStyles.button}`}
        size={15}
      />
    </div>
  );
}

// ── The shared shell pieces ──────────────────────────────────────────────────

const ICON = { width: 15, height: 15, viewBox: '0 0 24 24', 'aria-hidden': true } as const;

export function PlayIcon() {
  return (
    <svg {...ICON} width={13} height={13} fill="currentColor">
      <path d="M7 4.5v15l13-7.5z" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg {...ICON} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 20h14" />
    </svg>
  );
}

function SyncIcon() {
  return (
    <svg {...ICON} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M20 11a8 8 0 0 0-14.5-4.5L4 8m0-4v4h4M4 13a8 8 0 0 0 14.5 4.5L20 16m0 4v-4h-4" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg
      {...ICON}
      width={16}
      height={16}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
    >
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

/** an action's icon from what it does, so the labels can stay plain words. */
function actionIcon(onclick: string): ReactNode {
  if (onclick.startsWith('play')) return <PlayIcon />;
  if (onclick.startsWith('download')) return <DownloadIcon />;
  if (onclick.startsWith('sync')) return <SyncIcon />;
  return null;
}

export interface MixModalHeaderProps {
  titleId: string;
  /** the small label over the title: Mix, Station, Playlist · Deezer. */
  eyebrow: string;
  title: ReactNode;
  /** the one-line why under the title. */
  description?: ReactNode;
  meta?: ReactNode;
  /** one cover, or four for a mosaic. none shows a plain tile. */
  covers: string[];
  actions?: ReactNode;
  onClose: () => void;
}

/** the header all three track modals share: art, eyebrow, title, actions. */
export function MixModalHeader({
  titleId,
  eyebrow,
  title,
  description,
  meta,
  covers,
  actions,
  onClose,
}: MixModalHeaderProps) {
  return (
    <div className="mix-modal-header">
      <div
        className={covers.length === 4 ? 'mix-modal-cover is-mosaic' : 'mix-modal-cover'}
        aria-hidden="true"
      >
        {covers.length > 0 ? (
          covers.map((src) => <img key={src} src={src} alt="" loading="lazy" />)
        ) : (
          <span className="mix-modal-cover-blank">♪</span>
        )}
      </div>
      <div className="mix-modal-heading">
        <div className="mix-modal-eyebrow">{eyebrow}</div>
        <h2 className="mix-modal-title" id={titleId}>
          {title}
        </h2>
        {description ? <div className="mix-modal-subtitle">{description}</div> : null}
        <div className="mix-modal-meta">{meta}</div>
        {actions ? <div className="mix-modal-actions">{actions}</div> : null}
      </div>
      <button type="button" className="mix-modal-close" aria-label="Close" onClick={onClose}>
        <CloseIcon />
      </button>
    </div>
  );
}

export interface TrackListHeadProps {
  /** the select-all box. left out, the list has no checkbox column. */
  selectAll?: { id: string; checked: boolean; onChange: (checked: boolean) => void };
}

/** the column labels over the rows. select-all lives here, over the boxes it ticks. */
export function TrackListHead({ selectAll }: TrackListHeadProps) {
  return (
    <div className={selectAll ? 'mix-modal-colhead has-select' : 'mix-modal-colhead'}>
      {selectAll ? (
        <label className="mix-selbar-all">
          <input
            type="checkbox"
            id={selectAll.id}
            aria-label="Select all"
            checked={selectAll.checked}
            onChange={(e) => selectAll.onChange(e.target.checked)}
          />
        </label>
      ) : null}
      <span className="mix-colhead-num">#</span>
      <span />
      <span>Title</span>
      <span className="mix-colhead-album">Album</span>
      <span className="mix-colhead-time">Time</span>
      <span />
    </div>
  );
}

// ── The selection bar ────────────────────────────────────────────────────────

export interface MixSelectionBarProps {
  selected: number[];
  onClearSelection: () => void;
  onDownloadSelected: () => void;
  /** the ticked rows onto a playlist; anchor is the button */
  onAddSelectedToPlaylist?: (anchor: HTMLElement) => void;
}

/**
 * the floating bar that shows up once something is ticked (behaviour 4792-4805).
 *
 * the count and download ids are the vanilla's, kept so nothing that reads
 * them by id goes blind. Clear unticks every row AND the select-all box, it is
 * not the same as select-all(false), which only exists once all are ticked.
 */
export function MixSelectionBarView({
  selected,
  onClearSelection,
  onDownloadSelected,
  onAddSelectedToPlaylist,
}: MixSelectionBarProps) {
  const bar = mixSelectionBar(selected.length, 0);
  return (
    <div className="mix-modal-selbar" id="mix-modal-selbar" role="region" aria-label="Selection">
      <span className="mix-sel-count" id="mix-sel-count">
        {bar.countLabel}
      </span>
      <button type="button" className="btn btn--sm mix-selbar-clear" onClick={onClearSelection}>
        Clear
      </button>
      {onAddSelectedToPlaylist ? (
        <button
          type="button"
          className="btn btn--sm btn--secondary"
          onClick={(e) => onAddSelectedToPlaylist(e.currentTarget)}
        >
          Add to playlist
        </button>
      ) : null}
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
  const rows = tracks ? compactRows(tracks) : [];
  const hasTracks = rows.length > 0;
  const titleId = useId();
  // escape, focus trap, initial focus, focus restore, scroll lock. none of it
  // was here: escape did nothing and the thing was a plain div.
  const { ref, onBackdropClick } = useAccessibleModal<HTMLDivElement>(onClose);

  return (
    <div className="modal-overlay" id="mix-modal-overlay" onClick={onBackdropClick}>
      <div
        className={selected.length > 0 ? 'mix-modal has-selection' : 'mix-modal'}
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <MixModalHeader
          titleId={titleId}
          eyebrow="Mix"
          title={mix.title}
          description={mix.subtitle}
          // empty, not '0 tracks', until a lazy mix has fetched (4981)
          meta={tracks ? mixMetaLine(rows) : ''}
          covers={mixCoverArt(rows)}
          onClose={onClose}
          actions={actions.map((action) => {
            // Resolving a 50-track mix against the library takes a couple of
            // seconds and this button used to say nothing for all of it.
            const busy = playing && action.onclick.startsWith('play');
            return (
              <button
                type="button"
                key={action.label}
                // play leads and is the one filled button. the rest stay quiet
                // so the header has a single obvious thing to press.
                className={
                  action.onclick.startsWith('play')
                    ? 'btn btn--sm btn--primary'
                    : 'btn btn--sm btn--secondary'
                }
                // The live sync poller re-enables the button BY THIS ID (5035).
                id={action.isSync && base ? `${base}-sync-btn` : undefined}
                disabled={busy}
                aria-busy={busy || undefined}
                onClick={() => onAction(action)}
              >
                {actionIcon(action.onclick)}
                {busy ? 'Starting…' : action.label.replace(/^▶\s*/, '')}
              </button>
            );
          })}
        />

        {syncStatusOverride ??
          (base ? (
            <SyncStatus statusBase={base} progress={syncProgress} visible={Boolean(syncing)} />
          ) : null)}

        {hasTracks && !loading && !error ? (
          <TrackListHead
            selectAll={{
              id: 'mix-select-all',
              // total > 0 as well as count === total: without it an empty list
              // shows select-all ticked, because 0 === 0.
              checked: mixSelectionBar(selected.length, rows.length).selectAllChecked,
              onChange: (checked) => onSelectAll(mixSetAllSelected(rows.length, checked)),
            }}
          />
        ) : null}

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

        {hasTracks && selected.length > 0 ? (
          <MixSelectionBarView
            selected={selected}
            onClearSelection={onClearSelection}
            onDownloadSelected={onDownloadSelected}
            onAddSelectedToPlaylist={(anchor) => {
              const picked = new Set(selected);
              openAddToPlaylist(
                rows
                  .filter((row) => picked.has(row.index))
                  .map((row) => ({
                    track_name: row.name,
                    artist_name: row.artist,
                    album_name: row.album,
                  })),
                anchor,
              );
            }}
          />
        ) : null}
      </div>
    </div>
  );
}
