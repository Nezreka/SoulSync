import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { type KeyboardEvent, type MouseEvent, useMemo, useState } from 'react';

import { useNowPlaying } from '@/features/playback/use-now-playing';
import { AddToPlaylistButton, openAddToPlaylist } from '@/features/playlists/add-to-playlist';
import {
  likeSongs,
  unlikeSongs,
  type UserPlaylistTrack,
} from '@/features/playlists/user-playlists';

import type { LibrarySearch, LibrarySong } from '../-library.types';

import { librarySongsQueryOptions } from '../-library.api';
import {
  matchParts,
  readSongsResponse,
  songMeta,
  songPlaylistTrack,
  songQueueTrack,
} from '../-library.helpers';
import { deleteLibraryTrackRequest } from '../../artist-detail/-artist-detail.manage-actions';
import { ActionMenu } from '../../artist-detail/-ui/action-menu';
import { SmartDeleteDialog, TRACK_DELETE_COPY } from '../../artist-detail/-ui/smart-delete-dialog';
import { SongCopiesDialog } from './library-song-copies';
import styles from './library-songs.module.css';
import { duration, SongCover } from './song-bits';

/**
 * The library's songs (#1906). Click a song to play it and the list after it;
 * the cover plays it too. The heart likes it, the playlist button files it,
 * the ⋯ menu plays it next, queues it, opens its album or artist, compares
 * its copies, and (admins) deletes it. Tick songs to act on several at once.
 * A search's hit is marked in the title and artist.
 */

/** ``text`` with the search's hit marked. */
function Marked({ text, query }: { text: string; query: string }) {
  return (
    <>
      {matchParts(text, query).map((part, i) =>
        part.hit ? (
          <mark key={i} className={styles.hit}>
            {part.text}
          </mark>
        ) : (
          part.text
        ),
      )}
    </>
  );
}

