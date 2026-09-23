import { useEffect, useState } from 'react';

import type { BasicAlbum, BasicResult, BasicTrack, DownloadTarget } from '../-basic.types';

import {
  artColors,
  detectDiscBreaks,
  formatDuration,
  formatSize,
  isLossless,
  qualityLabel,
  resultTitle,
} from '../-basic.helpers';
import { isAlbum } from '../-basic.types';
import styles from './basic.module.css';

/**
 * The results list. one row per result, one Download button per row. HOW the
 * file comes in is picked afterwards in the chooser, so the row stays quiet.
 *
 * #search-results-area is load-bearing, the page tour points at it.
 */
export function BasicResults({
  results,
  placeholder,
  onDownload,
}: {
  results: BasicResult[];
  /**
   * the empty-state line. before any search it's a hint, after a search that
   * found nothing it says so. showing "no results" on a fresh page accuses the
   * user of a search they never ran.
   */
  placeholder: string;
  onDownload: (target: DownloadTarget) => void;
}) {
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());

  // collapse everything when the list changes underneath. index 2 in a new
  // result set is a different album, keeping it open would expand a folder
  // the user never clicked
  useEffect(() => {
    setExpanded(new Set());
  }, [results]);

  if (!results.length) {
    return (
      <div className={styles.list} id="search-results-area">
        <div className={`${styles.empty} bs-status-bar`}>
          <p id="search-status-text" role="status">
            {placeholder}
          </p>
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
    <div className={styles.list} id="search-results-area">
      {results.map((result, index) =>
        isAlbum(result) ? (
          <AlbumItem
            key={`${result.username}:${result.album_path}:${index}`}
            album={result}
            expanded={expanded.has(index)}
            onToggle={() => toggle(index)}
            onDownload={onDownload}
          />
        ) : (
          <div key={`${result.username}:${result.filename}:${index}`} className={styles.item}>
            <div className={styles.row} data-result-kind="track">
              <Art seed={`${result.artist ?? ''}${result.album ?? result.title ?? ''}`} />
              <div className={styles.meta}>
                <div className={styles.title}>
                  <span className={styles.titleText}>{resultTitle(result) || 'Unknown title'}</span>
                </div>
                <div className={styles.sub}>
                  {result.artist || 'Unknown artist'}
                  {result.album ? (
                    <>
                      <span className={styles.sep}>·</span>
                      {result.album}
                    </>
                  ) : null}
                </div>
              </div>
              <Facts result={result} />
              <DownloadButton
                label={`Download ${resultTitle(result) || 'track'}`}
                onClick={() => onDownload({ kind: 'track', track: result })}
              />
            </div>
          </div>
        ),
      )}
    </div>
  );
}

function AlbumItem({
  album,
  expanded,
  onToggle,
  onDownload,
}: {
  album: BasicAlbum;
  expanded: boolean;
  onToggle: () => void;
  onDownload: (target: DownloadTarget) => void;
}) {
  const tracks = album.tracks ?? [];
  const count = tracks.length || album.track_count || 0;
  return (
    <div className={styles.item}>
      <div className={styles.row} data-result-kind="album">
        <Art seed={`${album.artist ?? ''}${album.album_title ?? ''}`} album />
        <div className={styles.meta}>
          <div className={styles.title}>
            <button
              type="button"
              className={styles.expand}
              aria-expanded={expanded}
              aria-label={`${expanded ? 'Hide' : 'Show'} tracks of ${album.album_title || 'album'}`}
              onClick={onToggle}
            >
              <svg className={styles.chevron} viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="M6 3.5 10.5 8 6 12.5"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                />
              </svg>
              <span className={styles.titleText}>{album.album_title || 'Unknown album'}</span>
            </button>
          </div>
          <div className={styles.sub}>
            {album.artist || 'Unknown artist'}
            <span className={styles.sep}>·</span>
            {count} {count === 1 ? 'track' : 'tracks'}
            {album.year ? (
              <>
                <span className={styles.sep}>·</span>
                {album.year}
              </>
            ) : null}
          </div>
        </div>
        <Facts result={album} />
        <DownloadButton
          label={`Download ${album.album_title || 'album'}`}
          onClick={() => onDownload({ kind: 'album', album })}
        />
      </div>
      {expanded ? <AlbumTracks album={album} onDownload={onDownload} /> : null}
    </div>
  );
}

