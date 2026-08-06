/**
 * The mirrored playlist's TRACKS detail modal — openMirroredPlaylistModal
 * (stats-automations.js 1066-1165) ported React-native.
 *
 * WHY PORTED AND NOT ADOPTED (the pools were adopted; this is not the same):
 * the vanilla modal's primary button, Discover, calls discoverMirroredPlaylist
 * → openYouTubeDiscoveryModal, which lives in sync-services.js and the flip
 * deletes. Adoption's whole premise — "it keeps working untouched" — is false
 * for its main action. Three of its remaining four buttons (Edit Source,
 * Auto-Sync, Delete) already have React implementations from P5g/P5e, so
 * adopting would also run the same action two ways depending on where it was
 * clicked, including two pipeline pollers unaware of each other.
 *
 * The vanilla function STAYS ALIVE in stats-automations.js: auto-sync.js calls
 * it three times (the board's Details button at 1180 and the rename /
 * source-ref reopen tails at 2403/2437) and shared-helpers.js once at 1755
 * (already typeof-guarded). Those die with the auto-sync board phase, and the
 * vanilla copy dies with them. stats-automations.js survives the flip anyway —
 * the two pool modals live there.
 *
 * Markup is transcribed class for class and label for label from 1120-1157;
 * every .mm-* class already exists in style.css (verified), so there is no new
 * styling here. The actions are props because the call sites differ.
 */

import type { MirroredPlaylistDetail } from '../-sync.api';
import type { MirroredTrack } from '../-sync.mirrored';

import {
  mirroredDetailSourceIcon,
  mirroredDetailSourceLabel,
  mirroredHeroArt,
  mirroredRowDuration,
  mirroredTotalRuntime,
  timeAgo,
} from '../-sync.mirrored';

export interface MirroredDetailModalProps {
  playlistId: number;
  data: MirroredPlaylistDetail;
  /** Read once per render, as the vanilla reads the clock once per open. */
  now: number;
  onClose: () => void;
  /** 1148 — the vanilla closes the modal BEFORE it asks to delete. */
  onDelete: () => void;
  /** 1150 — Edit Source. */
  onEditSource: () => void;
  /** 1151 — Auto-Sync (runMirroredPlaylistPipeline). */
  onRunPipeline: () => void;
  /** 1153 — Discover (discoverMirroredPlaylist). */
  onDiscover: () => void;
}

function TrackRow({ track }: { track: MirroredTrack }) {
  return (
    <div className="mm-row">
      <span className="mm-row-pos">{track.position}</span>
      {track.image_url ? (
        <img
          className="mm-row-art"
          src={track.image_url}
          loading="lazy"
          // 1102: on a broken image the vanilla swaps the <img> for the empty
          // tile in place. Its `this.onerror=null` guard has no React
          // equivalent and needs none — dropping the src is what stops a
          // second error from firing.
          onError={(event) => {
            const img = event.currentTarget;
            img.classList.add('mm-art-empty');
            img.removeAttribute('src');
          }}
        />
      ) : (
        <span className="mm-row-art mm-art-empty" />
      )}
      <div className="mm-row-main">
        <span className="mm-row-title">{track.track_name}</span>
        <span className="mm-row-artist">{track.artist_name}</span>
      </div>
      <span className="mm-row-album">{track.album_name || ''}</span>
      <span className="mm-row-dur">{mirroredRowDuration(track.duration_ms)}</span>
    </div>
  );
}

export function MirroredDetailModal({
  data,
  now,
  onClose,
  onDelete,
  onEditSource,
  onRunPipeline,
  onDiscover,
}: MirroredDetailModalProps) {
  const tracks = (data.tracks ?? []) as MirroredTrack[];
  const source = data.source || 'unknown';
  const sourceIcon = mirroredDetailSourceIcon(source);
  const srcLabel = mirroredDetailSourceLabel(source);
  const heroArt = mirroredHeroArt(data.image_url, tracks);
  const { totalMs, label: totalLabel } = mirroredTotalRuntime(tracks);

  return (
    <div
      id="mirrored-track-modal"
      className="mirrored-modal-overlay"
      // 1159 — backdrop only, never a click inside the panel.
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="mirrored-modal">
        <div className="mm-hero">
          {heroArt ? (
            <div className="mm-hero-bg" style={{ backgroundImage: `url('${heroArt}')` }} />
          ) : null}
          <div className="mm-hero-content">
            {heroArt ? (
              <div className="mm-cover" style={{ backgroundImage: `url('${heroArt}')` }} />
            ) : (
              <div className={`mm-cover mm-cover-empty ${source}`}>{sourceIcon}</div>
            )}
            <div className="mm-hero-info">
              <span className="mm-eyebrow">Mirrored Playlist</span>
              <h2 className="mm-title">{data.name}</h2>
              <div className="mm-meta">
                <span className={`mm-source-pill ${source}`}>{srcLabel}</span>
                {data.owner ? (
                  <>
                    <span className="mm-meta-item">{data.owner}</span>
                    <span className="mm-dot">&middot;</span>
                  </>
                ) : null}
                <span className="mm-meta-item">{tracks.length} tracks</span>
                {totalMs ? (
                  <>
                    <span className="mm-dot">&middot;</span>
                    <span className="mm-meta-item">{totalLabel}</span>
                  </>
                ) : null}
                <span className="mm-dot">&middot;</span>
                <span className="mm-meta-item">
                  Mirrored {timeAgo(data.updated_at || data.mirrored_at, now)}
                </span>
              </div>
            </div>
          </div>
          <button type="button" className="mm-close" onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>
        <div className="mm-list">
          <div className="mm-list-head">
            <span>#</span>
            <span />
            <span>Title</span>
            <span>Album</span>
            <span className="mm-col-dur">Time</span>
          </div>
          {tracks.length > 0 ? (
            tracks.map((track, index) => (
              <TrackRow
                key={`${track.position ?? index}-${track.track_name ?? ''}`}
                track={track}
              />
            ))
          ) : (
            <div className="mm-empty">No tracks in this mirror yet.</div>
          )}
        </div>
        <div className="mm-actions">
          <button
            type="button"
            className="mm-btn mm-btn-danger"
            onClick={() => {
              // 1148 closes first, then asks — so the confirm is not behind
              // the overlay.
              onClose();
              onDelete();
            }}
          >
            Delete Mirror
          </button>
          <div className="mm-actions-right">
            <button type="button" className="mm-btn mm-btn-ghost" onClick={onEditSource}>
              Edit Source
            </button>
            <button type="button" className="mm-btn mm-btn-secondary" onClick={onRunPipeline}>
              Auto-Sync
            </button>
            <button type="button" className="mm-btn mm-btn-ghost" onClick={onClose}>
              Close
            </button>
            <button type="button" className="mm-btn mm-btn-primary" onClick={onDiscover}>
              Discover
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