/** Three bars that move while the song plays and rest when it's paused. */
function PlayingBars({ playing }: { playing: boolean }) {
  return (
    <span className={styles.bars} data-playing={playing || undefined} aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

function HeartIcon({ filled }: { filled: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
      <path
        d="M12 20.5s-7.5-4.6-9.5-9.2C1 7.8 3.2 4.5 6.6 4.5c2.1 0 3.6 1.2 5.4 3.2 1.8-2 3.3-3.2 5.4-3.2 3.4 0 5.6 3.3 4.1 6.8-2 4.6-9.5 9.2-9.5 9.2z"
        fill={filled ? 'currentColor' : 'none'}
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
    </svg>
  );
}

interface RowProps {
  song: LibrarySong;
  query: string;
  isAdmin: boolean;
  selected: boolean;
  selecting: boolean;
  liked: boolean;
  nowPlaying: { here: boolean; playing: boolean };
  onPlay: () => void;
  onToggleSelect: (event: { shiftKey: boolean }) => void;
  onLike: () => void;
  onCompare: () => void;
  onDelete: () => void;
}

function SongRow({
  song,
  query,
  isAdmin,
  selected,
  selecting,
  liked,
  nowPlaying,
  onPlay,
  onToggleSelect,
  onLike,
  onCompare,
  onDelete,
}: RowProps) {
  const navigate = useNavigate();
  const albumHref = `/artist-detail/library/${song.artist_id}?album=${song.album_id}`;
  const openArtist = (album?: boolean) =>
    void navigate({
      to: '/artist-detail/$source/$id',
      params: { source: 'library', id: String(song.artist_id) },
      search: album ? { album: String(song.album_id) } : {},
    });
  const quality = [song.format, song.bitrate ? `${song.bitrate}k` : ''].filter(Boolean).join(' ');
  const playable = Boolean(song.file_path);

  // the row plays; while songs are ticked it ticks instead, the way a
  // selection works everywhere else. its own buttons and links do their thing
  const activate = (event: { shiftKey: boolean }) => {
    if (selecting) onToggleSelect(event);
    else if (playable) onPlay();
  };
  const fromControl = (target: EventTarget) =>
    (target as HTMLElement).closest('button, a, input, [role="button"], [role="menu"]') !== null;

  return (
    <li
      className={styles.row}
      data-song-id={song.id}
      data-selected={selected || undefined}
      data-playing={nowPlaying.here || undefined}
      tabIndex={0}
      aria-label={`${song.title} by ${song.artist_name}`}
      onClick={(e: MouseEvent) => {
        if (!fromControl(e.target)) activate(e);
      }}
      onKeyDown={(e: KeyboardEvent) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === 'Enter') {
          e.preventDefault();
          activate(e);
        } else if (e.key === ' ') {
          e.preventDefault();
          onToggleSelect(e);
        }
      }}
    >
      <input
        type="checkbox"
        className={styles.check}
        checked={selected}
        aria-label={`Select ${song.title}`}
        onChange={() => undefined}
        onClick={(e) => {
          e.stopPropagation();
          onToggleSelect(e);
        }}
      />
      <button
        type="button"
        className={styles.art}
        onClick={onPlay}
        disabled={!playable}
        aria-label={`Play ${song.title}`}
        title={playable ? 'Play' : 'No file to play'}
      >
        <SongCover url={song.album_thumb_url} />
        {nowPlaying.here ? (
          <span className={styles.nowPlaying}>
            <PlayingBars playing={nowPlaying.playing} />
          </span>
        ) : (
          <span className={styles.playIcon} aria-hidden="true">
            ▶
          </span>
        )}
      </button>
      <div className={styles.text}>
        <div className={styles.title}>
          <span className={styles.titleText}>
            <Marked text={song.title} query={query} />
          </span>
          {song.copies > 1 ? (
            // a span, not a button: phones give every button a 38px touch
            // height, which made this tag a pill that squeezed the title
            <span
              role="button"
              tabIndex={0}
              className={styles.copies}
              title="Compare the copies of this song"
              onClick={onCompare}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  e.stopPropagation();
                  onCompare();
                }
              }}
            >
              {song.copies} copies
            </span>
          ) : null}
        </div>
        <div className={styles.meta}>
          <Marked text={song.artist_name} query={query} />
          <span className={styles.dot}>·</span>
          <a className={styles.album} href={albumHref}>
            {song.album_title || 'Unknown album'}
          </a>
          {songMeta(song) ? (
            <>
              <span className={styles.dot}>·</span>
              {songMeta(song)}
            </>
          ) : null}
        </div>
      </div>
      {quality ? <span className={styles.quality}>{quality}</span> : <span />}
      <span className={styles.time}>{duration(song.duration)}</span>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.iconBtn}
          data-liked={liked || undefined}
          aria-pressed={liked}
          aria-label={liked ? `Unlike ${song.title}` : `Like ${song.title}`}
          title={liked ? 'Remove from Liked Songs' : 'Save to Liked Songs'}
          onClick={onLike}
        >
          <HeartIcon filled={liked} />
        </button>
        <AddToPlaylistButton track={songPlaylistTrack(song)} className={styles.iconBtn} />
        <ActionMenu
          heading={<strong>{song.title}</strong>}
          items={[
            ...(playable
              ? [
                  {
                    key: 'next',
                    label: 'Play next',
                    onSelect: () => window.playNext?.(songQueueTrack(song)),
                  },
                  {
                    key: 'queue',
                    label: 'Add to queue',
                    onSelect: () => window.addToQueue?.(songQueueTrack(song)),
                  },
                ]
              : []),
            { key: 'album', label: 'Open album', onSelect: () => openArtist(true) },
            { key: 'artist', label: 'Open artist', onSelect: () => openArtist(false) },
            ...(song.copies > 1
              ? [{ key: 'copies', label: `Compare ${song.copies} copies`, onSelect: onCompare }]
              : []),
            ...(isAdmin
              ? [
                  {
                    key: 'delete',
                    label: 'Delete…',
                    danger: true,
                    title: 'Remove it from the library, and its file from disk if you choose',
                    onSelect: onDelete,
                  },
                ]
              : []),
          ]}
          trigger={(props) => (
            <button
              type="button"
              {...props}
              className={styles.iconBtn}
              aria-label={`More for ${song.title}`}
              title="More"
            >
              ⋯
            </button>
          )}
        />
      </div>
    </li>
  );
}

/** Delete one song, or several, through the artist page's dialog. */
async function deleteSongs(songs: LibrarySong[], choice: string): Promise<void> {
  const mode = choice === 'delete_file' ? 'delete_file' : 'db_only';
  let failed = 0;
  let last: Awaited<ReturnType<typeof deleteLibraryTrackRequest>> | null = null;
  for (const song of songs) {
    try {
      last = await deleteLibraryTrackRequest(song.id, mode);
      if (last.extra) window.showToast?.(last.extra, 'warning');
    } catch {
      failed += 1;
    }
  }
  if (songs.length === 1 && last) {
    window.showToast?.(last.message, last.tone);
  } else if (songs.length > 1) {
    const done = songs.length - failed;
    window.showToast?.(
      failed
        ? `Deleted ${done} of ${songs.length} songs; ${failed} couldn't be`
        : `Deleted ${done} songs${mode === 'delete_file' ? ', files too' : ''}`,
      failed ? 'warning' : 'success',
    );
  }
  if (songs.length === 1 && !last) {
    window.showToast?.(`Couldn't delete ${songs[0].title}`, 'error');
  }
}

