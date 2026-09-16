import type { AudiobookCategory } from '../-audiobooks.types';

import styles from './audiobooks-page.module.css';
import { useEdgeScroll } from './use-edge-scroll';

interface AudiobookGenreNavProps {
  categories: AudiobookCategory[];
  /** The active genre NAME — ids are not portable between storefronts. */
  active: string;
  onSelect: (name: string) => void;
}

/**
 * Genre pills built from Audible's own category tree.
 *
 * These are the same ids that appear in a product's category ladder, so a genre
 * chip on a detail page and a pill here select the identical shelf. The podcast
 * page had to hardcode a term map because iTunes exposes no such tree.
 *
 * There are two dozen top-level genres, which is always wider than the page, so
 * the strip carries the same arrows the rails do plus a fade on whichever edge
 * has more behind it. A hidden scrollbar with no arrows just reads as a row that
 * got cut off.
 */
export function AudiobookGenreNav({ categories, active, onSelect }: AudiobookGenreNavProps) {
  const { ref, canScrollLeft, canScrollRight, sync, scrollByPage, onWheel } =
    useEdgeScroll<HTMLDivElement>(categories.length);

  if (categories.length === 0) return null;

  return (
    <div
      className={`${styles.genreNavWrap} ${canScrollLeft ? styles.fadeLeft : ''} ${
        canScrollRight ? styles.fadeRight : ''
      }`}
    >
      <button
        type="button"
        className={`${styles.genreArrow} ${styles.genreArrowLeft}`}
        onClick={() => scrollByPage(-1)}
        hidden={!canScrollLeft}
        aria-label="Scroll genres left"
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

      <nav
        className={styles.genreNav}
        ref={ref}
        onScroll={sync}
        onWheel={onWheel}
        aria-label="Audiobook genres"
      >
        <button
          type="button"
          className={`${styles.genrePill} ${!active ? styles.genrePillActive : ''}`}
          onClick={() => onSelect('')}
        >
          For you
        </button>
        {categories.map((category) => (
          <button
            key={category.id}
            type="button"
            className={`${styles.genrePill} ${
              active === category.name ? styles.genrePillActive : ''
            }`}
            onClick={() => onSelect(category.name)}
          >
            {category.name}
          </button>
        ))}
      </nav>

      <button
        type="button"
        className={`${styles.genreArrow} ${styles.genreArrowRight}`}
        onClick={() => scrollByPage(1)}
        hidden={!canScrollRight}
        aria-label="Scroll genres right"
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
  );
}
