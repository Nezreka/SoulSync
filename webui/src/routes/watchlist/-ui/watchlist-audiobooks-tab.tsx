import { Link } from '@tanstack/react-router';
import { useCallback, useEffect, useState } from 'react';

import type { AudiobookFollowedAuthor } from '@/routes/audiobooks/-audiobooks.types';

import {
  fetchFollowedAuthors,
  runAuthorScan,
  unfollowAuthor,
} from '@/routes/audiobooks/-audiobooks.api';

import styles from './watchlist-audiobooks-tab.module.css';

function relativeTime(seconds: number): string {
  if (!seconds) return 'never';
  const delta = Date.now() / 1000 - seconds;
  if (delta < 90) return 'just now';
  const minutes = Math.round(delta / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/**
 * Authors you follow, and what following them has turned up.
 *
 * Following an author does not queue their back catalogue — it records the day
 * you followed and wishlists only what they publish after it. That is why each
 * row shows the watching-since date: without it, "0 releases" looks like a
 * broken scan rather than an author who simply has not published since Tuesday.
 */
export function WatchlistAudiobooksTab({ searchFilter }: { searchFilter?: string }) {
  const [authors, setAuthors] = useState<AudiobookFollowedAuthor[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    setAuthors(await fetchFollowedAuthors());
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const scanNow = async () => {
    setScanning(true);
    setNotice('');
    const summary = await runAuthorScan();
    setScanning(false);
    if (!summary) {
      setNotice('Could not check your authors.');
    } else {
      const checked = summary.authors ?? 0;
      setNotice(
        checked === 0
          ? 'No author was due another look yet.'
          : `Checked ${checked}, wishlisted ${summary.wishlisted ?? 0} new release${
              summary.wishlisted === 1 ? '' : 's'
            }.`,
      );
    }
    await load();
  };

  const stopFollowing = async (name: string) => {
    setAuthors((current) => current.filter((author) => author.name !== name));
    await unfollowAuthor(name);
    await load();
  };

  const filter = (searchFilter ?? '').trim().toLowerCase();
  const shown = filter
    ? authors.filter((author) => author.name.toLowerCase().includes(filter))
    : authors;

  return (
    <div className={styles.panel}>
      <header className={styles.head}>
        <p className={styles.blurb}>
          Checked once a day. New releases are added to your audiobook wishlist automatically —
          nothing published before you followed them is queued.
        </p>
        <button
          type="button"
          className={styles.scanNow}
          onClick={() => void scanNow()}
          disabled={scanning}
        >
          {scanning ? 'Checking…' : 'Check now'}
        </button>
      </header>

      {notice && <p className={styles.notice}>{notice}</p>}

      {loading ? (
        <div className={styles.skeleton} aria-hidden="true" />
      ) : shown.length === 0 ? (
        <div className={styles.empty}>
          <h3>{authors.length === 0 ? 'No authors followed' : 'No match'}</h3>
          <p>
            {authors.length === 0
              ? 'Open an author from the audiobooks catalogue and follow them — their next release lands on your wishlist on its own.'
              : 'No followed author matches that filter.'}
          </p>
          <Link to="/audiobooks" className={styles.browseLink}>
            Browse audiobooks
          </Link>
        </div>
      ) : (
        <ul className={styles.rows}>
          {shown.map((author) => (
            <li className={styles.row} key={author.name}>
              <Link
                to="/audiobooks/author/$name"
                params={{ name: author.name }}
                className={styles.name}
              >
                {author.name}
              </Link>

              <span className={styles.meta}>
                Watching since {author.since_date || 'you followed them'}
                {' · '}
                {author.found_total === 0
                  ? 'nothing new yet'
                  : `${author.found_total} release${author.found_total === 1 ? '' : 's'} found`}
                {' · '}
                checked {relativeTime(author.last_scanned_at)}
                {author.last_error ? ` · ${author.last_error}` : ''}
              </span>

              <button
                type="button"
                className={styles.action}
                onClick={() => void stopFollowing(author.name)}
              >
                Unfollow
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
