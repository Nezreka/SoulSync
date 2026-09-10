import type { AudiobookRating } from '../-audiobooks.types';

import styles from './audiobooks-page.module.css';

const STAR_LEVELS = ['5', '4', '3', '2', '1'] as const;

function formatCount(count: number): string {
  if (count >= 1000) return `${(count / 1000).toFixed(count >= 10000 ? 0 : 1)}k`;
  return String(count);
}

/**
 * A five-star row filled to the average.
 *
 * Fills are per-star clipped rather than rounded, so 4.8 shows four full stars
 * and one that is 80% filled instead of five identical ones.
 */
export function AudiobookStars({
  rating,
  size = 14,
}: {
  rating?: AudiobookRating | null;
  size?: number;
}) {
  if (!rating || rating.average == null) return null;
  const average = rating.average;

  return (
    <span
      className={styles.stars}
      style={{ ['--star-size' as string]: `${size}px` }}
      aria-label={`${average} out of 5`}
    >
      {[0, 1, 2, 3, 4].map((index) => {
        const fill = Math.min(1, Math.max(0, average - index));
        return (
          <span key={index} className={styles.star}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M12 2l2.9 6.3 6.9.8-5.1 4.7 1.4 6.8L12 17.3 5.9 20.6l1.4-6.8L2.2 9.1l6.9-.8z"
                className={styles.starTrack}
              />
            </svg>
            <span className={styles.starFill} style={{ width: `${fill * 100}%` }}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path
                  d="M12 2l2.9 6.3 6.9.8-5.1 4.7 1.4 6.8L12 17.3 5.9 20.6l1.4-6.8L2.2 9.1l6.9-.8z"
                  className={styles.starOn}
                />
              </svg>
            </span>
          </span>
        );
      })}
    </span>
  );
}

/** Compact "4.8 (98k)" used on cards, where a whole histogram would not fit. */
export function AudiobookRatingInline({ rating }: { rating?: AudiobookRating | null }) {
  if (!rating || rating.average == null) return null;
  return (
    <span className={styles.ratingInline}>
      <AudiobookStars rating={rating} size={12} />
      <span className={styles.ratingValue}>{rating.average.toFixed(1)}</span>
      {rating.count > 0 && (
        <span className={styles.ratingCount}>({formatCount(rating.count)})</span>
      )}
    </span>
  );
}

/**
 * The full histogram.
 *
 * Audible returns real per-star counts, so this is the actual distribution and
 * not five bars derived from an average. A 4.5 built from mostly fives and a
 * few ones reads very differently from a 4.5 that is all fours, and this is the
 * only place a listener can see the difference.
 */
export function AudiobookRatingBreakdown({ rating }: { rating?: AudiobookRating | null }) {
  if (!rating || !rating.count) return null;
  const max = Math.max(...STAR_LEVELS.map((level) => rating.distribution[level] || 0), 1);

  return (
    <div className={styles.ratingBreakdown}>
      <div className={styles.ratingSummary}>
        <span className={styles.ratingBig}>{rating.average?.toFixed(1) ?? '—'}</span>
        <AudiobookStars rating={rating} size={16} />
        <span className={styles.ratingTotal}>{rating.count.toLocaleString()} ratings</span>
      </div>
      <div className={styles.ratingBars}>
        {STAR_LEVELS.map((level) => {
          const count = rating.distribution[level] || 0;
          const share = rating.count ? Math.round((count / rating.count) * 100) : 0;
          return (
            <div className={styles.ratingBarRow} key={level}>
              <span className={styles.ratingBarLabel}>{level}</span>
              <div className={styles.ratingBarTrack}>
                <div
                  className={styles.ratingBarFill}
                  style={{ width: `${(count / max) * 100}%` }}
                />
              </div>
              <span className={styles.ratingBarPct}>{share}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
