/**
 * The shared discovery modal — ONE component for the nine sources, replacing
 * openYouTubeDiscoveryModal + getModalActionButtons + generateTableRowsFromState
 * (sync-services.js 9302-10095). Source behaviour comes from the config; state
 * lives in useSourceVertical, so the vanilla's hide-don't-remove DOM-as-state
 * dance is simply gone.
 *
 * Day-one knowing fixes carried by this component:
 * - Live bug #1: the sync_complete footer dispatches EVERY source through the
 *   config — the vanilla's sync dispatch omitted isDeezer, so Deezer rendered
 *   the YouTube re-sync button.
 * - Live bug #2: the dead duplicate `case 'download_complete'` is not ported.
 * - Live bug #5: headers and info strings use the LIVE metadata-source label
 *   (the vanilla's currentMusicSourceName is never reassigned).
 * - XSS: every cell renders as text by construction (the vanilla interpolated
 *   raw track names into innerHTML).
 *
 * Engine seams:
 * - Wing It: the dropdown is React-owned (the vanilla _toggleWingItDropdown
 *   reads script-scoped registries this page does not fill) but both actions
 *   call the engine directly with its own argument shapes: wingItDownload
 *   (tracks, name, sourceWord, null, true) after closing this modal — the
 *   vanilla removes the modal first (downloads.js 92-96) — and
 *   _wingItSyncFromModal(hash, tracks, name, isLB), which keeps it open. The
 *   engine's follow-up DOM writes (updateYouTubeModalButtons) target the
 *   VANILLA container id, which this modal deliberately does NOT use — so
 *   they no-op instead of innerHTML-clobbering React-owned children; wing-it
 *   sync progress feedback arrives when the page wiring polls it (P5).
 * - Downloads: onDownloadMissing hands off to the vanilla download engine via
 *   the parent (openDownloadMissingModalForYouTube).
 * - Fix Track Match: NOT adopted. The review proved the wishlist-tools flow
 *   reads the script-scoped listenbrainzPlaylistStates/youtubePlaylistStates
 *   registries (wishlist-tools.js 22-58) that a React page can never populate
 *   — every call would toast "Track data not found". The Fix/rematch/unmatch
 *   buttons therefore surface INTENTS (onFixTrack/onUnmatchTrack); the page
 *   wiring renders the React-native FixModal (-ui/fix-modal.tsx) for
 *   onFixTrack and runs postUnmatch + applyUnmatched (with the vanilla's
 *   'Match removed' / failure toasts) for onUnmatchTrack.
 */

import { useState } from 'react';

import type { SourceVerticalConfig } from '../-sync.sources';
import type { SourcePlaylistState } from '../-sync.state';
import type { DiscoveryRow } from '../-sync.transform';

import { discoveryRowAction } from '../-sync.core';
import {
  descriptionSourceWord,
  initialProgressText,
  metadataSourceLabel,
  modalDescription,
  modalSourceLabel,
  modalTitle,
  progressLineText,
  seededProgress,
} from '../-sync.modal-core';
import { syncPercent } from '../-sync.state';
import { OrganizeToggle } from './organize-toggle';

declare global {
  interface Window {
    wingItDownload?: (
      tracks: unknown[],
      playlistName: string,
      source?: string,
      cardIdentifier?: string | null,
      skipConfirm?: boolean,
    ) => void;
    _wingItSyncFromModal?: (
      urlHash: string,
      tracks: unknown[],
      name: string,
      isLB: boolean,
    ) => void;
    // showToast is already declared by the shell globals (globals.d.ts).
  }
}

export interface DiscoveryModalProps {
  config: SourceVerticalConfig;
  state: SourcePlaylistState;
  standalone: boolean;
  /** For mirrored states: the mirrored row's source (label capitalisation). */
  mirroredSource?: string;
  onClose: () => void;
  onStartDiscovery: () => void;
  onStartSync: () => void;
  onCancelSync: () => void;
  onDownloadMissing: (options?: { forcePlaylistFolder?: boolean }) => void;
  /** Present only for sources with a reset endpoint (beatport/youtube/mirrored). */
  onRediscover?: () => void;
  /** Mirrored only: retry the failed discoveries (#815). */
  onRetryFailed?: () => void;
  /** The Fix Track Match intents — implemented by the P4b fix modal. */
  onFixTrack?: (row: DiscoveryRow) => void;
  onUnmatchTrack?: (row: DiscoveryRow) => void;
}

