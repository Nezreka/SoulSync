/**
 * a list of songs: an album, a playlist, search results. each row plays on
 * click or enter, shows which one is playing (moving bars, still when
 * paused), and has its own add-to-playlist. on one of your playlists rows
 * can be dragged into a new order (or moved with alt + up/down) and taken
 * out. a song this profile can't play stays in the list, dimmed, with why.
 */

import { useState, type ReactNode } from 'react';

import type { SongRef } from '../../-player.api';

import { clock } from '../../-engine/timeline';
import { GripIcon, PlayIcon, PlusIcon, XIcon } from '../icons';
import { PlayerLink } from '../kit';
import { usePlayer } from '../playback/use-player';
import { RetryImg } from '../retry-img';
import { openAddToPlaylist } from './add-to-playlist';
import s from './music.module.css';

export interface TrackRow {
  key: string;
  title: string;
  /** the library track it plays (matches the player's item id); null: can't play */
  trackId: number | null;
  /** the number in front: a track number, a position */
  number?: number | string;
  artist?: string;
  artistHref?: string;
  album?: string;
  albumHref?: string;
  art?: string | null;
  /** seconds */
  duration?: number | null;
  explicit?: boolean;
  /** a small note on the right (a quality) */
  end?: ReactNode;
  /** why it can't play */
  why?: string;
  /** the song as playlists take it; null: can't be added */
  song: SongRef | null;
}

export function EqBars({ paused }: { paused?: boolean }) {
  return (
    <span className={s.eq} data-paused={paused ? 'true' : undefined} aria-hidden="true">
      <span />
      <span />
      <span />
    </span>
  );
}

export function TrackList({
  rows,
  onPlay,
  showArt,
  showAlbum,
  label = 'Songs',
  onRemove,
  onMove,
}: {
  rows: TrackRow[];
  onPlay: (index: number) => void;
  showArt?: boolean;
  showAlbum?: boolean;
  label?: string;
  /** one of your playlists: take a row out */
  onRemove?: (index: number) => void;
  /** one of your playlists: move a row from one place to another */
  onMove?: (from: number, to: number) => void;
}) {
  const player = usePlayer();
  const playingId = player.item?.type === 'track' ? Number(player.item.id) : null;
  const paused = player.status === 'paused';
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);

  return (
    <div
      className={s.tracks}
      data-art={showArt ? 'true' : undefined}
      data-album={showAlbum ? 'true' : undefined}
      data-end={rows.some((r) => r.end) ? 'true' : undefined}
      role="list"
      aria-label={label}
    >
      {rows.map((row, index) => {
        const playable = row.trackId !== null;
        const playing = playable && row.trackId === playingId;
        const play = () => playable && onPlay(index);
        return (
          <div
            key={row.key}
            role="listitem"
            className={s.track}
            data-playing={playing ? 'true' : undefined}
            data-off={playable ? undefined : 'true'}
            data-drag={drag === index ? 'true' : undefined}
            data-over={
              over === index && drag !== null && drag !== index
                ? drag < index
                  ? 'after'
                  : 'before'
                : undefined
            }
            title={playable ? undefined : (row.why ?? 'Not in your library')}
            draggable={!!onMove}
            onDragStart={(e) => {
              setDrag(index);
              e.dataTransfer.effectAllowed = 'move';
            }}
            onDragOver={(e) => {
              if (drag === null) return;
              e.preventDefault();
              setOver(index);
            }}
            onDragEnd={() => {
              setDrag(null);
              setOver(null);
            }}
            onDrop={(e) => {
              e.preventDefault();
              if (drag !== null && drag !== index) onMove?.(drag, index);
              setDrag(null);
              setOver(null);
            }}
          >
            <div
              className={s.trackHit}
              role="button"
              tabIndex={playable ? 0 : -1}
              aria-label={
                playable ? `Play ${row.title}` : `${row.title}, ${row.why ?? 'not in your library'}`
              }
              aria-disabled={!playable}
              onClick={play}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  play();
                } else if (onMove && e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
                  e.preventDefault();
                  e.stopPropagation();
                  const to = index + (e.key === 'ArrowUp' ? -1 : 1);
                  if (to >= 0 && to < rows.length) onMove(index, to);
                }
              }}
              data-nav={playable ? true : undefined}
            >
              <span className={s.trackNum}>
                {playing ? (
                  <EqBars paused={paused} />
                ) : (
                  <>
                    <span className={s.trackNumText}>{row.number ?? index + 1}</span>
                    {playable ? (
                      <span className={s.trackNumPlay}>
                        <PlayIcon />
                      </span>
                    ) : null}
                  </>
                )}
              </span>
              {showArt ? (
                <span className={s.trackArt}>
                  {row.art ? (
                    <RetryImg src={row.art} alt="" loading="lazy" decoding="async" />
                  ) : null}
                </span>
              ) : null}
              <span className={s.trackMain}>
                <span className={s.trackTitle}>
                  {row.title}
                  {row.explicit ? (
                    <span className={s.explicit} title="Explicit" aria-label="Explicit">
                      E
                    </span>
                  ) : null}
                </span>
                {row.artist ? (
                  <span className={s.trackSub}>
                    {row.artistHref ? (
                      <PlayerLink
                        href={row.artistHref}
                        onClick={(e) => e.stopPropagation()}
                        data-nav
                      >
                        {row.artist}
                      </PlayerLink>
                    ) : (
                      row.artist
                    )}
                    {!playable ? (
                      <span className={s.trackWhy}> · {row.why ?? 'Not in your library'}</span>
                    ) : null}
                  </span>
                ) : null}
              </span>
              {showAlbum ? (
                <span className={s.trackAlbum}>
                  {row.album && row.albumHref ? (
                    <PlayerLink href={row.albumHref} onClick={(e) => e.stopPropagation()} data-nav>
                      {row.album}
                    </PlayerLink>
                  ) : (
                    row.album
                  )}
                </span>
              ) : null}
            </div>
            <span className={s.trackActions}>
              {row.song ? (
                <button
                  type="button"
                  className={s.trackBtn}
                  aria-label={`Add ${row.title} to a playlist`}
                  title="Add to playlist"
                  onClick={() => openAddToPlaylist([row.song!])}
                  data-nav
                >
                  <PlusIcon />
                </button>
              ) : null}
              {onRemove ? (
                <button
                  type="button"
                  className={s.trackBtn}
                  aria-label={`Take ${row.title} out of this playlist`}
                  title="Take out of this playlist"
                  onClick={() => onRemove(index)}
                  data-nav
                >
                  <XIcon />
                </button>
              ) : null}
              {onMove ? (
                <span
                  className={s.trackGrip}
                  title="Drag to move (or alt + up/down)"
                  aria-hidden="true"
                >
                  <GripIcon />
                </span>
              ) : null}
            </span>
            <span className={s.trackEnd}>
              {row.end}
              <span>{row.duration ? clock(row.duration) : ''}</span>
            </span>
          </div>
        );
      })}
    </div>
  );
}
