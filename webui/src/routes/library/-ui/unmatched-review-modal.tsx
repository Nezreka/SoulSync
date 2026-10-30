/**
 * The files that imported without a match (#1202), reviewed in one place.
 *
 * the banner used to send you to a made-up "Unknown Artist" page and leave
 * you to work out what each file was. this shows each file with the evidence
 * a person would use (its folder, what its name says, the album the rest of
 * its folder is on) and the one action that settles it:
 *
 * - a second copy of a song already matched beside it: remove the copy
 * - anything else: find its match, the search already filled in from that
 *   evidence (re-identify copies first and removes the original only once
 *   the re-import worked)
 *
 * every file plays, so an untagged one can be heard before deciding.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { createPortal } from 'react-dom';

import { useAccessibleModal } from '@/components/dialog';

import type { LibrarySong } from '../-library-v2.songs';

import {
  LIBRARY_V2_QUERY_KEY,
  libraryV2UnmatchedTracksQueryOptions,
  type LibraryV2UnmatchedTrack,
} from '../-library-v2.api';
import { deleteLibraryTrackRequest } from '../-library-v2.songs';
import styles from './unmatched-review-modal.module.css';

function duration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return '';
  const s = Math.round(seconds > 10_000 ? seconds / 1000 : seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function play(track: LibraryV2UnmatchedTrack) {
  void window.SoulSyncWebShellBridge?.playLibraryTrack(
    {
      id: track.track_id,
      lib2_track_id: track.track_id,
      title: track.guess.title || track.title,
      file_path: track.file_path,
      exact_path: true,
    },
    track.guess.album ?? '',
    track.guess.artist ?? '',
  );
}

function evidence(track: LibraryV2UnmatchedTrack): string {
  const { guess, duplicate_of: dup } = track;
  if (dup) {
    return `Looks like a second copy of “${dup.title}” on ${dup.album_title} by ${dup.artist_name}, already in your library.`;
  }
  if (guess.album && guess.artist) {
    return `The rest of its folder is ${guess.album} by ${guess.artist}${
      guess.track_number ? `, and its name says track ${guess.track_number}` : ''
    }.`;
  }
  return 'No tags, and its folder holds music from many albums, so there is nothing to go on but the name.';
}

export function UnmatchedReviewModal({
  onClose,
  onFindMatch,
}: {
  onClose: () => void;
  /** the review steps aside for the search; the banner brings it back */
  onFindMatch: (track: LibraryV2UnmatchedTrack) => void;
}) {
  const queryClient = useQueryClient();
  const query = useQuery(libraryV2UnmatchedTracksQueryOptions());
  const [busy, setBusy] = useState<number | null>(null);
  const close = useCallback(() => onClose(), [onClose]);
  const { ref, onBackdropClick } = useAccessibleModal<HTMLDivElement>(close);

  const refresh = () => void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });

  const removeDuplicate = async (track: LibraryV2UnmatchedTrack) => {
    const dup = track.duplicate_of;
    if (!dup) return;
    const ok = await window.showConfirmDialog?.({
      title: 'Remove the duplicate?',
      message: `“${track.file_name}” is deleted from disk. “${dup.title}” on ${dup.album_title} stays as it is.`,
      confirmText: 'Delete duplicate',
      destructive: true,
    });
    if (!ok) return;
    setBusy(track.file_id);
    try {
      const result = await deleteLibraryTrackRequest(
        { album_id: track.album_id, file_ids: [track.file_id] } as LibrarySong,
        'delete_file',
      );
      window.showToast?.(
        result.tone === 'success' ? 'Duplicate removed' : result.message,
        result.tone,
      );
      refresh();
    } catch (error) {
      window.showToast?.(
        error instanceof Error ? error.message : 'Could not remove the duplicate',
        'error',
      );
    } finally {
      setBusy(null);
    }
  };

  const tracks = query.data ?? [];

  return createPortal(
    <div
      className={styles.overlay}
      onClick={(e) => {
        e.stopPropagation();
        onBackdropClick(e);
      }}
    >
      <div
        ref={ref}
        className={styles.sheet}
        role="dialog"
        aria-modal="true"
        aria-labelledby="unmatched-review-title"
      >
        <header className={styles.head}>
          <div>
            <h3 id="unmatched-review-title">Tracks without a match</h3>
            <p>
              SoulSync couldn’t tell what these files are, so they’re parked under Unknown Artist.
              Here’s what each one gives away.
            </p>
          </div>
          <button type="button" className={styles.close} aria-label="Close" onClick={close}>
            &times;
          </button>
        </header>

        <div className={styles.body} aria-busy={query.isLoading || undefined}>
          {query.isLoading ? (
            <p className={styles.state}>Looking at the files…</p>
          ) : query.isError ? (
            <p className={styles.state}>Couldn’t load them. Try again in a moment.</p>
          ) : tracks.length === 0 ? (
            <p className={styles.state}>All matched. Nothing left to review.</p>
          ) : (
            <ul className={styles.list}>
              {tracks.map((track) => (
                <li key={track.file_id} className={styles.row}>
                  <button
                    type="button"
                    className={styles.play}
                    aria-label={`Play ${track.file_name}`}
                    title="Play"
                    onClick={() => play(track)}
                  >
                    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                      <path d="M8 5.5v13l11-6.5z" fill="currentColor" />
                    </svg>
                  </button>
                  <div className={styles.info}>
                    <div className={styles.name} title={track.file_path}>
                      {track.file_name}
                    </div>
                    <div className={styles.meta}>
                      {[track.format, duration(track.duration), track.folder]
                        .filter(Boolean)
                        .join(' · ')}
                    </div>
                    <div
                      className={`${styles.evidence} ${track.duplicate_of ? styles.duplicate : ''}`}
                    >
                      {evidence(track)}
                    </div>
                  </div>
                  <div className={styles.actions}>
                    {track.duplicate_of ? (
                      <button
                        type="button"
                        className={styles.danger}
                        disabled={busy === track.file_id}
                        onClick={() => void removeDuplicate(track)}
                      >
                        {busy === track.file_id ? 'Removing…' : 'Remove duplicate'}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className={track.duplicate_of ? styles.secondary : styles.primary}
                      onClick={() => onFindMatch(track)}
                    >
                      Find match
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