/** The wing-it source word the engine expects (_wingItAction, downloads.js 80). */
export function wingItSourceWord(config: SourceVerticalConfig): string {
  const words: Partial<Record<string, string>> = {
    listenbrainz: 'ListenBrainz',
    tidal: 'Tidal',
    qobuz: 'Qobuz',
    deezer: 'Deezer',
    beatport: 'Beatport',
  };
  return words[config.id] ?? 'YouTube';
}

function playlistTracks(state: SourcePlaylistState): unknown[] {
  return Array.isArray(state.playlist?.tracks) ? (state.playlist.tracks as unknown[]) : [];
}

function playlistName(state: SourcePlaylistState): string {
  return typeof state.playlist?.name === 'string' ? state.playlist.name : 'Playlist';
}

/**
 * Pending rows for a modal opened before any results exist —
 * generateInitialTableRows (10001-10034): LB tracks carry track_name/
 * artist_name, everyone else name + artists (array joined).
 */
export function pendingSourceRows(
  config: SourceVerticalConfig,
  tracks: unknown[],
): { index: number; track: string; artist: string }[] {
  return tracks.map((raw, index) => {
    const t = raw as {
      name?: string;
      artists?: string[] | string;
      track_name?: string;
      artist_name?: string;
    };
    if (config.id === 'listenbrainz') {
      return {
        index,
        track: t.track_name || 'Unknown Track',
        artist: t.artist_name || 'Unknown Artist',
      };
    }
    return {
      index,
      track: t.name || 'Unknown Track',
      artist: t.artists
        ? Array.isArray(t.artists)
          ? t.artists.join(', ')
          : t.artists
        : 'Unknown Artist',
    };
  });
}

function RowActions({
  row,
  onFixTrack,
  onUnmatchTrack,
}: {
  row: DiscoveryRow;
  onFixTrack?: (row: DiscoveryRow) => void;
  onUnmatchTrack?: (row: DiscoveryRow) => void;
}) {
  const action = discoveryRowAction(row);
  if (action === 'none' || !onFixTrack) return <>-</>;
  if (action === 'rematch') {
    return (
      <>
        <button
          type="button"
          className="rematch-btn"
          title="Change this match"
          onClick={() => onFixTrack(row)}
        >
          ↻
        </button>
        <button
          type="button"
          className="rematch-btn"
          style={{ marginLeft: 4, color: '#ff6b6b' }}
          title="Remove this match"
          onClick={() => onUnmatchTrack?.(row)}
        >
          ✕
        </button>
      </>
    );
  }
  return (
    <button
      type="button"
      className="fix-match-btn"
      title={
        action === 'fix-wing-it'
          ? 'Search for a proper metadata match'
          : 'Manually search for this track'
      }
      onClick={() => onFixTrack(row)}
    >
      🔧 Fix
    </button>
  );
}

/**
 * The React-owned wing-it dropdown: the two engine actions the vanilla's
 * _toggleWingItDropdown offers (downloads.js 13-46). Download closes this
 * modal first (the vanilla removes it, 92-96); Sync keeps it open.
 */
function WingItButton({
  config,
  state,
  onClose,
}: {
  config: SourceVerticalConfig;
  state: SourcePlaylistState;
  onClose: () => void;
}) {
  const [open, setOpen] = useState(false);
  const tracks = playlistTracks(state);

  const run = (action: 'download' | 'sync') => {
    setOpen(false);
    if (!tracks.length) {
      window.showToast?.('No tracks available for Wing It', 'error');
      return;
    }
    if (action === 'download') {
      onClose();
      window.wingItDownload?.(tracks, playlistName(state), wingItSourceWord(config), null, true);
    } else {
      window._wingItSyncFromModal?.(
        state.fakeHash,
        tracks,
        playlistName(state),
        config.id === 'listenbrainz',
      );
    }
  };

  return (
    <span className="wing-it-wrap">
      <button
        type="button"
        className="modal-btn wing-it-btn"
        onClick={() => setOpen((current) => !current)}
      >
        ⚡ Wing It
      </button>
      {open && (
        <div className="wing-it-dropdown visible">
          <button
            type="button"
            className="wing-it-dropdown-item"
            data-action="download"
            onClick={() => run('download')}
          >
            <span className="wing-it-dropdown-icon">📥</span>
            <span className="wing-it-dropdown-label">Download</span>
            <span className="wing-it-dropdown-hint">Grab the source tracks directly</span>
          </button>
          <button
            type="button"
            className="wing-it-dropdown-item"
            data-action="sync"
            onClick={() => run('sync')}
          >
            <span className="wing-it-dropdown-icon">🔄</span>
            <span className="wing-it-dropdown-label">Sync to Server</span>
            <span className="wing-it-dropdown-hint">Match against the server library</span>
          </button>
        </div>
      )}
    </span>
  );
}

