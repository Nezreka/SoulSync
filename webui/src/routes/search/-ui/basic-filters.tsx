import type { BasicResult, FilterState, FormatFilter, SortKey, TypeFilter } from '../-basic.types';

import { FORMAT_FILTERS, SORT_OPTIONS, TYPE_FILTERS } from '../-basic.types';
import styles from './basic.module.css';

/**
 * The toolbar over the results: how many came back, then type, format and
 * sort.
 *
 * hidden until a search finds something, then it stays for the session.
 * filtering a list down to nothing must not take the controls away with it,
 * that would strand the user with no way to undo the filter.
 *
 * the arrow flips the sort. down is each key's natural order (best first for
 * numbers, A-Z for text), up reverses it.
 */
export function BasicFilters({
  filters,
  visible,
  results,
  sourceName,
  onChange,
  onToggleOrder,
}: {
  filters: FilterState;
  visible: boolean;
  /** everything the search returned, before filtering */
  results: BasicResult[];
  sourceName: string;
  onChange: (patch: Partial<FilterState>) => void;
  onToggleOrder: () => void;
}) {
  if (!visible) return null;
  const albums = results.filter((result) => result.result_type === 'album').length;
  const tracks = results.length - albums;
  const format = FORMAT_FILTERS.find((option) => option.value === filters.format);
  const sort = SORT_OPTIONS.find((option) => option.value === filters.sort);

  return (
    <div id="filters-container" className={styles.toolbar}>
      <div className={styles.count}>
        <b>
          {results.length} {results.length === 1 ? 'result' : 'results'}
        </b>
        {sourceName ? ` from ${sourceName}` : ''} · {albums} {albums === 1 ? 'album' : 'albums'},{' '}
        {tracks} {tracks === 1 ? 'track' : 'tracks'}
      </div>

      <div className={styles.segmented} role="group" aria-label="Type">
        {TYPE_FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={filters.type === option.value}
            onClick={() => onChange({ type: option.value as TypeFilter })}
          >
            {option.label}
          </button>
        ))}
      </div>

      <label className={styles.menu}>
        Format <b>{format && format.value !== 'all' ? format.label : 'Any'}</b>
        <Caret />
        <select
          aria-label="Format"
          value={filters.format}
          onChange={(event) => onChange({ format: event.target.value as FormatFilter })}
        >
          {FORMAT_FILTERS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.value === 'all' ? 'Any' : option.label}
            </option>
          ))}
        </select>
      </label>

      <label className={styles.menu}>
        Sort <b>{sort?.label ?? ''}</b>
        <Caret />
        <select
          aria-label="Sort by"
          value={filters.sort}
          onChange={(event) => onChange({ sort: event.target.value as SortKey })}
        >
          {SORT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <button
        id="sort-order-btn"
        className={styles.orderButton}
        type="button"
        data-reversed={filters.reversed}
        aria-label={filters.reversed ? 'Reversed order' : 'Default order'}
        title={filters.reversed ? 'Reversed order' : 'Default order'}
        onClick={onToggleOrder}
      >
        <svg viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path
            d="M7 2.5v9M3.5 8 7 11.5 10.5 8"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  );
}

function Caret() {
  return (
    <svg className={styles.caret} viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}
