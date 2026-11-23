/**
 * a list of songs: an album, a playlist, search results. each row plays on
 * click or enter, shows which one is playing (moving bars, still when
 * paused), and has Heart (Liked Songs), Add to Queue (when actively playing),
 * and Add to Playlist actions.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState, type ReactNode } from 'react';

import type { PlayItem } from '../../-engine/types';
import type { SongRef } from '../../-player.api';

import { clock } from '../../-engine/timeline';
import { playlistEdits, playlistQuery, playlistsQuery } from '../../-player.api';
import { DownloadIcon, GripIcon, HeartIcon, PlayIcon, PlusIcon, XIcon } from '../icons';
import { PlayerLink } from '../kit';
import { getEngine, usePlayer } from '../playback/use-player';
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
  /** when not in the library, clicking the row requests it */
  onRequest?: () => void;
  /** optional inline action button for missing tracks */
  playable?: boolean;
  onMissingAction?: () => void;
  missingActionLabel?: string;
  missingActionDone?: boolean;
  missingActionBusy?: boolean;
  /** the song as playlists take it; null: can't be added */
  song?: SongRef | null;
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

function QueueAddIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h13" />
      <path d="M3 12h9" />
      <path d="M3 18h9" />
      <path d="M18 14v6" />
      <path d="M15 17h6" />
    </svg>
  );
}

