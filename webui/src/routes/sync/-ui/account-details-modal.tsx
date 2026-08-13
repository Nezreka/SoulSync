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
 * DECLARED DIVERGENCE (narrowed): the vanilla's footer is assembled from
 * typeof-guarded optional globals returning HTML strings
 * (playlistOrganizeToggleHtml, playlistModalDownloadSyncFooterHtml,
 * loadPlaylistOrganizePreferenceIntoModal, 1938-1956), which a React tree
 * cannot host. The port originally rendered only the documented fallback (the
 * '📥 Download Missing Tracks' button) — which LOST the Sync button (Boulder's
 * report). The sync half of the footer is now re-rendered natively: the
 * sync-mode <select id="sync-mode-<id>"> (startPlaylistSync reads that exact
 * id, downloads.js:3848, so mode selection works unchanged) and the
 * <button id="sync-btn-<id>"> the ENGINE mutates imperatively while syncing
 * (adopted-region idiom, like the sync-status block above). Only the
 * quality-profile/organize toggle remains deferred. In standalone mode the
 * vanilla renders no sync footer at all (shared-helpers.js:1698) — same here
 * via useStandalone.
 */

import type { AccountPlaylistRow } from '../-sync.accounts';
import type { AccountPlaylistTracks } from '../-sync.api';

import { formatDuration } from '../-sync.core';
import { useStandalone } from '../-sync.use-standalone';

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
  /** Start THIS playlist's sync (the engine's startPlaylistSync, which reads
   *  the sync-mode select rendered beside the button). */
  onSync: () => void;
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
  onSync,
}: AccountDetailsModalProps) {
  const standalone = useStandalone();
  const syncing = Boolean(window.isPlaylistSyncing?.(playlistId));
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
            {!standalone ? (
              <>
                <select
                  id={`sync-mode-${playlistId}`}
                  className="playlist-modal-sync-mode"
                  defaultValue=""
                  title="Default uses your Settings > Playlist sync mode. Replace overwrites the server playlist; Reconcile edits it in place (keeps custom image/description); Append only adds new tracks."
                >
                  <option value="">Sync mode: default</option>
                  <option value="replace">Replace</option>
                  <option value="reconcile">Reconcile (keep image/desc)</option>
                  <option value="append">Append only</option>
                </select>
                {/* ADOPTED: the engine rewrites this button's disabled/text
                    while the sync runs (downloads.js:3873-3877). */}
                <button
                  type="button"
                  id={`sync-btn-${playlistId}`}
                  className="playlist-modal-btn playlist-modal-btn-primary"
                  disabled={syncing}
                  onClick={onSync}
                >
                  {syncing ? '⏳ Syncing...' : 'Sync Playlist'}
                </button>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
