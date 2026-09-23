import type { BasicSource } from '../-basic.types';

import { sourceLabel } from '../-basic.api';
import styles from './basic.module.css';

/**
 * The one search control: source picker, input, Search (or Cancel while a
 * search runs).
 *
 * the ids are load-bearing. the global download widget and the wishlist hand
 * queries in through #downloads-search-input, and the page tour points at
 * #bs-source-row and .bs-search-bar.
 *
 * the input is disabled mid-search so a second Enter can't stack a search on
 * top of the one running.
 */
export function BasicSearchBar({
  query,
  searching,
  sources,
  activeSource,
  singleSource,
  onQueryChange,
  onSubmit,
  onCancel,
  onSelectSource,
}: {
  query: string;
  searching: boolean;
  sources: BasicSource[];
  activeSource: string | null;
  singleSource: boolean;
  onQueryChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  onSelectSource: (name: string) => void;
}) {
  return (
    <form
      className={`${styles.searchBox} bs-search-bar`}
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        if (!searching) onSubmit();
      }}
    >
      <SourcePicker
        sources={sources}
        activeSource={activeSource}
        singleSource={singleSource}
        onSelect={onSelectSource}
      />
      <input
        className={styles.input}
        type="text"
        id="downloads-search-input"
        placeholder="Search artists, albums, tracks…"
        aria-label="Search download sources"
        autoComplete="off"
        spellCheck={false}
        value={query}
        disabled={searching}
        onChange={(event) => onQueryChange(event.target.value)}
      />
      {searching ? (
        <>
          <span className={styles.spinner} role="status" aria-label="Searching" />
          <button
            id="downloads-cancel-btn"
            className={styles.cancelButton}
            type="button"
            onClick={onCancel}
          >
            Cancel
          </button>
        </>
      ) : (
        <button id="downloads-search-btn" className={styles.goButton} type="submit">
          Search
        </button>
      )}
    </form>
  );
}

/**
 * Which source the search goes to.
 *
 * with one source configured there is nothing to pick, so it's a plain label.
 * with several it's a native select laid invisibly over the pill: the keyboard
 * and screen readers get a real picker, the eye gets the pill.
 */
function SourcePicker({
  sources,
  activeSource,
  singleSource,
  onSelect,
}: {
  sources: BasicSource[];
  activeSource: string | null;
  singleSource: boolean;
  onSelect: (name: string) => void;
}) {
  if (!sources.length) return null;
  const current =
    (singleSource ? sources[0] : sources.find((s) => s.name === activeSource)) ?? sources[0];

  return (
    <div className={styles.source} id="bs-source-row" data-source={current.name}>
      <span className={styles.sourceDot} aria-hidden="true" />
      <span className={styles.sourceName}>{sourceLabel(current)}</span>
      {singleSource ? null : (
        <>
          <svg className={styles.caret} viewBox="0 0 12 12" fill="none" aria-hidden="true">
            <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" strokeWidth="1.6" />
          </svg>
          <select
            aria-label="Search source"
            value={current.name}
            onChange={(event) => onSelect(event.target.value)}
          >
            {sources.map((source) => (
              <option key={source.name} value={source.name}>
                {sourceLabel(source)}
              </option>
            ))}
          </select>
        </>
      )}
    </div>
  );
}