function AlbumTracks({
  album,
  onDownload,
}: {
  album: BasicAlbum;
  onDownload: (target: DownloadTarget) => void;
}) {
  const tracks = album.tracks ?? [];
  const breaks = detectDiscBreaks(tracks);
  let disc = 1;
  return (
    <div className={styles.tracks}>
      {breaks.size ? <div className={styles.disc}>Disc 1</div> : null}
      {tracks.map((track, trackIndex) => {
        const newDisc = breaks.has(trackIndex) ? ++disc : null;
        const artist = track.artist && track.artist !== album.artist ? track.artist : '';
        return (
          <TrackLine
            key={`${track.filename}:${trackIndex}`}
            disc={newDisc}
            track={track}
            trackIndex={trackIndex}
            artist={artist}
            onDownload={() => onDownload({ kind: 'albumTrack', album, trackIndex })}
          />
        );
      })}
    </div>
  );
}

function TrackLine({
  disc,
  track,
  trackIndex,
  artist,
  onDownload,
}: {
  disc: number | null;
  track: BasicTrack;
  trackIndex: number;
  artist: string;
  onDownload: () => void;
}) {
  const number = track.track_number || trackIndex + 1;
  return (
    <>
      {disc ? <div className={styles.disc}>Disc {disc}</div> : null}
      <div className={styles.track}>
        <span className={styles.trackNumber}>{String(number).padStart(2, '0')}</span>
        <span className={styles.trackTitle}>
          {track.title || `Track ${trackIndex + 1}`}
          {artist ? <span className={styles.trackArtist}> · {artist}</span> : null}
        </span>
        <span className={`${styles.dim} ${styles.trackMeta}`}>
          {formatDuration(track.duration)}
        </span>
        <span className={`${styles.dim} ${styles.trackMeta}`}>{formatSize(track.size)}</span>
        <button
          type="button"
          className={styles.trackDownload}
          aria-label={`Download ${track.title || `track ${trackIndex + 1}`}`}
          onClick={onDownload}
        >
          Download
        </button>
      </div>
    </>
  );
}

/** quality badge, size, length (or album), uploader */
function Facts({ result }: { result: BasicResult }) {
  const label = qualityLabel(result);
  const album = isAlbum(result);
  const duration = album ? '' : formatDuration(result.duration);
  return (
    <div className={styles.facts}>
      {label ? (
        <span className={`${styles.badge}${isLossless(result) ? ` ${styles.badgeLossless}` : ''}`}>
          {label}
        </span>
      ) : null}
      <span>{formatSize(album ? result.total_size : result.size)}</span>
      {duration ? <span className={styles.dim}>{duration}</span> : null}
      <Uploader username={result.username || ''} />
    </div>
  );
}

/**
 * The uploader. chat.js listens for clicks on .chat-user-link with
 * data-chat-msg-user and opens a message to them, so both stay.
 */
function Uploader({ username }: { username: string }) {
  if (!username) return null;
  return (
    <button
      type="button"
      className={`${styles.uploader} chat-user-link`}
      data-chat-msg-user={username}
      title={`Shared by ${username}. Message them on Soulseek`}
    >
      {username}
    </button>
  );
}

function DownloadButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" className={styles.download} aria-label={label} onClick={onClick}>
      <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path
          d="M8 2.5v8M4.5 7 8 10.5 11.5 7M3 13.5h10"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <span className={styles.downloadLabel}>Download</span>
    </button>
  );
}

/** a stable colour field per release, raw results carry no cover */
function Art({ seed, album = false }: { seed: string; album?: boolean }) {
  const [light, dark] = artColors(seed || '?');
  return (
    <div
      className={`${styles.art}${album ? ` ${styles.artAlbum}` : ''}`}
      aria-hidden="true"
      style={{
        background: `radial-gradient(120% 90% at 20% 15%, ${light}, transparent 60%), linear-gradient(145deg, ${dark}, #111218)`,
      }}
    />
  );
}
