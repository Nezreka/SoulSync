import { Link } from '@tanstack/react-router';

import type { AudiobookItem } from '../-audiobooks.types';

import styles from './audiobooks-page.module.css';

interface AudiobookSeriesStripProps {
  seriesTitle: string;
  books: AudiobookItem[];
  currentAsin: string;
}

/**
 * The series in reading order, with the book you are looking at marked.
 *
 * Order comes from the sequence Audible prints, so half-numbered novellas land
 * between the novels they sit between (Edgedancer at 2.5) and companions with
 * no number sit at the end. This is the view that answers "what do I listen to
 * next", which is the question a series page exists for.
 */
export function AudiobookSeriesStrip({
  seriesTitle,
  books,
  currentAsin,
}: AudiobookSeriesStripProps) {
  if (books.length <= 1) return null;

  return (
    <section className={styles.seriesStrip}>
      <header className={styles.railHeader}>
        <div>
          <h2 className={styles.railTitle}>{seriesTitle}</h2>
          <p className={styles.railSubtitle}>
            {books.length} book{books.length === 1 ? '' : 's'} in reading order
          </p>
        </div>
      </header>

      <ol className={styles.seriesTrack}>
        {books.map((book) => {
          const entry = book.series[0];
          const current = book.asin === currentAsin;
          return (
            <li className={styles.seriesItem} key={book.asin}>
              <Link
                to="/audiobooks/$asin"
                params={{ asin: book.asin }}
                className={`${styles.seriesLink} ${current ? styles.seriesLinkCurrent : ''}`}
                aria-current={current ? 'true' : undefined}
              >
                <span className={styles.seriesBadge}>{entry?.sequence || '—'}</span>
                {book.cover_url ? (
                  <img className={styles.seriesCover} src={book.cover_url} alt="" loading="lazy" />
                ) : (
                  <div
                    className={`${styles.seriesCover} ${styles.coverFallback}`}
                    aria-hidden="true"
                  />
                )}
                <span className={styles.seriesMeta}>
                  <span className={styles.seriesTitleText}>{book.title}</span>
                  {book.runtime_formatted && (
                    <span className={styles.seriesRuntime}>{book.runtime_formatted}</span>
                  )}
                </span>
                {current && <span className={styles.seriesCurrentTag}>You’re here</span>}
              </Link>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
