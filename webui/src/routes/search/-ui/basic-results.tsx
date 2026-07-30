import { useEffect, useState } from 'react';

import type { BasicAlbum, BasicResult, BasicTrack } from '../-basic.types';

import {
  albumFormatLabel,
  detectDiscBreaks,
  formatBitrate,
  formatSize,
  resultTitle,
} from '../-basic.helpers';
import { isAlbum } from '../-basic.types';

export interface BasicResultActions {
  onDownloadTrack: (track: BasicTrack, index: number) => void;
  onStreamTrack: (track: BasicTrack, index: number) => void;
  onMatchedTrack: (track: BasicTrack, index: number) => void;
  onDownloadAlbum: (album: BasicAlbum, index: number) => void;
  onMatchedAlbum: (album: BasicAlbum, index: number) => void;
  onDownloadAlbumTrack: (album: BasicAlbum, albumIndex: number, trackIndex: number) => void;
  onStreamAlbumTrack: (album: BasicAlbum, albumIndex: number, trackIndex: number) => void;
  onMatchedAlbumTrack: (album: BasicAlbum, albumIndex: number, trackIndex: number) => void;
}

/**
 * The results list.
 *
 * Indices are positions in THIS list — the one on screen, after filtering and
 * sorting — because that is what `window.currentSearchResults` publishes and
 * what the vanilla matched-download modal indexes into.
 */
export function BasicResults({
  results,
  actions,
  placeholder,
}: {
  results: BasicResult[];
  actions: BasicResultActions;
  /**
   * The empty-state line. Two different sentences in the vanilla, and the
   * difference matters: before any search the static markup reads "Enter a
   * search term to get started." (index.html), while a search that found
   * nothing renders "No search results found." (displayDownloadsResults).
   * Showing the second one on a fresh page accuses the user of a failed search
   * they never ran.
   */
  placeholder: string;
}) {
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());

  // Collapse everything when the list changes underneath. Index 2 in a new
  // result set is a different album, so keeping it open would expand a folder
  // the user never clicked.
  useEffect(() => {
    setExpanded(new Set());
  }, [results]);

  if (!results.length) {
    return (
      <div className="bs-results-wrap" id="search-results-area">
        <div className="search-results-placeholder">
          <p>{placeholder}</p>
        </div>
      </div>
    );
  }

  const toggle = (index: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  return (
    <div className="bs-results-wrap" id="search-results-area">
      {results.map((result, index) =>
        isAlbum(result) ? (
          <AlbumCard
            key={`${result.username}:${result.album_path}:${index}`}
            album={result}
            index={index}
            expanded={expanded.has(index)}
            onToggle={() => toggle(index)}
            actions={actions}
          />
        ) : (
          <TrackCard
            key={`${result.username}:${result.filename}:${index}`}
            track={result}
            index={index}
            actions={actions}
          />
        ),
      )}
    </div>
  );
}

/** `Shared by <uploader>` — the button chat.js's delegated handler listens for. */
function Uploader({ username }: { username: string }) {
  return (
    <>
      Shared by{' '}
      <button
        type="button"
        className="chat-user-link"
        data-chat-msg-user={username}
        title="Message this user on Soulseek"
      >
        {username || 'Unknown'}
      </button>
    </>
  );
}

