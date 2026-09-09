import { Link } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { AudiobookReleaseContents } from '../-audiobooks.api';
import type { AudiobookDownload } from '../-audiobooks.types';
import type { AudiobookReleaseCandidate } from '../-audiobooks.types';

import {
  addToWishlist,
  blockRelease,
  cancelReleaseSearch,
  fetchDownloads,
  fetchReleaseContents,
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

/** Plain words for a download row's state. "staged" means nothing to anyone. */
const STATUS_WORDS: Record<string, string> = {
  queued: 'Queued',
  downloading: 'Downloading',
  importing: 'Importing',
  staged: 'Held back',
  completed: 'In your library',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

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
  // Contents are read on demand, one release at a time: reading every result
  // up front would fetch a .torrent from the indexer for every row on screen.
  const [openRow, setOpenRow] = useState('');
  const [contents, setContents] = useState<Record<string, AudiobookReleaseContents | null>>({});
  const [readingRow, setReadingRow] = useState('');
  // Which release each grab produced, so the row that was clicked can show
  // what happened to it instead of pointing at another page.
  const [blocked, setBlocked] = useState<Set<string>>(new Set());
  const [grabbedRefs, setGrabbedRefs] = useState<Record<string, string>>({});
  const [downloads, setDownloads] = useState<Record<string, AudiobookDownload>>({});
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
    const key = rowKey(release);
    setGrabbing(key);
    setMessage('');
    const result = await grabRelease(asin, release);
    setGrabbing('');
    setGrabbed(result.ok);
    if (result.ok && result.ref) {
      setGrabbedRefs((prev) => ({ ...prev, [key]: result.ref }));
    }
    setMessage(result.ok ? 'Sent to your download client.' : result.error || 'Grab failed.');
  };

  /**
   * Follow anything grabbed from this modal.
   *
   * A book can sit at "staged" for days with a perfectly good reason — the
   * release turned out to be part 1 of 5, or the torrent has not finished —
   * and without showing that reason a held book is indistinguishable from a
   * hung one. Only polls while something grabbed here is still in flight.
   */
  useEffect(() => {
    const refs = Object.values(grabbedRefs);
    if (refs.length === 0) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      if (cancelled) return;
      const rows = await fetchDownloads(false);
      if (cancelled) return;
      const byId: Record<string, AudiobookDownload> = {};
      for (const row of rows) byId[row.download_id] = row;
      setDownloads(byId);

      const settled = refs.every((ref) => {
        const status = byId[ref]?.status;
        return status === 'completed' || status === 'failed' || status === 'cancelled';
      });
      if (!settled) timer = setTimeout(() => void tick(), 2500);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [grabbedRefs]);

  /**
   * What to group a release under.
   *
   * NOT the peer on Soulseek. A peer offers one folder for a book, so grouping
   * by peer made every Soulseek row its own group and every one of them a
   * "best of" — a label on everything is a label on nothing. Soulseek is one
   * source with many peers, the way an indexer is one source with many
   * uploads. The peer is still named on the row itself.
   */
  const sourceOf = (release: AudiobookReleaseCandidate) =>
    release.protocol === 'soulseek' ? 'Soulseek' : release.indexer || release.protocol || 'unknown';

  const rowKey = (release: AudiobookReleaseCandidate) => release.guid || release.title;

  /**
   * The best from each source, then everything else grouped by protocol.
   *
   * Marking only the overall winner meant nothing could be labelled until the
   * whole search settled, because the next indexer to answer could take the
   * crown. Per source it is decidable the moment that source replies, and it
   * is the more useful question anyway: twenty rows from one indexer used to
   * bury a better hit from another.
   *
   * The server already sorted by score, so the first row seen for a source IS
   * that source's best and no re-scoring happens here.
   */
  const { ordered, bestKeys } = useMemo(() => {
    const seen = new Set<string>();
    const bests: AudiobookReleaseCandidate[] = [];
    const rest: AudiobookReleaseCandidate[] = [];

    for (const release of releases) {
      const source = sourceOf(release);
      if (seen.has(source)) {
        rest.push(release);
      } else {
        seen.add(source);
        bests.push(release);
      }
    }

    // Bests keep the server's score order. The remainder groups by protocol so
    // a long tail reads as "the rest of the torrents, then the rest of the
    // Soulseek folders" instead of interleaving them.
    rest.sort((a, b) => (a.protocol || '').localeCompare(b.protocol || ''));

    return {
      ordered: [...bests, ...rest],
      bestKeys: new Set(bests.map(rowKey)),
    };
  }, [releases]);

  /**
   * Refuse this release from now on. The RELEASE, not the book — the book
   * stays wanted, and the wishlist simply stops being offered this copy.
   */
  const block = async (release: AudiobookReleaseCandidate, key: string) => {
    setBlocked((prev) => new Set(prev).add(key));
    await blockRelease(release, asin, title, 'Blocked by hand');
  };

  const toggleContents = async (release: AudiobookReleaseCandidate, key: string) => {
    if (openRow === key) {
      setOpenRow('');
      return;
    }
    setOpenRow(key);
    // Cached: a release's contents do not change while the modal is open, and
    // re-reading would hit the indexer again on every expand.
    if (contents[key] !== undefined) return;
    setReadingRow(key);
    const found = await fetchReleaseContents(release);
    setContents((prev) => ({ ...prev, [key]: found }));
    setReadingRow('');
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
              {ordered.map((release) => {
                const key = rowKey(release);
                // Decidable as soon as this source has answered, so it appears
                // while the slower sources are still running.
                const best = bestKeys.has(key) && releases.length > 1;
                const tracked = downloads[grabbedRefs[key] || ''];
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
                        {/* Size alone cannot be judged: 800MB is generous for a
                            6-hour book and thin for a 40-hour one. Against the
                            runtime it becomes a bitrate, which can be. */}
                        {release.implied_kbps ? (
                          <span
                            className={`${styles.releaseTag} ${styles.qualityTag} ${
                              styles[`quality_${release.quality_band || 'standard'}`] || ''
                            }`}
                            title={
                              release.abridged
                                ? `${release.quality_note} Treat this as rough: an abridged release is shorter than the runtime this is measured against.`
                                : release.quality_note
                            }
                          >
                            ~{release.implied_kbps} kbps
                          </span>
                        ) : null}
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
                      {best && (
                        <span
                          className={styles.bestMatch}
                          title={
                            `The best ${sourceOf(release)} has for this book: closest ` +
                            'title match, then narrator, format and whether the size is ' +
                            'plausible for the runtime. The reasons below show the arithmetic.'
                          }
                        >
                          ★ Best from {sourceOf(release)}
                        </span>
                      )}

                      {/* Playing time can otherwise only be measured after
                          downloading, by decoding the files. A release that
                          names its bitrate can be caught here instead. */}
                      {release.short_warning && (
                        <span className={styles.shortWarning}>⚠ {release.short_warning}</span>
                      )}

                      {release.reasons.length > 0 && (
                        <span className={styles.releaseReasons}>{release.reasons.join(' · ')}</span>
                      )}

                      <div className={styles.releaseRowActions}>
                        <button
                          type="button"
                          className={styles.contentsToggle}
                          onClick={() => void toggleContents(release, key)}
                          aria-expanded={openRow === key}
                        >
                          {openRow === key ? '▾' : '▸'} What's inside
                        </button>

                        {/* A bad copy you never want offered again. The book
                            stays wanted; only this posting is refused. */}
                        <button
                          type="button"
                          className={styles.blockBtn}
                          onClick={() => void block(release, key)}
                          disabled={blocked.has(key)}
                          title="Never offer this release again. The book stays on your wishlist."
                        >
                          {blocked.has(key) ? 'Blocked' : 'Block'}
                        </button>
                      </div>

                      {openRow === key && (
                        <div className={styles.contents}>
                          {readingRow === key ? (
                            <span className={styles.contentsNote}>Reading the release…</span>
                          ) : contents[key] === null ? (
                            <span className={styles.contentsNote}>
                              Could not read this release.
                            </span>
                          ) : contents[key]?.note ? (
                            <span className={styles.contentsNote}>{contents[key]?.note}</span>
                          ) : (
                            <>
                              <p className={styles.contentsSummary}>
                                <strong>{contents[key]?.summary.audio_count}</strong>{' '}
                                {contents[key]?.summary.audio_count === 1
                                  ? 'audio file'
                                  : 'audio files'}
                                {contents[key]?.summary.formats.length ? (
                                  <> · {contents[key]?.summary.formats.join(', ').toUpperCase()}</>
                                ) : null}
                                {contents[key]?.summary.extra_count ? (
                                  <>
                                    {' '}
                                    · {contents[key]?.summary.extra_count} extra
                                    {contents[key]?.summary.extra_count === 1 ? '' : 's'}
                                  </>
                                ) : null}
                              </p>
                              <ul className={styles.contentsList}>
                                {contents[key]?.files.map((file) => (
                                  <li
                                    className={styles.contentsFile}
                                    key={file.name}
                                    data-extra={
                                      /\.(mp3|m4a|m4b|flac|ogg|opus|wav|aac|wma)$/i.test(file.name)
                                        ? undefined
                                        : 'true'
                                    }
                                  >
                                    <span className={styles.contentsFileName}>{file.name}</span>
                                    <span className={styles.contentsFileSize}>
                                      {formatSize(file.size)}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            </>
                          )}
                        </div>
                      )}
                    </div>

                    {tracked ? (
                      <div className={styles.rowStatus}>
                        <span
                          className={`${styles.rowStatusLabel} ${
                            styles[`status_${tracked.status}`] || ''
                          }`}
                        >
                          {STATUS_WORDS[tracked.status] || tracked.status}
                        </span>
                        {tracked.status === 'downloading' && (
                          <span className={styles.rowStatusPct}>
                            {Math.round(tracked.progress)}%
                          </span>
                        )}
                        {/* The reason a book is held. Without it a staged book
                            looks identical to a hung one. */}
                        {tracked.completeness && tracked.status === 'staged' && (
                          <span className={styles.rowStatusWhy}>{tracked.completeness}</span>
                        )}
                        {tracked.error && (
                          <span className={styles.rowStatusWhy}>{tracked.error}</span>
                        )}
                      </div>
                    ) : (
                      <button
                        type="button"
                        className={styles.releaseGrab}
                        data-download-action=""
                        onClick={() => void grab(release)}
                        disabled={Boolean(grabbing)}
                      >
                        {grabbing === key ? 'Sending…' : 'Download'}
                      </button>
                    )}
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