export interface LibrarySongListProps {
  songs: LibrarySong[];
  isAdmin: boolean;
  /** after a delete: refetch whatever list this is */
  onChanged: () => void;
  /** the search, to mark its hit */
  query?: string;
  /** where a play says it's from ("Songs", "Songs matching love") */
  context?: string;
  /** a newer list is loading: keep this one, dimmed */
  stale?: boolean;
}

/** The rows, their selection bar, and the dialogs they share. */
export function LibrarySongList({
  songs,
  isAdmin,
  onChanged,
  query = '',
  context = 'Songs',
  stale = false,
}: LibrarySongListProps) {
  const nowPlaying = useNowPlaying();
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [likes, setLikes] = useState<ReadonlyMap<string, boolean>>(() => new Map());
  const [deleting, setDeleting] = useState<LibrarySong[] | null>(null);
  const [comparing, setComparing] = useState<LibrarySong | null>(null);

  // a selection is of THESE rows: a new list (search, page) starts clean
  const listKey = songs.map((s) => s.id).join('|');
  const [selectionFor, setSelectionFor] = useState(listKey);
  if (selectionFor !== listKey) {
    setSelectionFor(listKey);
    setSelected(new Set());
    setAnchor(null);
  }

  const picked = useMemo(() => songs.filter((s) => selected.has(String(s.id))), [songs, selected]);
  const isLiked = (song: LibrarySong) => likes.get(String(song.id)) ?? Boolean(song.liked);

  const toggle = (index: number, shiftKey: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      const id = String(songs[index].id);
      if (shiftKey && anchor !== null) {
        const [from, to] = anchor < index ? [anchor, index] : [index, anchor];
        for (let i = from; i <= to; i += 1) next.add(String(songs[i].id));
      } else if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
    setAnchor(index);
  };

  const playFrom = (index: number) => {
    const queue = songs
      .slice(index)
      .filter((s) => s.file_path)
      .map(songQueueTrack);
    if (queue.length) void window.playTrackList?.(queue, context);
  };

  const setLiked = async (targets: LibrarySong[], like: boolean) => {
    const tracks = targets.map(songPlaylistTrack).filter((t): t is UserPlaylistTrack => t !== null);
    if (!tracks.length) return;
    setLikes((prev) => {
      const next = new Map(prev);
      for (const s of targets) next.set(String(s.id), like);
      return next;
    });
    try {
      if (like) await likeSongs(tracks);
      else await unlikeSongs(tracks);
      if (targets.length > 1) {
        window.showToast?.(
          like ? `Liked ${targets.length} songs` : `Removed ${targets.length} from Liked Songs`,
          'success',
        );
      }
    } catch (error) {
      // put the hearts back the way they were
      setLikes((prev) => {
        const next = new Map(prev);
        for (const s of targets) next.set(String(s.id), !like);
        return next;
      });
      window.showToast?.(
        `Couldn't update Liked Songs: ${error instanceof Error ? error.message : 'unknown error'}`,
        'error',
      );
    }
  };

  const remove = async (choice: string) => {
    const targets = deleting;
    setDeleting(null);
    if (!targets?.length) return;
    await deleteSongs(targets, choice);
    setSelected(new Set());
    onChanged();
  };

  const clear = () => setSelected(new Set());
  const playable = picked.filter((s) => s.file_path);

  return (
    <>
      <ul
        className={styles.list}
        id="library-songs-list"
        data-stale={stale || undefined}
        data-selecting={selected.size > 0 || undefined}
        aria-busy={stale || undefined}
      >
        {songs.map((song, index) => (
          <SongRow
            key={song.id}
            song={song}
            query={query}
            isAdmin={isAdmin}
            selected={selected.has(String(song.id))}
            selecting={selected.size > 0}
            liked={isLiked(song)}
            nowPlaying={{
              here: nowPlaying.id !== null && nowPlaying.id === String(song.id),
              playing: nowPlaying.playing,
            }}
            onPlay={() => playFrom(index)}
            onToggleSelect={(e) => toggle(index, e.shiftKey)}
            onLike={() => void setLiked([song], !isLiked(song))}
            onCompare={() => setComparing(song)}
            onDelete={() => setDeleting([song])}
          />
        ))}
      </ul>

      {selected.size > 0 ? (
        <div className={styles.selectionBar} role="toolbar" aria-label="Selected songs">
          <span className={styles.selectionCount}>{selected.size} selected</span>
          <button
            type="button"
            className={styles.barBtn}
            disabled={!playable.length}
            onClick={() => {
              void window.playTrackList?.(playable.map(songQueueTrack), context);
            }}
          >
            ▶ Play
          </button>
          <button
            type="button"
            className={styles.barBtn}
            disabled={!playable.length}
            onClick={() => window.addTracksToQueue?.(playable.map(songQueueTrack))}
          >
            Add to queue
          </button>
          <button
            type="button"
            className={styles.barBtn}
            onClick={(e) =>
              openAddToPlaylist(
                picked.map(songPlaylistTrack).filter((t): t is UserPlaylistTrack => t !== null),
                e.currentTarget,
              )
            }
          >
            Add to playlist
          </button>
          <button
            type="button"
            className={styles.barBtn}
            onClick={() => void setLiked(picked, !picked.every(isLiked))}
          >
            {picked.every(isLiked) ? 'Unlike' : '♥ Like'}
          </button>
          {isAdmin ? (
            <button
              type="button"
              className={`${styles.barBtn} ${styles.barDanger}`}
              onClick={() => setDeleting(picked)}
            >
              Delete…
            </button>
          ) : null}
          <button type="button" className={styles.barClear} onClick={clear}>
            Clear
          </button>
        </div>
      ) : null}

      {deleting ? (
        <SmartDeleteDialog
          copy={
            deleting.length === 1
              ? { ...TRACK_DELETE_COPY, title: `Delete ${deleting[0].title}` }
              : {
                  ...TRACK_DELETE_COPY,
                  title: `Delete ${deleting.length} songs`,
                  noun: `${deleting.length} songs`,
                  keepDesc: 'Remove their database entries only. Files stay on disk.',
                  deleteTitle: 'Delete Files Too',
                  deleteDesc:
                    'Remove them from the library and delete their audio files from disk.',
                }
          }
          onChoose={(choice) => void remove(choice)}
          onClose={() => setDeleting(null)}
        />
      ) : null}

      {comparing ? (
        <SongCopiesDialog
          song={comparing}
          isAdmin={isAdmin}
          onClose={() => setComparing(null)}
          onChanged={onChanged}
        />
      ) : null}
    </>
  );
}

