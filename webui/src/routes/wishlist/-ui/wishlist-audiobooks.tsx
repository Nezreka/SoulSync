import { Link } from '@tanstack/react-router';
import { useCallback, useEffect, useMemo, useState } from 'react';

import type {
  AudiobookNarratorMode,
  AudiobookWishlistCounts,
  AudiobookWishlistEntry,
  AudiobookWishlistStatus,
  AudiobookWishlistSummary,
} from '@/routes/audiobooks/-audiobooks.types';

import {
  fetchWishlist,
  removeFromWishlist,
  runWishlistPass,
  setNarratorMode,
} from '@/routes/audiobooks/-audiobooks.api';
import { AudiobookReleasesModal } from '@/routes/audiobooks/-ui/audiobook-releases-modal';

import styles from './wishlist-audiobooks.module.css';

const FILTERS: { key: AudiobookWishlistStatus | 'all'; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'wanted', label: 'Looking' },
  { key: 'grabbed', label: 'Downloading' },
  { key: 'done', label: 'Got it' },
  { key: 'failed', label: 'Not found yet' },
];

const STATUS_LABELS: Record<AudiobookWishlistStatus, string> = {
  wanted: 'Looking',
  searching: 'Searching now',
  grabbed: 'Sent to downloads',
  done: 'In your library',
  failed: 'Not found yet',
};

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
 * The audiobooks half of the wishlist page.
 *
 * A separate list from music on purpose — the same isolation the video side
 * keeps between movies, shows and channels. They share the page and nothing
 * else: different database, different search, different acquisition chain.
 *
 * It is deliberately honest about waiting. An audiobook is usually not on any
 * indexer the day it is wanted, so every row shows how many times it has been
 * looked for and when it was last tried; without that, "nothing found yet" and
 * "nothing is happening" look identical, and only one of them is a bug.
 */
