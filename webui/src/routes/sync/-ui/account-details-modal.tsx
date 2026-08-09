/**
 * The account playlist's track-list modal — showPlaylistDetailsModal
 * (sync-spotify.js 1878-1958) and its Deezer-ARL twin
 * (showDeezerArlPlaylistDetailsModal, sync-services.js 2572-2660).
 *
 * The twins are byte-identical apart from four things, all props here: the
 * element id, the organize source ('spotify' vs 'deezer'), the ARL footer's
 * closeBeforeDownload, and ARL's tolerance for missing fields (its track count
 * falls back to tracks.length and its owner to '', 2592-2593).
 *
 * ADOPTED REGION — the `.playlist-modal-sync-status` block is hidden markup the
 * SYNC engine unhides and fills (#modal-total/-matched/-failed/-percentage-<id>),
 * exactly like the card's progress indicator. React renders it hidden and never
 * touches it again.
 *
 * DECLARED DIVERGENCE: the vanilla's footer is assembled from three
 * typeof-guarded optional globals (playlistOrganizeToggleHtml,
 * playlistModalDownloadSyncFooterHtml, loadPlaylistOrganizePreferenceIntoModal,
 * 1938-1956). Those live in wishlist-tools and return HTML STRINGS; a React
 * tree cannot host them without dangerouslySetInnerHTML, which this port does
 * not use anywhere. The port renders the vanilla's own DOCUMENTED FALLBACK —
 * the single '📥 Download Missing Tracks' button — which is what the page shows
 * whenever those globals are absent. The quality-profile/organize toggle is
 * deferred with the same reasoning already recorded for the mirrored card and
 * the discovery-modal footer.
 */

import type { AccountPlaylistRow } from '../-sync.accounts';
import type { AccountPlaylistTracks } from '../-sync.api';

import { formatDuration } from '../-sync.core';

/** formatArtists' contract, as the vanilla feeds it (1927). */
function formatArtists(artists: unknown): string {
  if (Array.isArray(artists)) {
    return artists
      .map((a) => (typeof a === 'object' && a !== null ? ((a as { name?: string }).name ?? '') : a))
      .join(', ');
  }
  return typeof artists === 'string' ? artists : '';
}

export interface AccountDetailsModalProps {
  /** '#playlist-details-modal' (1883) or '#deezer-arl-playlist-details-modal' (2576). */
  modalId: string;
  /** The id the engine keys by — prefixed for ARL (2581). */
  playlistId: string;
  row: AccountPlaylistRow;
  detail: AccountPlaylistTracks;
  /**
   * The header count. REAL DRIFT, so each tab computes its own: Spotify prints
   * `playlist.track_count` raw (1901), where ARL falls back with `||` so a zero
   * count shows tracks.length instead (2592).
   */
  trackCount: number;
  onClose: () => void;
  /** ARL closes the modal FIRST (2639); Spotify does not (1948). */
  closeBeforeDownload: boolean;
  onDownloadMissing: () => void;
}

export function AccountDetailsModal({
  modalId,
  playlistId,
  row,
  detail,
  trackCount,
  onClose,
  closeBeforeDownload,
  onDownloadMissing,
}: AccountDetailsModalProps) {
  const tracks = detail.tracks ?? [];
  // ARL tolerates a missing owner (2593); Spotify prints the row's (1902).
  const owner = detail.owner ?? row.owner ?? '';
  const description = detail.description ?? row.description;

  return (
    <div id={modalId} className="modal-overlay" style={{ display: 'flex' }}>
      <div className="modal-container playlist-modal">
        <div className="playlist-modal-header">
          <div className="playlist-header-content">
            <h2>{detail.name ?? row.name}</h2>
            <div className="playlist-quick-info">
              <span className="playlist-track-count">{trackCount} tracks</span>
              <span className="playlist-owner">by {owner}</span>
            </div>
            {/* Hidden until the sync engine fills it (1905-1912). */}
            <div
              className="playlist-modal-sync-status"
              id={`modal-sync-status-${playlistId}`}
              style={{ display: 'none' }}
            >
              <span className="sync-stat total-tracks">
                ♪ <span id={`modal-total-${playlistId}`}>0</span>
              </span>
              <span className="sync-separator">/</span>
              <span className="sync-stat matched-tracks">
                ✓ <span id={`modal-matched-${playlistId}`}>0</span>
              </span>
              <span className="sync-separator">/</span>
              <span className="sync-stat failed-tracks">
                ✗ <span id={`modal-failed-${playlistId}`}>0</span>
              </span>
              <span className="sync-stat percentage">
                (<span id={`modal-percentage-${playlistId}`}>0</span>%)
              </span>
            </div>
          </div>
          <span
            className="playlist-modal-close"
            onClick={onClose}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') onClose();
            }}
          >
            ×
          </span>
        </div>

        <div className="playlist-modal-body">
          {description ? <div className="playlist-description">{description}</div> : null}
          <div className="playlist-tracks-container">
            <div className="playlist-tracks-list">
              {tracks.map((track, index) => (
                <div className="playlist-track-item" key={`${track.id ?? index}-${index}`}>
                  <span className="playlist-track-number">{index + 1}</span>
                  <div className="playlist-track-info">
                    <div className="playlist-track-name">{track.name}</div>
                    <div className="playlist-track-artists">{formatArtists(track.artists)}</div>
                  </div>
                  <div className="playlist-track-duration">{formatDuration(track.duration_ms)}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="playlist-modal-footer">
          <div className="playlist-modal-footer-left" />
          <div className="playlist-modal-footer-right">
            <button
              type="button"
              className="playlist-modal-btn playlist-modal-btn-secondary"
              onClick={onClose}
            >
              Close
            </button>
            <button
              type="button"
              className="playlist-modal-btn playlist-modal-btn-tertiary"
              onClick={() => {
                if (closeBeforeDownload) onClose();
                onDownloadMissing();
              }}
            >
              📥 Download Missing Tracks
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
