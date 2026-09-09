import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';

import type { AudiobookReleaseCandidate } from '../-audiobooks.types';

import {
  addToWishlist,
  cancelReleaseSearch,
  grabRelease,
  pollReleaseSearch,
  startReleaseSearch,
} from '../-audiobooks.api';
import { AudiobookOverlay } from './audiobook-overlay';
import styles from './audiobooks-page.module.css';

interface AudiobookReleasesModalProps {
  asin: string;
  title: string;
  onClose: () => void;
}

function formatSize(bytes: number): string {
  if (!bytes) return '';
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

/**
 * What is actually downloadable for one book.
 *
 * Deliberately not a one-click "download" that picks silently. The ranking is
 * good but it is a guess made from a release NAME — the same book can appear as
 * an m4b, a folder of mp3s, a dramatized adaptation and an abridgement, and only
 * the person listening knows which of those they wanted. So the list shows what
 * was found, in ranked order, with the arithmetic visible.
 *
 * The search behind this fans out to every configured indexer and is slow by
 * nature: up to three query variants in series, then Soulseek. Run as one
 * blocking request it showed nothing at all until every source had finished,
 * which reads as a hang on the one screen where somebody is waiting.
 *
 * So it streams, using the same start/poll contract the video side's download
 * modal uses: start the search, then poll and re-render what has arrived.
 * Each poll replaces the whole list rather than appending, because ranking is
 * global — a peer with the right narrator has to be able to land above a
 * torrent found two queries earlier.
 */
export function AudiobookReleasesModal({ asin, title, onClose }: AudiobookReleasesModalProps) {
  const [releases, setReleases] = useState<AudiobookReleaseCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [stage, setStage] = useState('');
  const [searchError, setSearchError] = useState('');
  const [grabbing, setGrabbing] = useState('');
  const [message, setMessage] = useState('');
  const [grabbed, setGrabbed] = useState(false);
  const [wishlisting, setWishlisting] = useState(false);
  const jobRef = useRef('');

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    setLoading(true);
    setReleases([]);
    setSearchError('');
    setStage('Starting the search');

    void (async () => {
      const job = await startReleaseSearch(asin);
      if (cancelled) return;
      if (!job) {
        setLoading(false);
        setSearchError('Could not start the search.');
        return;
      }
      jobRef.current = job.id;

      const tick = async () => {
        if (cancelled) return;
        const state = await pollReleaseSearch(job.id);
        if (cancelled) return;

        // A dropped poll is not fatal — try again on the next tick.
        if (state && !state.expired) {
          setReleases(state.releases);
          setStage(state.stage);
          if (state.error) setSearchError(state.error);
          if (state.complete) {
            setLoading(false);
            jobRef.current = '';
            return;
          }
        } else if (state?.expired) {
          // The job is gone. Keep whatever is already on screen rather than
          // blanking a list the user may be reading.
          setLoading(false);
          jobRef.current = '';
          return;
        }
        timer = setTimeout(() => void tick(), job.pollMs);
      };

      timer = setTimeout(() => void tick(), job.pollMs);
    })();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      // Closing the modal stops the work, rather than leaving a search running
      // for a book nobody is looking at any more.
      if (jobRef.current) void cancelReleaseSearch(jobRef.current);
      jobRef.current = '';
    };
  }, [asin]);

  const grab = async (release: AudiobookReleaseCandidate) => {
    setGrabbing(release.guid || release.title);
    setMessage('');
    const result = await grabRelease(asin, release);
    setGrabbing('');
    setGrabbed(result.ok);
    setMessage(result.ok ? 'Sent to your download client.' : result.error || 'Grab failed.');
  };

  // The empty state used to TELL the reader to wishlist the book and then give
  // them nothing to click.
  const wishlist = async () => {
    setWishlisting(true);
    const ok = await addToWishlist(asin);
    setWishlisting(false);
    setMessage(
      ok
        ? 'Added to your wishlist — it will keep looking on its own.'
        : 'Could not add it to your wishlist.',
    );
  };

  return (
    <AudiobookOverlay onClose={onClose} label={`Releases for ${title}`}>
      <div className={styles.modal}>
        <header className={styles.modalHeader}>
          <div>
            <span className={styles.modalEyebrow}>Releases</span>
            <h2 className={styles.modalTitle}>{title}</h2>
          </div>
          <button type="button" className={styles.modalClose} onClick={onClose} aria-label="Close">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M6 6l12 12M18 6L6 18"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </header>

        {message && (
          <p className={styles.modalMessage}>
            {message}
            {/* A grab with no way to go and watch it is a dead end. */}
            {grabbed && (
              <Link to="/active-downloads" className={styles.modalMessageLink} onClick={onClose}>
                View downloads
              </Link>
            )}
          </p>
        )}

        <div className={styles.modalBody}>
          {/* Results render as they arrive, so the list is usable while the
              slower sources are still answering. The strip below says what is
              still running rather than replacing what has already landed. */}
          {loading && (
            <div
              className={releases.length > 0 ? styles.searchStrip : styles.modalLoading}
              role="status"
              aria-live="polite"
            >
              <span className={styles.spinner} aria-hidden="true" />
              {stage || 'Searching your indexers…'}
            </div>
          )}

          {!loading && searchError && (
            <div className={styles.emptyState}>
              <h3>The search could not finish</h3>
              <p>{searchError}</p>
              <p>
                That is a source failing, not proof the book is unavailable, so nothing has been
                added to your wishlist.
              </p>
            </div>
          )}

          {!loading && !searchError && releases.length === 0 ? (
            <div className={styles.emptyState}>
              <h3>Nothing found</h3>
              <p>
                No indexer has this one right now. Add it to your wishlist and it will keep looking
                on its own.
              </p>
              <button
                type="button"
                className={styles.emptyAction}
                onClick={() => void wishlist()}
                disabled={wishlisting}
              >
                {wishlisting ? 'Adding…' : 'Add to wishlist'}
              </button>
            </div>
          ) : (
            <ul className={styles.releaseList}>
              {releases.map((release) => {
                const key = release.guid || release.title;
                return (
                  <li className={styles.releaseRow} key={key}>
                    <div className={styles.releaseMain}>
                      <span className={styles.releaseTitle}>{release.title}</span>
                      <div className={styles.releaseTags}>
                        {release.audio_format && (
                          <span className={`${styles.releaseTag} ${styles.releaseTagFormat}`}>
                            {release.audio_format.toUpperCase()}
                          </span>
                        )}
                        {release.abridged && (
                          <span className={`${styles.releaseTag} ${styles.releaseTagWarn}`}>
                            Abridged
                          </span>
                        )}
                        <span className={styles.releaseTag}>
                          {release.protocol === 'soulseek' ? 'Soulseek' : release.protocol}
                        </span>
                        {/* A peer IS the source on Soulseek, so it is named
                            rather than shown as "soulseek:someone". */}
                        {release.soulseek ? (
                          <span className={styles.releaseTag}>{release.soulseek.username}</span>
                        ) : (
                          release.indexer && (
                            <span className={styles.releaseTag}>{release.indexer}</span>
                          )
                        )}
                        {release.soulseek && (
                          <span className={styles.releaseTag}>
                            {release.soulseek.file_count}{' '}
                            {release.soulseek.file_count === 1 ? 'file' : 'files'}
                          </span>
                        )}
                        {formatSize(release.size_bytes) && (
                          <span className={styles.releaseTag}>
                            {formatSize(release.size_bytes)}
                          </span>
                        )}
                        {/* Free upload slots, not seeders: on Soulseek that is
                            what answers "can I actually get this right now". */}
                        {release.seeders != null && (
                          <span className={styles.releaseTag}>
                            {release.soulseek
                              ? `${release.seeders} slots free`
                              : `${release.seeders} seeders`}
                          </span>
                        )}
                      </div>
                      {release.reasons.length > 0 && (
                        <span className={styles.releaseReasons}>{release.reasons.join(' · ')}</span>
                      )}
                    </div>

                    <button
                      type="button"
                      className={styles.releaseGrab}
                      onClick={() => void grab(release)}
                      disabled={Boolean(grabbing)}
                    >
                      {grabbing === key ? 'Sending…' : 'Download'}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </AudiobookOverlay>
  );
}