function AlbumCard({
  album,
  index,
  expanded,
  onToggle,
  actions,
}: {
  album: BasicAlbum;
  index: number;
  expanded: boolean;
  onToggle: () => void;
  actions: BasicResultActions;
}) {
  const tracks = album.tracks ?? [];
  const discBreaks = detectDiscBreaks(tracks);
  const multiDisc = discBreaks.size > 0;
  let discNumber = 1;

  return (
    <div className={`album-result-card${expanded ? ' expanded' : ''}`} data-album-index={index}>
      {/*
        The whole header is the expand target, as in the vanilla. It is a div
        rather than a button because it CONTAINS buttons, and a button inside a
        button is invalid markup that browsers resolve by dropping one of them.
      */}
      <div
        className="album-card-header"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onToggle();
          }
        }}
      >
        <div className="album-expand-indicator">{expanded ? '▼' : '▶'}</div>
        <div className="album-icon">💿</div>
        <div className="album-info">
          <div className="album-title">{album.album_title || 'Unknown Album'}</div>
          <div className="album-artist">by {album.artist || 'Unknown Artist'}</div>
          <div className="album-details">
            {tracks.length} tracks • {formatSize(album.total_size)} • {albumFormatLabel(album)}
          </div>
          <div className="album-uploader">
            <Uploader username={album.username || ''} />
          </div>
        </div>
        {/* stopPropagation so a download does not also toggle the folder. */}
        <div className="album-actions" onClick={(event) => event.stopPropagation()}>
          <button
            type="button"
            className="album-download-btn"
            onClick={() => actions.onDownloadAlbum(album, index)}
          >
            ⬇ Download Album
          </button>
          <button
            type="button"
            className="album-matched-btn"
            title="Matched Album Download"
            onClick={() => actions.onMatchedAlbum(album, index)}
          >
            Matched Album🎯
          </button>
        </div>
      </div>

      <div className="album-track-list" style={{ display: expanded ? 'block' : 'none' }}>
        {multiDisc ? <DiscSeparator label="Disc 1" first /> : null}
        {tracks.map((track, trackIndex) => {
          const separator = discBreaks.has(trackIndex) ? ++discNumber : null;
          return (
            <div key={`${track.filename}:${trackIndex}`}>
              {separator ? <DiscSeparator label={`Disc ${separator}`} /> : null}
              <AlbumTrackRow
                album={album}
                track={track}
                albumIndex={index}
                trackIndex={trackIndex}
                actions={actions}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * The inline styles are the vanilla's, kept verbatim.
 *
 * `.disc-separator` has no stylesheet rule — the appearance lives entirely in
 * these attributes, so dropping them for a class would leave the separators
 * looking like unstyled text.
 */
function DiscSeparator({ label, first = false }: { label: string; first?: boolean }) {
  return (
    <div
      className="disc-separator"
      style={{
        padding: '6px 12px',
        fontWeight: 600,
        fontSize: '0.85em',
        color: 'var(--text-secondary, #aaa)',
        borderBottom: '1px solid var(--border-color, #333)',
        margin: first ? '0 0 4px 0' : '8px 0 4px 0',
      }}
    >
      {label}
    </div>
  );
}

function AlbumTrackRow({
  album,
  track,
  albumIndex,
  trackIndex,
  actions,
}: {
  album: BasicAlbum;
  track: BasicTrack;
  albumIndex: number;
  trackIndex: number;
  actions: BasicResultActions;
}) {
  const bitrate = formatBitrate(track.bitrate);
  return (
    <div className="track-item">
      <div className="track-item-info">
        <div className="track-item-title">{track.title || `Track ${trackIndex + 1}`}</div>
        <div className="track-item-details">
          {track.track_number ? `${track.track_number}. ` : ''}
          {track.artist || album.artist || 'Unknown Artist'} • {formatSize(track.size)} •{' '}
          {track.quality || 'Unknown'} {bitrate}
        </div>
      </div>
      <div className="track-item-actions">
        <button
          type="button"
          className="track-stream-btn"
          onClick={() => actions.onStreamAlbumTrack(album, albumIndex, trackIndex)}
        >
          Stream ▶
        </button>
        <button
          type="button"
          className="track-download-btn"
          onClick={() => actions.onDownloadAlbumTrack(album, albumIndex, trackIndex)}
        >
          Download ⬇
        </button>
        <button
          type="button"
          className="track-matched-btn"
          title="Matched Download"
          onClick={() => actions.onMatchedAlbumTrack(album, albumIndex, trackIndex)}
        >
          Matched Download 🎯
        </button>
      </div>
    </div>
  );
}

function TrackCard({
  track,
  index,
  actions,
}: {
  track: BasicTrack;
  index: number;
  actions: BasicResultActions;
}) {
  const bitrate = formatBitrate(track.bitrate);
  return (
    <div className="track-result-card">
      <div className="track-icon">🎵</div>
      <div className="track-info">
        <div className="track-title">{resultTitle(track) || 'Unknown Title'}</div>
        <div className="track-artist">by {track.artist || 'Unknown Artist'}</div>
        <div className="track-details">
          {formatSize(track.size)} • {track.quality || 'Unknown'} {bitrate}
        </div>
        <div className="track-uploader">
          <Uploader username={track.username || ''} />
        </div>
      </div>
      <div className="track-actions">
        <button
          type="button"
          className="track-stream-btn"
          title="Stream Track"
          onClick={() => actions.onStreamTrack(track, index)}
        >
          Stream ▶
        </button>
        <button
          type="button"
          className="track-download-btn"
          title="Download"
          onClick={() => actions.onDownloadTrack(track, index)}
        >
          Download ⬇
        </button>
        <button
          type="button"
          className="track-matched-btn"
          title="Matched Download"
          onClick={() => actions.onMatchedTrack(track, index)}
        >
          Matched Download🎯
        </button>
      </div>
    </div>
  );
}
