import type { AudiobookItem } from '../-audiobooks.types';

import { useAudiobookContext } from './audiobook-context';
import styles from './audiobooks-page.module.css';

interface AudiobookWishlistButtonProps {
  book: AudiobookItem;
  /** "icon" for the corner of a cover, "full" for a labelled button. */
  variant?: 'icon' | 'full';
}

const HEART_PATH =
  'M12 20.5l-1.4-1.3C5.4 14.5 2 11.4 2 7.6 2 4.9 4.1 3 6.7 3c1.5 0 3 .7 3.9 1.9L12 6.2l1.4-1.3C14.3 3.7 15.8 3 17.3 3 19.9 3 22 4.9 22 7.6c0 3.8-3.4 6.9-8.6 11.6z';

/**
 * Want / stop wanting a book. One click, no question.
 *
 * It used to open a dialog asking whether to hold out for this narrator's
 * reading or accept any edition. That is a real question — an Audible ASIN is
 * one specific performance, and the release that turns up on an indexer may be
 * a different one — but it is the wrong moment to ask it. Almost everyone wants
 * the reading they just chose, and a dialog on every heart click taxes the most
 * common action in the app to serve the rarest intent.
 *
 * So adding assumes the reading you picked, and the choice moves to the wishlist
 * row, where a book that is not turning up is actually visible and loosening it
 * is a thing you would go and do on purpose.
 *
 * Apple-sourced results are excluded entirely: their id is an Apple collection
 * id, not an ASIN, and wishlisting one would store a row nothing could search.
 */
export function AudiobookWishlistButton({ book, variant = 'icon' }: AudiobookWishlistButtonProps) {
  const { isWishlisted, isWishlistBusy, toggleWishlist } = useAudiobookContext();

  if (book.source !== 'audible') return null;

  const wanted = isWishlisted(book.asin);
  const busy = isWishlistBusy(book.asin);
  const label = wanted ? 'On your wishlist' : 'Add to wishlist';

  const icon = wanted ? (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={HEART_PATH} fill="currentColor" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={HEART_PATH} fill="none" stroke="currentColor" strokeWidth="1.8" />
    </svg>
  );

  if (variant === 'full') {
    return (
      <button
        type="button"
        className={`${styles.wishlistFull} ${wanted ? styles.wishlistFullOn : ''}`}
        onClick={() => void toggleWishlist(book)}
        disabled={busy}
        aria-pressed={wanted}
      >
        {icon}
        {wanted ? 'Wishlisted' : 'Add to wishlist'}
      </button>
    );
  }

  return (
    <button
      type="button"
      className={`${styles.wishlistIcon} ${wanted ? styles.wishlistIconOn : ''}`}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        void toggleWishlist(book);
      }}
      disabled={busy}
      aria-pressed={wanted}
      aria-label={label}
      title={label}
    >
      {icon}
    </button>
  );
}
