import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { DialogFrame } from '@/components/dialog';

import type { LibrarySong } from '../-library.types';

import { fetchSongCopies } from '../-library.api';
import { songQueueTrack } from '../-library.helpers';
import { deleteLibraryTrackRequest } from '../../artist-detail/-artist-detail.manage-actions';
import { SmartDeleteDialog, TRACK_DELETE_COPY } from '../../artist-detail/-ui/smart-delete-dialog';
import styles from './library-songs.module.css';
import { duration, SongCover } from './song-bits';

/**
 * Every copy of one song side by side (#1906): which album, which year, the
 * file's format, bitrate and size, best file first. Each plays, so you can
 * hear the difference; an admin can keep one and remove the rest, from the
 * library or from disk too.
 */

function size(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return '';
  return bytes >= 1024 * 1024 * 1024
    ? `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function SongCopiesDialog({
  song,
  isAdmin,
  onClose,
  onChanged,
}: {
  song: LibrarySong;
  isAdmin: boolean;
  onClose: () => void;
  /** after copies were removed */
  onChanged: () => void;
}) {
  const query = useQuery({
    queryKey: ['library', 'song-copies', String(song.id)],
    queryFn: () => fetchSongCopies(song.id),
  });
  const [keeping, setKeeping] = useState<LibrarySong | null>(null);
  const copies = query.data ?? [];

  const keepOnly = async (keep: LibrarySong, choice: string) => {
    setKeeping(null);
    const others = copies.filter((c) => String(c.id) !== String(keep.id));
    const mode = choice === 'delete_file' ? 'delete_file' : 'db_only';
    let failed = 0;
    for (const other of others) {
      try {
        await deleteLibraryTrackRequest(other.id, mode);
      } catch {
        failed += 1;
      }
    }
    window.showToast?.(
      failed
        ? `Removed ${others.length - failed} of ${others.length} copies; ${failed} couldn't be`
        : `Kept the ${keep.album_title || 'chosen'} copy, removed ${others.length}`,
      failed ? 'warning' : 'success',
    );
    onChanged();
    onClose();
  };

  return (
    <>
      <DialogFrame
        open
        onOpenChange={(next) => {
          if (!next) onClose();
        }}
        viewportClassName="modal-overlay"
        className={styles.copiesDialog}
        aria-label={`Copies of ${song.title}`}
      >
        <header className={styles.copiesHead}>
          <div>
            <h3>
              {copies.length || song.copies} copies of {song.title}
            </h3>
            <p>{song.artist_name}</p>
          </div>
          <button type="button" className={styles.iconBtn} aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        {query.isPending ? (
          <p className={styles.copiesNote}>Loading the copies…</p>
        ) : query.isError ? (
          <p className={styles.copiesNote}>Couldn&apos;t load the copies.</p>
        ) : (
          <ul className={styles.copiesList}>
            {copies.map((copy, index) => (
              <li key={copy.id} className={styles.copyRow}>
                <span className={styles.copyArt}>
                  <SongCover url={copy.album_thumb_url} />
                </span>
                <div className={styles.copyText}>
                  <div className={styles.copyAlbum}>
                    {copy.album_title || 'Unknown album'}
                    {copy.year ? <span className={styles.dot}>·</span> : null}
                    {copy.year ?? ''}
                    {index === 0 && copies.length > 1 ? (
                      <span
                        className={styles.best}
                        title="Lossless before lossy, then the higher bitrate"
                      >
                        Best file
                      </span>
                    ) : null}
                  </div>
                  <div className={styles.copySpecs}>
                    {[
                      copy.format,
                      copy.bitrate ? `${copy.bitrate} kbps` : '',
                      size(copy.file_size),
                      duration(copy.duration),
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </div>
                  {isAdmin && copy.file_path ? (
                    <div className={styles.copyPath} title={copy.file_path}>
                      {copy.file_path}
                    </div>
                  ) : null}
                </div>
                <div className={styles.copyActions}>
                  <button
                    type="button"
                    className={styles.barBtn}
                    disabled={!copy.file_path}
                    onClick={() => void window.playTrackList?.([songQueueTrack(copy)], song.title)}
                  >
                    ▶ Play
                  </button>
                  {isAdmin && copies.length > 1 ? (
                    <button
                      type="button"
                      className={styles.barBtn}
                      onClick={() => setKeeping(copy)}
                    >
                      Keep this one
                    </button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </DialogFrame>
      {keeping ? (
        <SmartDeleteDialog
          copy={{
            ...TRACK_DELETE_COPY,
            title: `Keep the ${keeping.album_title || 'chosen'} copy`,
            noun: copies.length - 1 === 1 ? 'other copy' : `${copies.length - 1} other copies`,
            keepDesc: 'Remove the other copies from the library. Their files stay on disk.',
            deleteTitle: 'Delete Their Files Too',
            deleteDesc: 'Remove the other copies and delete their audio files from disk.',
          }}
          onChoose={(choice) => void keepOnly(keeping, choice)}
          onClose={() => setKeeping(null)}
        />
      ) : null}
    </>
  );
}
