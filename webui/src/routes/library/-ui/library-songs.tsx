import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState } from 'react';

import { AddToPlaylistButton } from '@/features/playlists/add-to-playlist';

import type { LibrarySearch, LibrarySong } from '../-library.types';

import { librarySongsQueryOptions } from '../-library.api';
import {
  playLibrarySong,
  readSongsResponse,
  songMeta,
  songPlaylistTrack,
} from '../-library.helpers';
import { deleteLibraryTrackRequest } from '../../artist-detail/-artist-detail.manage-actions';
import { ActionMenu } from '../../artist-detail/-ui/action-menu';
import { SmartDeleteDialog, TRACK_DELETE_COPY } from '../../artist-detail/-ui/smart-delete-dialog';
import styles from './library-songs.module.css';

/**
 * The library's songs (#1906): find a song by its title, tell copies apart by
 * their album, play it, put it on a playlist. Admins can also delete one from
 * the library, and its file from disk, through the same dialog the artist
 * page uses.
 */

function duration(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return '';
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

/** The album's cover, or a note when there is none or it won't load. */
function SongCover({ url }: { url?: string | null }) {
  const [failed, setFailed] = useState(false);
  if (!url || failed) return <span>♪</span>;
  return <img src={url} alt="" loading="lazy" onError={() => setFailed(true)} />;
}

function SongRow({
  song,
  isAdmin,
  onDelete,
}: {
  song: LibrarySong;
  isAdmin: boolean;
  onDelete: (song: LibrarySong) => void;
}) {
  const navigate = useNavigate();
  const albumHref = `/artist-detail/library/${song.artist_id}?album=${song.album_id}`;
  const openArtist = (album?: boolean) =>
    void navigate({
      to: '/artist-detail/$source/$id',
      params: { source: 'library', id: String(song.artist_id) },
      search: album ? { album: String(song.album_id) } : {},
    });
  const quality = [song.format, song.bitrate ? `${song.bitrate}k` : ''].filter(Boolean).join(' ');
  return (
    <li className={styles.row} data-song-id={song.id}>
      <button
        type="button"
        className={styles.art}
        onClick={() => void playLibrarySong(song)}
        disabled={!song.file_path}
        aria-label={`Play ${song.title}`}
        title={song.file_path ? 'Play' : 'No file to play'}
      >
        <SongCover url={song.album_thumb_url} />
        <span className={styles.playIcon} aria-hidden="true">
          ▶
        </span>
      </button>
      <div className={styles.text}>
        <div className={styles.title}>
          {song.title}
          {song.copies > 1 ? (
            <span
              className={styles.copies}
              title={`${song.copies} copies of this song in your library, on different albums or files`}
            >
              {song.copies} copies
            </span>
          ) : null}
        </div>
        <div className={styles.meta}>
          {song.artist_name}
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
        <AddToPlaylistButton track={songPlaylistTrack(song)} className={styles.iconBtn} />
        <ActionMenu
          heading={<strong>{song.title}</strong>}
          items={[
            {
              key: 'album',
              label: 'Open album',
              onSelect: () => openArtist(true),
            },
            {
              key: 'artist',
              label: 'Open artist',
              onSelect: () => openArtist(false),
            },
            ...(isAdmin
              ? [
                  {
                    key: 'delete',
                    label: 'Delete…',
                    danger: true,
                    title: 'Remove it from the library, and its file from disk if you choose',
                    onSelect: () => onDelete(song),
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

/** The rows, plus the admin delete dialog they share. */
export function LibrarySongList({
  songs,
  isAdmin,
  onChanged,
}: {
  songs: LibrarySong[];
  isAdmin: boolean;
  /** after a delete: refetch whatever list this is */
  onChanged: () => void;
}) {
  const [deleting, setDeleting] = useState<LibrarySong | null>(null);
  const remove = async (choice: string) => {
    const song = deleting;
    setDeleting(null);
    if (!song) return;
    try {
      const result = await deleteLibraryTrackRequest(
        song.id,
        choice === 'delete_file' ? 'delete_file' : 'db_only',
      );
      window.showToast?.(result.message, result.tone);
      if (result.extra) window.showToast?.(result.extra, 'warning');
      onChanged();
    } catch (error) {
      window.showToast?.(`Couldn't delete ${song.title}: ${(error as Error).message}`, 'error');
    }
  };
  return (
    <>
      <ul className={styles.list} id="library-songs-list">
        {songs.map((song) => (
          <SongRow key={song.id} song={song} isAdmin={isAdmin} onDelete={setDeleting} />
        ))}
      </ul>
      {deleting ? (
        <SmartDeleteDialog
          copy={{ ...TRACK_DELETE_COPY, title: `Delete ${deleting.title}` }}
          onChoose={(choice) => void remove(choice)}
          onClose={() => setDeleting(null)}
        />
      ) : null}
    </>
  );
}

const STRIP_SIZE = 5;

/** The strip's query. The page asks the same one (react-query shares it) to
 *  know whether a search that found no artists did find songs. */
export function songsStripQueryOptions(profileId: number, search: LibrarySearch) {
  return librarySongsQueryOptions(
    profileId,
    { ...search, letter: 'all', page: 1, sort: '' },
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
      <LibrarySongList songs={songs} isAdmin={isAdmin} onChanged={() => void query.refetch()} />
    </section>
  );
}
