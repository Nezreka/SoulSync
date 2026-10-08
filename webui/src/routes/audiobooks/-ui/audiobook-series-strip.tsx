import { Link } from '@tanstack/react-router';

import type { AudiobookItem } from '../-audiobooks.types';

import styles from './audiobooks-page.module.css';

interface AudiobookSeriesStripProps {
  seriesTitle: string;
  books: AudiobookItem[];
  currentAsin: string;
}

export type SeriesOwnership = 'owned' | 'edition' | null;

/**
 * what the strip says about each book: 'owned' when this asin is in the
 * library, 'edition' when it isn't but another book at the same number is.
 * a series lists the novel, its dramatized version and the part 1 / part 2
 * splits as separate books at one number, and owning any of them means you
 * have that story.
 */
export function seriesOwnership(books: readonly AudiobookItem[]): Map<string, SeriesOwnership> {
  const ownedAt = new Set<string>();
  for (const book of books) {
    const seq = book.series[0]?.sequence;
    if (book.owned && seq) ownedAt.add(seq);
  }
  const out = new Map<string, SeriesOwnership>();
  for (const book of books) {
    const seq = book.series[0]?.sequence;
    out.set(book.asin, book.owned ? 'owned' : seq && ownedAt.has(seq) ? 'edition' : null);
  }
  return out;
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
  const ownership = seriesOwnership(books);
  const ownedCount = books.filter((b) => b.owned).length;

  return (
    <section className={styles.seriesStrip}>
      <header className={styles.railHeader}>
        <div>
          <h2 className={styles.railTitle}>{seriesTitle}</h2>
          <p className={styles.railSubtitle}>
            {books.length} book{books.length === 1 ? '' : 's'} in reading order
            {ownedCount > 0 ? ` · ${ownedCount} in your library` : ''}
          </p>
        </div>
      </header>

      <ol className={styles.seriesTrack}>
        {books.map((book) => {
          const entry = book.series[0];
          const current = book.asin === currentAsin;
          const own = ownership.get(book.asin);
          return (
            <li className={styles.seriesItem} key={book.asin}>
              <Link
                to="/audiobooks/$asin"
                params={{ asin: book.asin }}
                className={`${styles.seriesLink} ${current ? styles.seriesLinkCurrent : ''} ${
                  own === 'owned' ? styles.seriesLinkOwned : ''
                }`}
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
                {own === 'owned' ? (
                  <span className={styles.seriesOwned} title="Already in your library">
                    <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true">
                      <path
                        d="M2 8.5 6 12.5 14 4"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    Owned
                  </span>
                ) : own === 'edition' ? (
                  <span
                    className={styles.seriesEdition}
                    title="You have another edition of this book"
                  >
                    Other edition
                  </span>
                ) : null}
                {current && <span className={styles.seriesCurrentTag}>You’re here</span>}
              </Link>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
