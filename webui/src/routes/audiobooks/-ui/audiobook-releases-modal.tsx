import { useEffect, useState } from 'react';

import type { AudiobookReleaseCandidate } from '../-audiobooks.types';

import { fetchReleases, grabRelease } from '../-audiobooks.api';
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
 * nature, so the loading state has to look like work rather than like a hang.
 */
export function AudiobookReleasesModal({ asin, title, onClose }: AudiobookReleasesModalProps) {
  const [releases, setReleases] = useState<AudiobookReleaseCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [grabbing, setGrabbing] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      const found = await fetchReleases(asin);
      if (cancelled) return;
      setReleases(found);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [asin]);

  const grab = async (release: AudiobookReleaseCandidate) => {
    setGrabbing(release.guid || release.title);
    setMessage('');
    const result = await grabRelease(asin, release);
    setGrabbing('');
    setMessage(result.ok ? 'Sent to your download client.' : result.error || 'Grab failed.');
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

        {message && <p className={styles.modalMessage}>{message}</p>}

        <div className={styles.modalBody}>
          {loading ? (
            <div className={styles.modalLoading}>
              <span className={styles.spinner} aria-hidden="true" />
              Searching your indexers…
            </div>
          ) : releases.length === 0 ? (
            <div className={styles.emptyState}>
              <h3>Nothing found</h3>
              <p>
                No indexer has this one right now. Add it to your wishlist and it will keep looking
                on its own.
              </p>
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