/** Placeholder rows while the first page loads. */
export function LibrarySongSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <ul className={styles.list} aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <li key={i} className={`${styles.row} ${styles.skeleton}`}>
          <span />
          <span className={styles.skelArt} />
          <span className={styles.skelText}>
            <i />
            <i />
          </span>
        </li>
      ))}
    </ul>
  );
}

const STRIP_SIZE = 5;

/** The strip's query. The page asks the same one (react-query shares it) to
 *  know whether a search that found no artists did find songs. */
export function songsStripQueryOptions(profileId: number, search: LibrarySearch) {
  return librarySongsQueryOptions(
    profileId,
    { ...search, letter: 'all', page: 1, sort: '', dupes: false, quality: '' },
    STRIP_SIZE,
  );
}

/**
 * The top song matches above an artist or album search, so typing a song's
 * name in the library search finds it whichever view you're in.
 */
export function LibrarySongsStrip({
  profileId,
  search,
  isAdmin,
  onSeeAll,
}: {
  profileId: number;
  search: LibrarySearch;
  isAdmin: boolean;
  onSeeAll: () => void;
}) {
  const q = search.q.trim();
  const query = useQuery({
    ...songsStripQueryOptions(profileId, search),
    enabled: q.length >= 2,
  });
  if (q.length < 2) return null;
  let songs: LibrarySong[] = [];
  let total = 0;
  let capped = false;
  try {
    const read = readSongsResponse(query.data);
    songs = read.songs;
    total = read.pagination.totalCount;
    capped = read.capped;
  } catch {
    return null;
  }
  if (!songs.length) return null;
  return (
    <section className={styles.strip} aria-label="Songs that match">
      <header className={styles.stripHead}>
        <h3>Songs</h3>
        {total > songs.length || capped ? (
          <button type="button" className={styles.seeAll} onClick={onSeeAll}>
            See all {capped ? `${total.toLocaleString()}+` : total.toLocaleString()} songs →
          </button>
        ) : null}
      </header>
      <LibrarySongList
        songs={songs}
        isAdmin={isAdmin}
        query={q}
        context={`Songs matching "${q}"`}
        stale={query.isPlaceholderData}
        onChanged={() => void query.refetch()}
      />
    </section>
  );
}