/** The footer state machine — getModalActionButtons (9588-9928), config-driven. */
function FooterActions(props: DiscoveryModalProps) {
  const {
    config,
    state,
    standalone,
    onClose,
    onStartDiscovery,
    onStartSync,
    onCancelSync,
    onDownloadMissing,
    onRediscover,
    onRetryFailed,
  } = props;
  // The vanilla's gates (9603-9605): results presence, COUNTER-only matches,
  // and the converted-playlist fallback that keeps Download available.
  const hasResults = state.rows.length > 0;
  const hasMatches = state.spotifyMatches > 0;
  const hasConverted = Boolean(state.convertedSpotifyPlaylistId);
  // retryFailedMirroredDiscovery counts every row that is NOT found (9684).
  const failedCount = state.rows.filter((r) => r.status_class !== 'found').length;

  if (state.phase === 'fresh') {
    return (
      <>
        <button type="button" className="modal-btn modal-btn-primary" onClick={onStartDiscovery}>
          🔍 Start Discovery
        </button>
        <WingItButton config={config} state={state} onClose={onClose} />
      </>
    );
  }

  if (state.phase === 'discovering') {
    return <div className="modal-info">🔍 Discovering {metadataSourceLabel()} matches...</div>;
  }

  if (state.phase === 'syncing') {
    const progress = state.lastSyncProgress;
    const percent = syncPercent(config.sync.percentFormula, progress);
    return (
      <>
        <button type="button" className="modal-btn modal-btn-danger" onClick={onCancelSync}>
          ❌ Cancel Sync
        </button>
        <div className="playlist-modal-sync-status" style={{ display: 'flex' }}>
          <span className="sync-stat total-tracks">♪ {progress?.total_tracks ?? 0}</span>
          <span className="sync-separator">/</span>
          <span className="sync-stat matched-tracks">✓ {progress?.matched_tracks ?? 0}</span>
          <span className="sync-separator">/</span>
          <span className="sync-stat failed-tracks">✗ {progress?.failed_tracks ?? 0}</span>
          <span className="sync-stat percentage">({percent}%)</span>
        </div>
      </>
    );
  }

  // discovered / sync_complete / downloading / download_complete — the action
  // group. The vanilla's sync_complete dispatch omitted Deezer (bug #1) and a
  // dead second download_complete case masked it (bug #2); the config dispatch
  // makes those unrepresentable.
  if (!hasResults) {
    return (
      <div className="modal-info">
        ⚠️ No discovery results available. Try starting discovery again.
      </div>
    );
  }

  // NOTE: the vanilla's "ℹ️ No Spotify matches found." prepend (9700-9702) is
  // unreachable dead code — the wing-it wrap defeats its startsWith guard —
  // so with no matches and no converted id the footer shows only the
  // always-present actions, exactly what the vanilla actually renders.
  return (
    <>
      {hasMatches && !standalone && (
        <button type="button" className="modal-btn modal-btn-primary" onClick={onStartSync}>
          🔄 Sync This Playlist
        </button>
      )}
      {(hasMatches || hasConverted) &&
        (standalone && config.id === 'spotify_public' ? (
          <button
            type="button"
            className="modal-btn modal-btn-primary soulsync-standalone-action"
            onClick={() => onDownloadMissing({ forcePlaylistFolder: true })}
          >
            📁 Download to Playlist Folder
          </button>
        ) : (
          <button
            type="button"
            className="modal-btn modal-btn-primary"
            onClick={() => onDownloadMissing()}
          >
            🔍 Download Missing Tracks
          </button>
        ))}
      {config.id === 'mirrored' && failedCount > 0 && onRetryFailed && (
        <button type="button" className="modal-btn modal-btn-secondary" onClick={onRetryFailed}>
          🔄 Retry Failed ({failedCount})
        </button>
      )}
      {config.api.reset && onRediscover && (
        <button type="button" className="modal-btn modal-btn-secondary" onClick={onRediscover}>
          🔄 Rediscover
        </button>
      )}
      <WingItButton config={config} state={state} onClose={onClose} />
    </>
  );
}

