import { Link } from '@tanstack/react-router';

import type { AudiobookItem } from '../-audiobooks.types';

import { useAudiobookContext } from './audiobook-context';
import { AudiobookPersonLinks } from './audiobook-person-links';
import { AudiobookStars } from './audiobook-rating';
import styles from './audiobooks-page.module.css';

/**
 * The billboard at the top of the browse page.
 *
 * The cover doubles as the backdrop, blurred and darkened, so the hero picks up
 * the book's own palette without needing a separate art asset.
 */
export function AudiobookHero({ book }: { book: AudiobookItem }) {
  const { playSample, isPlaying } = useAudiobookContext();
  const playing = isPlaying(book.asin);
  const backdrop = book.cover_url_large || book.cover_url;

  const facts = [
    book.runtime_formatted,
    book.format_type === 'unabridged' ? 'Unabridged' : book.format_type,
    book.release_date?.slice(0, 4),
  ].filter(Boolean);

  return (
    <section className={styles.hero}>
      {backdrop && (
        <div className={styles.heroBackdrop} style={{ backgroundImage: `url(${backdrop})` }} />
      )}
      <div className={styles.heroScrim} />

      <div className={styles.heroInner}>
        {backdrop && <img className={styles.heroCover} src={backdrop} alt="" />}

        <div className={styles.heroText}>
          <span className={styles.heroEyebrow}>Top seller</span>
          <h1 className={styles.heroTitle}>{book.title}</h1>
          {book.subtitle && <p className={styles.heroSubtitle}>{book.subtitle}</p>}

          <div className={styles.heroCredits}>
            {book.authors.length > 0 && (
              <span>
                By <AudiobookPersonLinks people={book.authors} role="author" max={3} />
              </span>
            )}
            {book.narrators.length > 0 && (
              <span className={styles.heroNarrator}>
                Narrated by <AudiobookPersonLinks people={book.narrators} role="narrator" max={3} />
              </span>
            )}
          </div>

          <div className={styles.heroFacts}>
            <AudiobookStars rating={book.rating} size={15} />
            {book.rating?.average != null && (
              <span className={styles.heroRating}>
                {book.rating.average.toFixed(1)} · {book.rating.count.toLocaleString()} ratings
              </span>
            )}
            {facts.map((fact) => (
              <span className={styles.heroFact} key={fact}>
                {fact}
              </span>
            ))}
          </div>

          {book.short_summary && <p className={styles.heroSummary}>{book.short_summary}</p>}

          <div className={styles.heroActions}>
            {book.sample_url && (
              <button type="button" className={styles.heroPrimary} onClick={() => playSample(book)}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  {playing ? (
                    <>
                      <rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" />
                      <rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" />
                    </>
                  ) : (
                    <path d="M8 5v14l11-7z" fill="currentColor" />
                  )}
                </svg>
                {playing ? 'Pause sample' : 'Play sample'}
              </button>
            )}
            <Link
              to="/audiobooks/$asin"
              params={{ asin: book.asin }}
              className={styles.heroSecondary}
            >
              View details
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
