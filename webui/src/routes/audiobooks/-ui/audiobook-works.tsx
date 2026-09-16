import { useCallback, useState } from 'react';

import type { AudiobookPersonProfile } from '../-audiobooks.types';

import { AudiobookCard } from './audiobook-card';
import { AudiobookRail } from './audiobook-rail';
import styles from './audiobooks-page.module.css';

/** Anchor id for a series shelf, so the jump bar can reach it. */
function shelfId(title: string): string {
  return `series-${title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')}`;
}

/**
 * Everything one person made, as shelves rather than as a list.
 *
 * The first cut of this rendered each series as a grid of compact rows —
 * 48px thumbnail, title, runtime — repeated thirteen times down the page. It
 * was legible and completely lifeless: the covers are the best asset an
 * audiobook catalogue has and that layout shrank them to the size of a favicon,
 * then made every series look identical to every other.
 *
 * So each series is a shelf of full-size covers in reading order, with its
 * position stamped on the cover. Same card the rest of the app uses, so a book
 * looks like a book wherever it appears, and the number is what turns a row into
 * an order.
 *
 * A prolific author has more shelves than fit on a screen, so the jump bar
 * across the top is the table of contents — it is also the only place the whole
 * shape of a career is visible at once.
 */
export function AudiobookWorks({ profile }: { profile: AudiobookPersonProfile }) {
  const [jumped, setJumped] = useState<string>('');

  const jumpTo = useCallback((title: string) => {
    setJumped(title);
    const target = document.getElementById(shelfId(title));
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const hasSeries = profile.series.length > 0;
  const hasStandalone = profile.standalone.length > 0;
  if (!hasSeries && !hasStandalone) return null;

  return (
    <section className={styles.works}>
      <header className={styles.worksHeader}>
        <h2 className={styles.worksTitle}>Works</h2>
        <span className={styles.worksCount}>{profile.total_books.toLocaleString()} titles</span>
      </header>

      {profile.series.length > 1 && (
        <nav className={styles.worksJump} aria-label="Jump to a series">
          {profile.series.map((group) => (
            <button
              key={group.title}
              type="button"
              className={`${styles.worksJumpPill} ${
                jumped === group.title ? styles.worksJumpPillActive : ''
              }`}
              onClick={() => jumpTo(group.title)}
            >
              {group.title}
              <span className={styles.worksJumpCount}>{group.books.length}</span>
            </button>
          ))}
          {hasStandalone && (
            <button
              type="button"
              className={`${styles.worksJumpPill} ${
                jumped === '__standalone' ? styles.worksJumpPillActive : ''
              }`}
              onClick={() => {
                setJumped('__standalone');
                document
                  .getElementById('series-standalone')
                  ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
              }}
            >
              Standalone
              <span className={styles.worksJumpCount}>{profile.standalone.length}</span>
            </button>
          )}
        </nav>
      )}

      {profile.series.map((group) => (
        <AudiobookRail
          key={group.title}
          id={shelfId(group.title)}
          title={group.title}
          subtitle={`${group.books.length} books · reading order`}
          books={group.books}
          seriesTitle={group.title}
        />
      ))}

      {hasStandalone && (
        <section className={styles.resultsSection} id="series-standalone">
          <header className={styles.railHeader}>
            <div>
              <h2 className={styles.railTitle}>{hasSeries ? 'Standalone' : 'All titles'}</h2>
              <p className={styles.railSubtitle}>
                {profile.standalone.length} title{profile.standalone.length === 1 ? '' : 's'}
                {hasSeries ? ' outside a series' : ''}
              </p>
            </div>
          </header>
          <div className={styles.grid}>
            {profile.standalone.map((book) => (
              <AudiobookCard book={book} key={book.asin} />
            ))}
          </div>
        </section>
      )}
    </section>
  );
}
