import { useEffect, useRef, useState } from 'react';

import type { AudiobookSearchType } from '../-audiobooks.types';

import styles from './audiobooks-page.module.css';

interface AudiobookSearchBarProps {
  query: string;
  searchType: AudiobookSearchType;
  busy: boolean;
  onQueryChange: (value: string) => void;
  onSearchTypeChange: (value: AudiobookSearchType) => void;
  onSubmit: () => void;
  onClear: () => void;
}

/**
 * Search with the mode surfaced as a segmented control rather than buried in a
 * dropdown.
 *
 * Narrator is a first-class mode because it is the search no other audiobook
 * source can answer, and a listener who has found a narrator they like will
 * browse by that name more often than by anything else.
 */
const MODES: { value: AudiobookSearchType; label: string; hint: string }[] = [
  { value: 'keywords', label: 'All', hint: 'Search everything' },
  { value: 'title', label: 'Title', hint: 'Match the book title' },
  { value: 'author', label: 'Author', hint: 'Match the author' },
  { value: 'narrator', label: 'Narrator', hint: 'Match the narrator' },
];

export function AudiobookSearchBar({
  query,
  searchType,
  busy,
  onQueryChange,
  onSearchTypeChange,
  onSubmit,
  onClear,
}: AudiobookSearchBarProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [focused, setFocused] = useState(false);

  // "/" focuses search, the way every catalogue app the user already has does.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
      if (event.key === '/' && !typing) {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  return (
    <div className={`${styles.searchBar} ${focused ? styles.searchBarFocused : ''}`}>
      <form
        className={styles.searchInputWrap}
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <svg className={styles.searchIcon} viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M16.5 16.5L21 21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
        <input
          ref={inputRef}
          className={styles.searchInput}
          value={query}
          placeholder="Search audiobooks, authors, narrators…"
          onChange={(event) => onQueryChange(event.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          aria-label="Search audiobooks"
        />
        {query && (
          <button
            type="button"
            className={styles.searchClear}
            onClick={onClear}
            aria-label="Clear search"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M6 6l12 12M18 6L6 18"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        )}
        <button type="submit" className={styles.searchSubmit} disabled={busy || !query.trim()}>
          {busy ? 'Searching…' : 'Search'}
        </button>
      </form>

      <div className={styles.searchModes} role="group" aria-label="Search mode">
        {MODES.map((mode) => (
          <button
            key={mode.value}
            type="button"
            title={mode.hint}
            className={`${styles.searchMode} ${searchType === mode.value ? styles.searchModeActive : ''}`}
            onClick={() => onSearchTypeChange(mode.value)}
            aria-pressed={searchType === mode.value}
          >
            {mode.label}
          </button>
        ))}
      </div>
    </div>
  );
}
