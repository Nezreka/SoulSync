import { useEffect } from 'react';

import type { AudiobookItem, AudiobookNarratorMode } from '../-audiobooks.types';

import styles from './audiobooks-page.module.css';

interface AudiobookNarratorChoiceProps {
  book: AudiobookItem;
  onChoose: (mode: AudiobookNarratorMode) => void;
  onCancel: () => void;
}

/**
 * The one question worth asking when a book goes on the wishlist.
 *
 * On Audible the narrator is baked into the ASIN — Jim Dale and Stephen Fry are
 * separate catalogue entries with their own covers and runtimes, not two
 * options on one book. So picking a book has already picked a performance, and
 * there is nothing to choose there.
 *
 * What is genuinely open is what happens at download time, because the release
 * that turns up on an indexer may be a different edition than the one wished
 * for. Hence exactly two answers, and never a combination: hold out for this
 * reading, or take whichever turns up first.
 */
export function AudiobookNarratorChoice({
  book,
  onChoose,
  onCancel,
}: AudiobookNarratorChoiceProps) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const narrator = book.narrator_names[0] ?? '';
  const cover = book.cover_url || book.cover_url_large;

  return (
    <div className={styles.modalBackdrop} onClick={onCancel} role="presentation">
      <div
        className={styles.narratorModal}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`Choose a narrator for ${book.title}`}
      >
        <header className={styles.narratorHead}>
          {cover && <img className={styles.narratorCover} src={cover} alt="" />}
          <div className={styles.narratorHeadText}>
            <span className={styles.modalEyebrow}>Add to wishlist</span>
            <h2 className={styles.narratorTitle}>{book.title}</h2>
            {book.author_names.length > 0 && (
              <p className={styles.narratorAuthor}>{book.author_names.join(', ')}</p>
            )}
          </div>
        </header>

        <p className={styles.narratorBlurb}>
          Which reading should we go and find? A book is only ever downloaded in one narrator's
          performance — chapters are never mixed between editions.
        </p>

        <div className={styles.narratorOptions}>
          <button
            type="button"
            className={`${styles.narratorOption} ${styles.narratorOptionPrimary}`}
            onClick={() => onChoose('exact')}
          >
            <span className={styles.narratorOptionTitle}>
              {narrator ? `${narrator} only` : 'This edition only'}
            </span>
            <span className={styles.narratorOptionSub}>
              Keeps looking until this exact performance turns up
            </span>
          </button>

          <button type="button" className={styles.narratorOption} onClick={() => onChoose('any')}>
            <span className={styles.narratorOptionTitle}>Any narrator</span>
            <span className={styles.narratorOptionSub}>
              Takes whichever complete edition appears first
            </span>
          </button>
        </div>

        <button type="button" className={styles.narratorCancel} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
