import type { LabelFilter, LabelSort } from '../-label-detail.types';

/**
 * Filter pills with live counts, and the sort select.
 *
 * The pill ORDER is the vanilla's — All, Missing, Owned — and worth keeping:
 * this page is an acquisition surface, so "what am I missing" sits next to
 * "everything" rather than after the things you already have.
 */
export function LabelToolbar({
  filter,
  sort,
  counts,
  onFilter,
  onSort,
}: {
  filter: LabelFilter;
  sort: LabelSort;
  counts: { all: number; owned: number; missing: number };
  onFilter: (next: LabelFilter) => void;
  onSort: (next: LabelSort) => void;
}) {
  const pill = (value: LabelFilter, label: string, count: number, id: string) => (
    <button
      type="button"
      data-lf={value}
      className={filter === value ? 'active' : ''}
      onClick={() => onFilter(value)}
    >
      {label} <span id={id}>{count}</span>
    </button>
  );

  return (
    <div className="label-detail-toolbar" id="label-detail-toolbar">
      <div className="label-detail-filters" id="label-detail-filters">
        {pill('all', 'All', counts.all, 'lf-count-all')}
        {pill('missing', 'Missing', counts.missing, 'lf-count-missing')}
        {pill('owned', 'Owned', counts.owned, 'lf-count-owned')}
      </div>
      <select
        className="label-detail-sort"
        id="label-detail-sort"
        value={sort}
        onChange={(event) => onSort(event.currentTarget.value as LabelSort)}
      >
        <option value="newest">Newest first</option>
        <option value="oldest">Oldest first</option>
        <option value="artist">Artist A–Z</option>
      </select>
    </div>
  );
}
