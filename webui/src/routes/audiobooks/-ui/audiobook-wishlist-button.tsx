import { useState } from 'react';

import type { AudiobookItem, AudiobookNarratorMode } from '../-audiobooks.types';

import { useAudiobookContext } from './audiobook-context';
import { AudiobookNarratorChoice } from './audiobook-narrator-choice';
import styles from './audiobooks-page.module.css';

interface AudiobookWishlistButtonProps {
  book: AudiobookItem;
  /** "icon" for the corner of a cover, "full" for a labelled button. */
  variant?: 'icon' | 'full';
}

const HEART_PATH =
  'M12 20.5l-1.4-1.3C5.4 14.5 2 11.4 2 7.6 2 4.9 4.1 3 6.7 3c1.5 0 3 .7 3.9 1.9L12 6.2l1.4-1.3C14.3 3.7 15.8 3 17.3 3 19.9 3 22 4.9 22 7.6c0 3.8-3.4 6.9-8.6 11.6z';

/**
 * Want / stop wanting a book.
 *
 * The wishlist is what turns "not on any indexer today" into "got it" without
 * anyone remembering to search again, so this is the primary action on a book
 * that cannot be downloaded right now — which, for an audiobook, is most of
 * them most of the time.
 *
 * Adding asks which reading to hold out for; removing does not, and neither
 * does a book with no known narrator, because there is nothing to hold to.
 *
 * Apple-sourced results are excluded entirely: their id is an Apple collection
 * id, not an ASIN, and wishlisting one would store a row nothing could search.
 */
export function AudiobookWishlistButton({ book, variant = 'icon' }: AudiobookWishlistButtonProps) {
  const { isWishlisted, isWishlistBusy, toggleWishlist } = useAudiobookContext();
  const [asking, setAsking] = useState(false);

  if (book.source !== 'audible') return null;

  const wanted = isWishlisted(book.asin);
  const busy = isWishlistBusy(book.asin);
  const label = wanted ? 'On your wishlist' : 'Add to wishlist';

  const activate = () => {
    if (wanted || book.narrator_names.length === 0) {
      void toggleWishlist(book);
      return;
    }
    setAsking(true);
  };

  const choose = (mode: AudiobookNarratorMode) => {
    setAsking(false);
    void toggleWishlist(book, mode);
  };

  const icon = wanted ? (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={HEART_PATH} fill="currentColor" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={HEART_PATH} fill="none" stroke="currentColor" strokeWidth="1.8" />
    </svg>
  );

  // Rendered BESIDE the button, never inside it: a dialog nested in a <button>
  // is invalid markup and swallows its own clicks.
  const prompt = asking ? (
    <AudiobookNarratorChoice book={book} onChoose={choose} onCancel={() => setAsking(false)} />
  ) : null;

  if (variant === 'full') {
    return (
      <>
        <button
          type="button"
          className={`${styles.wishlistFull} ${wanted ? styles.wishlistFullOn : ''}`}
          onClick={activate}
          disabled={busy}
          aria-pressed={wanted}
        >
          {icon}
          {wanted ? 'Wishlisted' : 'Add to wishlist'}
        </button>
        {prompt}
      </>
    );
  }

  return (
    <>
      <button
        type="button"
        className={`${styles.wishlistIcon} ${wanted ? styles.wishlistIconOn : ''}`}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          activate();
        }}
        disabled={busy}
        aria-pressed={wanted}
        aria-label={label}
        title={label}
      >
        {icon}
      </button>
      {prompt}
    </>
  );
}
