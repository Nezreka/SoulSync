/**
 * The engine-driven playlist card, shared by the Spotify and Deezer-ARL tabs
 * (renderSpotifyPlaylists 1645-1664, renderDeezerArlPlaylists 2502-2521).
 *
 * ADOPTED REGION — read this before changing any id or class here.
 *
 * React renders this skeleton; the VANILLA DOWNLOAD ENGINE then paints into it
 * by selector and React must not paint over it:
 *   #progress-<id>            updateCardToSyncing      (downloads.js 4139)
 *   .playlist-card-status     updateCardToDefault      (downloads.js 4202)
 *   #action-btn-<id>          updatePlaylistCardUI     (sync-spotify.js 1679)
 *   #progress-btn-<id>        updatePlaylistCardUI
 *   .download-complete on the card itself
 *
 * That is why the status span and the two buttons render their INITIAL text
 * only and are never re-rendered from React state: the engine owns them from
 * the moment a download or sync starts. The vanilla has the same contract —
 * it rebuilds this innerHTML wholesale on refresh and then calls
 * checkForActiveProcesses() to re-apply (1618-1621).
 *
 * The `selectable` flag is real drift, not a convenience: Spotify's card has
 * an onclick that toggles selection (1646); Deezer-ARL's has none at all
 * (2503).
 */

import type { AccountPlaylistRow } from '../-sync.accounts';

import { PlaylistArt } from './playlist-art';
import { SourceIcon } from './source-icon';

export interface AccountPlaylistCardProps {
  /** The id the ENGINE uses — raw for Spotify, `deezer_arl_<n>` for ARL. */
  cardId: string;
  row: AccountPlaylistRow;
  /**
   * The SOURCE this card belongs to ('spotify' | 'deezer'), used for the brand
   * mark drawn when the row carries no `image_url`. The art tile is a NEW
   * sibling — it deliberately adds no id and no class the engine selects on, so
   * the adopted contract above is untouched.
   */
  glyph: string;
  statusClass: string;
  statusLabel: string;
  /** Spotify only (1646); ARL cards do not toggle (2503). */
  selectable: boolean;
  selected: boolean;
  onToggleSelect?: () => void;
  onOpenDetails: () => void;
  onViewProgress: () => void;
  /** ARL adds a second class to the same element (2503). */
  extraClassName?: string;
}

export function AccountPlaylistCard({
  cardId,
  row,
  glyph,
  statusClass,
  statusLabel,
  selectable,
  selected,
  onToggleSelect,
  onOpenDetails,
  onViewProgress,
  extraClassName,
}: AccountPlaylistCardProps) {
  return (
    <div
      className={`playlist-card${extraClassName ? ` ${extraClassName}` : ''}${
        selected ? ' selected' : ''
      }`}
      data-playlist-id={cardId}
      onClick={
        selectable
          ? (event) => {
              // 1799: a click that lands on either action button never
              // toggles. This guard is DEAD in both worlds and a mutation
              // survivor on it is equivalent: the only clickable children are
              // the two buttons, and both call stopPropagation first, so this
              // handler never sees a BUTTON target. Transcribed because the
              // vanilla has it, not because it fires.
              if ((event.target as HTMLElement).tagName === 'BUTTON') return;
              onToggleSelect?.();
            }
          : undefined
      }
    >
      <div className="playlist-card-main">
        <div className="playlist-card-art">
          <PlaylistArt url={row.image_url} fallback={<SourceIcon source={glyph} />} />
        </div>
        <div className="playlist-card-content">
          <div className="playlist-card-name">{row.name}</div>
          <div className="playlist-card-info">
            <span>{row.track_count} tracks</span>
            {' • '}
            <span className={`playlist-card-status ${statusClass}`}>{statusLabel}</span>
          </div>
          {/* The engine writes this element's innerHTML. Empty by design. */}
          <div className="sync-progress-indicator" id={`progress-${cardId}`} />
        </div>
        <div className="playlist-card-actions">
          <button
            type="button"
            id={`action-btn-${cardId}`}
            onClick={(event) => {
              event.stopPropagation();
              onOpenDetails();
            }}
          >
            Sync / Download
          </button>
          <button
            type="button"
            id={`progress-btn-${cardId}`}
            className="view-progress-btn hidden"
            onClick={(event) => {
              event.stopPropagation();
              onViewProgress();
            }}
          >
            View Progress
          </button>
        </div>
      </div>
    </div>
  );
}