export function WishlistAudiobooks() {
  const [items, setItems] = useState<AudiobookWishlistEntry[]>([]);
  const [counts, setCounts] = useState<AudiobookWishlistCounts | null>(null);
  const [worker, setWorker] = useState<AudiobookWishlistSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<AudiobookWishlistStatus | 'all'>('all');
  const [searching, setSearching] = useState(false);
  const [notice, setNotice] = useState('');
  const [releasesFor, setReleasesFor] = useState<AudiobookWishlistEntry | null>(null);

  const load = useCallback(async () => {
    const view = await fetchWishlist();
    setItems(view.items);
    setCounts(view.counts);
    setWorker(view.worker);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const shown = useMemo(
    () => (filter === 'all' ? items : items.filter((item) => item.status === filter)),
    [items, filter],
  );

  const searchNow = async () => {
    setSearching(true);
    setNotice('');
    const summary = await runWishlistPass();
    setSearching(false);
    if (!summary) {
      setNotice('Could not run a search pass.');
    } else {
      const checked = summary.checked ?? 0;
      setNotice(
        checked === 0
          ? 'Nothing was due for another look yet.'
          : `Checked ${checked}, sent ${summary.grabbed ?? 0} to downloads.`,
      );
    }
    await load();
  };

  /**
   * Loosening or tightening which reading will do.
   *
   * This lives here rather than behind a dialog when the book is added: almost
   * everyone wants the reading they just picked, and a question on every heart
   * click taxes the commonest action to serve the rarest intent. A book that is
   * not turning up is visible on this row, which is exactly the moment someone
   * decides they would take another narrator after all.
   */
  const changeNarratorMode = async (asin: string, mode: AudiobookNarratorMode) => {
    setItems((current) =>
      current.map((item) => (item.asin === asin ? { ...item, narrator_mode: mode } : item)),
    );
    const ok = await setNarratorMode(asin, mode);
    if (!ok) await load();
  };

  const remove = async (asin: string) => {
    setItems((current) => current.filter((item) => item.asin !== asin));
    await removeFromWishlist(asin);
    await load();
  };

  const backoffHours = Math.round((worker?.retry_after_seconds ?? 0) / 3600);

  return (
    <div className={styles.panel}>
      <header className={styles.head}>
        <div className={styles.headText}>
          <p className={styles.blurb}>
            Searched on a schedule by the{' '}
            <strong>{worker?.automation_name ?? 'audiobook wishlist'}</strong> automation
            {backoffHours > 0 && (
              <>
                {' '}
                — each book then waits {backoffHours} hours between attempts, so one that isn't out
                yet can't crowd out the rest
              </>
            )}
            . Change how often it runs on the Automations page.
          </p>
        </div>

        <div className={styles.headActions}>
          <button
            type="button"
            className={styles.searchNow}
            onClick={() => void searchNow()}
            disabled={searching}
          >
            {searching ? 'Searching…' : 'Search now'}
          </button>
        </div>
      </header>

      {notice && <p className={styles.notice}>{notice}</p>}

      {counts && counts.total > 0 && (
        <nav className={styles.filters} aria-label="Filter by status">
          {FILTERS.map((entry) => {
            const total = entry.key === 'all' ? counts.total : (counts[entry.key] ?? 0);
            if (entry.key !== 'all' && total === 0) return null;
            return (
              <button
                key={entry.key}
                type="button"
                className={`${styles.filter} ${filter === entry.key ? styles.filterOn : ''}`}
                onClick={() => setFilter(entry.key)}
              >
                {entry.label}
                <span className={styles.filterCount}>{total}</span>
              </button>
            );
          })}
        </nav>
      )}

      {loading ? (
        <div className={styles.skeleton} aria-hidden="true" />
      ) : shown.length === 0 ? (
        <div className={styles.empty}>
          <h3>{items.length === 0 ? 'No audiobooks wanted yet' : 'Nothing in this state'}</h3>
          <p>
            {items.length === 0
              ? 'Add a book from anywhere in the catalogue and it will keep looking until it turns up.'
              : 'Try another filter.'}
          </p>
          <Link to="/audiobooks" className={styles.browseLink}>
            Browse audiobooks
          </Link>
        </div>
      ) : (
        <ul className={styles.grid}>
          {shown.map((item) => (
            <li className={styles.card} key={item.asin}>
              <Link
                to="/audiobooks/$asin"
                params={{ asin: item.asin }}
                className={styles.coverLink}
                aria-label={item.title}
              >
                <div className={styles.art}>
                  {item.cover_url ? (
                    <img className={styles.cover} src={item.cover_url} alt="" loading="lazy" />
                  ) : (
                    <span className={styles.coverBlank} aria-hidden="true">
                      &#9835;
                    </span>
                  )}
                  <span className={styles.scrim} aria-hidden="true" />
                  <span
                    className={`${styles.badge} ${
                      item.status === 'done'
                        ? styles.badgeDone
                        : item.status === 'grabbed'
                          ? styles.badgeActive
                          : item.status === 'failed'
                            ? styles.badgeFailed
                            : ''
                    }`}
                  >
                    {STATUS_LABELS[item.status] ?? item.status}
                  </span>
                </div>
              </Link>

              <div className={styles.info}>
                <Link to="/audiobooks/$asin" params={{ asin: item.asin }} className={styles.title}>
                  {item.title}
                </Link>
                <span className={styles.byline}>{item.authors.join(', ')}</span>
                {item.series_title && (
                  <span className={styles.series}>
                    {item.series_sequence ? `Book ${item.series_sequence} of ` : ''}
                    {item.series_title}
                  </span>
                )}
                <span className={styles.trail} title={item.last_error || undefined}>
                  {item.attempt_count === 0
                    ? 'Not looked for yet'
                    : `Looked ${item.attempt_count}\u00d7 \u00b7 last ${relativeTime(item.last_attempt_at)}`}
                  {item.last_error ? ` \u00b7 ${item.last_error}` : ''}
                </span>
              </div>

              {/* the controls stay off the artwork and appear on hover, the way
                  the video wishlist keeps its poster clean */}
              <div className={styles.actions}>
                {item.narrators.length > 0 && (
                  <button
                    type="button"
                    className={styles.action}
                    title={
                      item.narrator_mode === 'exact'
                        ? `Only ${item.narrators[0]}'s reading will do \u2014 click to accept any narrator`
                        : 'Any narrator will do \u2014 click to hold out for the one you picked'
                    }
                    onClick={() =>
                      void changeNarratorMode(
                        item.asin,
                        item.narrator_mode === 'exact' ? 'any' : 'exact',
                      )
                    }
                  >
                    {item.narrator_mode === 'exact' ? 'Narrator locked' : 'Any narrator'}
                  </button>
                )}
                <button
                  type="button"
                  className={styles.action}
                  onClick={() => setReleasesFor(item)}
                >
                  Find releases
                </button>
                <button
                  type="button"
                  className={`${styles.action} ${styles.actionDanger}`}
                  onClick={() => void remove(item.asin)}
                >
                  Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {releasesFor && (
        <AudiobookReleasesModal
          asin={releasesFor.asin}
          title={releasesFor.title}
          onClose={() => setReleasesFor(null)}
        />
      )}
    </div>
  );
}
