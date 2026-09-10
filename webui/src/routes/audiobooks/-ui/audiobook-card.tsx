import { Link } from '@tanstack/react-router';

import type { AudiobookItem } from '../-audiobooks.types';

import { useAudiobookContext } from './audiobook-context';
import { AudiobookPersonLinks } from './audiobook-person-links';
import { AudiobookRatingInline } from './audiobook-rating';
import { AudiobookWishlistButton } from './audiobook-wishlist-button';
import styles from './audiobooks-page.module.css';

/**
 * One audiobook card.
 *
 * The cover is the play surface: every Audible title carries a real preview, so
 * hovering a card offers the sample and clicking anywhere else opens the detail
 * page. Narrator is on the card on purpose — it is the fact listeners choose by
 * and the fact no other audiobook source can give us.
 */
interface AudiobookCardProps {
  book: AudiobookItem;
  /**
   * Position within the series being displayed, when the card sits in one.
   * Passed in rather than read off the book because a book can be in several
   * series at once and only the shelf knows which one it is standing on.
   */
  sequence?: string | null;
}

export function AudiobookCard({ book, sequence }: AudiobookCardProps) {
  const { playSample, isPlaying, isLoaded } = useAudiobookContext();
  const playing = isPlaying(book.asin);
  const loaded = isLoaded(book.asin);
  const seriesEntry = book.series[0];

  return (
    <div className={`${styles.card} ${loaded ? styles.cardActive : ''}`}>
      <Link
        to="/audiobooks/$asin"
        params={{ asin: book.asin }}
        className={styles.cardCoverLink}
        aria-label={book.title}
      >
        {book.cover_url ? (
          <img className={styles.cardCover} src={book.cover_url} alt="" loading="lazy" />
        ) : (
          <div className={`${styles.cardCover} ${styles.coverFallback}`} aria-hidden="true" />
        )}
        {sequence && <span className={styles.cardSequence}>{sequence}</span>}
        {book.owned && (
          <span className={styles.cardOwned} title="Already in your library">
            <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
              <path
                d="M2 8.5 6 12.5 14 4"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Owned
          </span>
        )}
        {book.runtime_formatted && (
          <span className={styles.cardRuntime}>{book.runtime_formatted}</span>
        )}
      </Link>

      <AudiobookWishlistButton book={book} />

      {book.sample_url && (
        <button
          type="button"
          className={`${styles.cardPlay} ${playing ? styles.cardPlayActive : ''}`}
          onClick={() => playSample(book)}
          aria-label={playing ? `Pause sample of ${book.title}` : `Play sample of ${book.title}`}
        >
          {playing ? (
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" />
              <rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8 5v14l11-7z" fill="currentColor" />
            </svg>
          )}
        </button>
      )}

      <div className={styles.cardBody}>
        <Link to="/audiobooks/$asin" params={{ asin: book.asin }} className={styles.cardTitle}>
          {book.title}
        </Link>
        {book.authors.length > 0 && (
          <div className={styles.cardAuthor}>
            <AudiobookPersonLinks people={book.authors} role="author" max={2} />
          </div>
        )}
        {book.narrators.length > 0 && (
          <div className={styles.cardNarrator}>
            Narrated by <AudiobookPersonLinks people={book.narrators} role="narrator" max={1} />
          </div>
        )}
        <div className={styles.cardFooter}>
          <AudiobookRatingInline rating={book.rating} />
          {!sequence && seriesEntry?.sequence && (
            <span className={styles.cardSeries}>
              Book {seriesEntry.sequence} · {seriesEntry.title}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