export function DiscoveryModal(props: DiscoveryModalProps) {
  const { config, state, mirroredSource, onClose, onFixTrack, onUnmatchTrack } = props;
  const fakeHash = state.fakeHash;

  const sourceLabel = modalSourceLabel(config.id, fakeHash, mirroredSource);
  const metadataLabel = metadataSourceLabel();
  const seeded = seededProgress(state);
  const tracks = playlistTracks(state);
  // Seed with the playlist's own count (9518); once payloads flow, their
  // authoritative spotify_total wins (the live painter, 10113) — results can
  // outnumber a rate-limited partial track fetch.
  const total = state.spotifyTotal || tracks.length;
  const showLine = state.rows.length > 0;
  const pending = state.rows.length === 0 ? pendingSourceRows(config, tracks) : [];

  return (
    <div className="modal-overlay" id={`sync-discovery-modal-${fakeHash}`}>
      <div className="youtube-discovery-modal">
        <div className="modal-header">
          <h2>{modalTitle(config.id, fakeHash)}</h2>
          <div className="modal-subtitle">
            {playlistName(state)} ({total} tracks)
          </div>
          <div className="modal-description">
            {modalDescription(
              state.phase,
              descriptionSourceWord(config.id, fakeHash),
              metadataLabel,
            )}
          </div>
          <button type="button" className="modal-close-btn" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="modal-body">
          <div className="progress-section">
            <div className="progress-label">🔍 {metadataLabel} Discovery Progress</div>
            <div className="progress-bar-container">
              <div className="progress-bar-fill" style={{ width: `${seeded.progress}%` }} />
            </div>
            <div className="progress-text">
              {showLine && total > 0
                ? progressLineText(seeded.matches, total, seeded.progress)
                : initialProgressText(state.phase)}
            </div>
          </div>

          <div className="discovery-table-container">
            <table className="discovery-table">
              <thead>
                <tr>
                  <th>{sourceLabel} Track</th>
                  <th>{sourceLabel} Artist</th>
                  <th>Status</th>
                  <th>{metadataLabel} Track</th>
                  <th>{metadataLabel} Artist</th>
                  <th>Album</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {state.rows.map((row) => (
                  <tr key={row.index} id={`sync-discovery-row-${fakeHash}-${row.index}`}>
                    <td className="yt-track">{row.yt_track}</td>
                    <td className="yt-artist">
                      {/* #863: an unknown/blank source artist falls back to the matched one. */}
                      {(row.yt_artist === 'Unknown Artist' || !row.yt_artist) &&
                      row.spotify_artist !== '-'
                        ? row.spotify_artist
                        : row.yt_artist}
                    </td>
                    <td className={`discovery-status ${row.status_class}`}>{row.status}</td>
                    <td className="spotify-track">{row.spotify_track}</td>
                    <td className="spotify-artist">{row.spotify_artist}</td>
                    <td className="spotify-album">{row.spotify_album}</td>
                    <td className="discovery-actions">
                      <RowActions
                        row={row}
                        onFixTrack={onFixTrack}
                        onUnmatchTrack={onUnmatchTrack}
                      />
                    </td>
                  </tr>
                ))}
                {pending.map((row) => (
                  <tr
                    key={`pending-${row.index}`}
                    id={`sync-discovery-row-${fakeHash}-${row.index}`}
                  >
                    <td className="yt-track">{row.track}</td>
                    <td className="yt-artist">{row.artist}</td>
                    <td className="discovery-status">🔍 Pending...</td>
                    <td className="spotify-track">-</td>
                    <td className="spotify-artist">-</td>
                    <td className="spotify-album">-</td>
                    <td className="discovery-actions">-</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="modal-footer">
          <div className="modal-footer-left">
            {/* The organize toggle is spotify_public-only, above the actions
                (discoveryModalOrganizeFooterHtml, 9552-9566). The resolve ref
                is the BARE url_hash: the vanilla builds spotify_public_<hash>
                and then normalizePlaylistOrganizeRef strips the prefix before
                the wire request (shared-helpers.js 1113-1115) — the stored
                source_playlist_id is the bare hash. */}
            {config.id === 'spotify_public' && (
              <OrganizeToggle playlistRef={state.sourceId} source="spotify_public" />
            )}
            <div className="modal-footer-actions">
              <FooterActions {...props} />
            </div>
          </div>
          <div className="modal-footer-right">
            <button type="button" className="modal-btn modal-btn-secondary" onClick={onClose}>
              🏠 Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
