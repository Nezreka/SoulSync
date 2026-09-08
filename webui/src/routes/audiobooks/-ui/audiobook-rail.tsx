import type { AudiobookItem } from '../-audiobooks.types';

import { AudiobookCard } from './audiobook-card';
import styles from './audiobooks-page.module.css';
import { useEdgeScroll } from './use-edge-scroll';

interface AudiobookRailProps {
  title: string;
  books: AudiobookItem[];
  subtitle?: string;
  onSeeAll?: () => void;
  /**
   * When the rail IS a series, the series' title. Each card then shows its
   * position in that series on the cover, which is what turns a row of covers
   * into a reading order.
   */
  seriesTitle?: string;
  id?: string;
}

/**
 * A horizontally scrolling shelf.
 *
 * The arrows only appear when there is somewhere to go — a shelf that fits on
 * screen showing dead arrows is the tell of a rail that was built for one
 * viewport width.
 */
export function AudiobookRail({
  title,
  books,
  subtitle,
  onSeeAll,
  seriesTitle,
  id,
}: AudiobookRailProps) {
  const { ref, canScrollLeft, canScrollRight, sync, scrollByPage, onWheel } =
    useEdgeScroll<HTMLDivElement>(books.length);

  if (books.length === 0) return null;

  return (
    <section className={styles.rail} id={id}>
      <header className={styles.railHeader}>
        <div>
          <h2 className={styles.railTitle}>{title}</h2>
          {subtitle && <p className={styles.railSubtitle}>{subtitle}</p>}
        </div>
        <div className={styles.railControls}>
          {onSeeAll && (
            <button type="button" className={styles.railSeeAll} onClick={onSeeAll}>
              See all
            </button>
          )}
          <button
            type="button"
            className={styles.railArrow}
            onClick={() => scrollByPage(-1)}
            disabled={!canScrollLeft}
            aria-label={`Scroll ${title} left`}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M15 5l-7 7 7 7"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
          <button
            type="button"
            className={styles.railArrow}
            onClick={() => scrollByPage(1)}
            disabled={!canScrollRight}
            aria-label={`Scroll ${title} right`}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M9 5l7 7-7 7"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      </header>

      <div className={styles.railTrack} ref={ref} onScroll={sync} onWheel={onWheel}>
        {books.map((book) => (
          <div className={styles.railItem} key={`${book.source}-${book.asin}`}>
            <AudiobookCard
              book={book}
              sequence={
                seriesTitle
                  ? (book.series.find((entry) => entry.title === seriesTitle)?.sequence ?? null)
                  : null
              }
            />
          </div>
        ))}
      </div>
    </section>
  );
}