function songKey(artist: string | undefined, title: string): string {
  return `${(artist ?? '').trim().toLowerCase()}|${title.trim().toLowerCase()}`;
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
  const queryClient = useQueryClient();
  const playingId = player.item?.type === 'track' ? Number(player.item.id) : null;
  const hasActivePlayback = !!player.item;
  const paused = player.status === 'paused';
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const [optimisticLiked, setOptimisticLiked] = useState<Record<string, boolean>>({});
  const [queuedKeys, setQueuedKeys] = useState<Record<string, boolean>>({});

  // Single lightweight lookup of the profile's Liked Songs playlist for O(1) per-row Heart state
  const lists = useQuery({ ...playlistsQuery(), retry: false });
  const likedPlaylist = lists.data?.yours.find((p) => p.liked) ?? null;
  const likedDetail = useQuery({
    ...playlistQuery(likedPlaylist?.id ?? 0),
    enabled: !!likedPlaylist?.id,
    retry: false,
  });

  const likedSets = useMemo(() => {
    const byId = new Set<number>();
    const byName = new Set<string>();
    for (const song of likedDetail.data?.songs ?? []) {
      if (song.track_id != null) byId.add(Number(song.track_id));
      byName.add(songKey(song.artist, song.title));
    }
    return { byId, byName };
  }, [likedDetail.data]);

  const isRowLiked = (row: TrackRow): boolean => {
    if (row.key in optimisticLiked) return optimisticLiked[row.key];
    if (row.trackId !== null && likedSets.byId.has(row.trackId)) return true;
    const artist = row.song?.artist ?? row.artist;
    return likedSets.byName.has(songKey(artist, row.title));
  };

  const toggleLike = async (row: TrackRow) => {
    if (!row.song) return;
    const next = !isRowLiked(row);
    setOptimisticLiked((prev) => ({ ...prev, [row.key]: next }));
    try {
      if (next) await playlistEdits.like([row.song]);
      else await playlistEdits.unlike([row.song]);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['player', 'playlists'] }),
        queryClient.invalidateQueries({ queryKey: ['player', 'playlist-membership'] }),
        likedPlaylist
          ? queryClient.invalidateQueries({ queryKey: ['player', 'playlist', likedPlaylist.id] })
          : null,
      ]);
    } catch {
      setOptimisticLiked((prev) => ({ ...prev, [row.key]: !next }));
    }
  };

  const addToQueue = (row: TrackRow) => {
    if (row.trackId === null) return;
    const item: PlayItem = {
      type: 'track',
      id: String(row.trackId),
      title: row.title,
      subtitle: row.song?.artist ?? row.artist ?? '',
      artwork: row.art ?? null,
      href: row.albumHref,
      duration: row.duration ?? null,
      parentTitle: row.song?.album ?? row.album,
    };
    getEngine().append([item]);
    setQueuedKeys((prev) => ({ ...prev, [row.key]: true }));
    window.setTimeout(() => {
      setQueuedKeys((prev) => ({ ...prev, [row.key]: false }));
    }, 1600);
  };

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
        const playable = row.playable !== undefined ? row.playable : row.trackId !== null;
        const requestable = !playable && !!(row.onRequest || row.onMissingAction);
        const interactive = playable || requestable;
        const playing = playable && row.trackId !== null && row.trackId === playingId;
        const liked = !!row.song && isRowLiked(row);
        const justQueued = !!queuedKeys[row.key];
        const activate = () => {
          if (playable) onPlay(index);
          else if (row.onMissingAction) row.onMissingAction();
          else if (row.onRequest) row.onRequest();
        };
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
            title={
              playable
                ? undefined
                : requestable
                  ? `Click to request "${row.title}"`
                  : (row.why ?? 'Not in your library')
            }
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
              tabIndex={interactive ? 0 : -1}
              aria-label={
                playable
                  ? `Play ${row.title}`
                  : requestable
                    ? `Request ${row.title}`
                    : `${row.title}, ${row.why ?? 'not in your library'}`
              }
              aria-disabled={!interactive}
              onClick={activate}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  activate();
                } else if (onMove && e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
                  e.preventDefault();
                  e.stopPropagation();
                  const to = index + (e.key === 'ArrowUp' ? -1 : 1);
                  if (to >= 0 && to < rows.length) onMove(index, to);
                }
              }}
              data-nav={interactive ? true : undefined}
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
                    ) : requestable ? (
                      <span className={s.trackNumPlay}>
                        <DownloadIcon />
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
            <span
              className={s.trackActions}
              style={liked || justQueued || (!playable && row.onMissingAction) ? { opacity: 1 } : undefined}
            >
              {row.song ? (
                <button
                  type="button"
                  className={s.trackBtn}
                  aria-pressed={liked}
                  aria-label={liked ? `Remove ${row.title} from Liked Songs` : `Like ${row.title}`}
                  title={liked ? 'In Liked Songs' : 'Add to Liked Songs'}
                  style={liked ? { color: 'var(--accent-light, #f43f5e)' } : undefined}
                  onClick={(e) => {
                    e.stopPropagation();
                    void toggleLike(row);
                  }}
                  data-nav
                >
                  <HeartIcon filled={liked} />
                </button>
              ) : null}
              {playable && hasActivePlayback && row.trackId !== null ? (
                <button
                  type="button"
                  className={s.trackBtn}
                  aria-label={`Add ${row.title} to queue`}
                  title={justQueued ? 'Added to queue' : 'Add to queue'}
                  style={justQueued ? { color: 'var(--accent-light, #10b981)' } : undefined}
                  onClick={(e) => {
                    e.stopPropagation();
                    addToQueue(row);
                  }}
                  data-nav
                >
                  {justQueued ? '✓' : <QueueAddIcon />}
                </button>
              ) : null}
              {row.song ? (
                <button
                  type="button"
                  className={s.trackBtn}
                  aria-label={`Add ${row.title} to a playlist`}
                  title="Add to playlist"
                  onClick={(e) => {
                    e.stopPropagation();
                    openAddToPlaylist([row.song!]);
                  }}
                  data-nav
                >
                  <PlusIcon />
                </button>
              ) : null}
              {!playable && row.onMissingAction ? (
                <button
                  type="button"
                  className={s.trackBtn}
                  style={{
                    width: 'auto',
                    padding: '0 10px',
                    height: 26,
                    fontSize: '0.76rem',
                    fontWeight: 600,
                    borderRadius: 999,
                    border: '1px solid rgba(255,255,255,0.16)',
                    background: row.missingActionDone ? 'rgba(255,255,255,0.06)' : 'rgba(var(--accent-rgb), 0.22)',
                    color: '#fff',
                  }}
                  disabled={row.missingActionBusy || row.missingActionDone}
                  onClick={(e) => {
                    e.stopPropagation();
                    row.onMissingAction?.();
                  }}
                  data-nav
                >
                  {row.missingActionLabel ?? 'Request'}
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
