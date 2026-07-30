import type { FilterState, FormatFilter, SortKey, TypeFilter } from '../-basic.types';

import { FORMAT_FILTERS, SORT_OPTIONS, TYPE_FILTERS } from '../-basic.types';

/**
 * The type / format / sort pill rows.
 *
 * Hidden until a search finds something, then latched on for the session — see
 * `filtersVisible` on the controller for why they must not disappear again.
 *
 * The arrow is a direction TOGGLE, not a sort key: it carries no
 * `data-filter-type`, so it never joins the active-pill group. ↓ is each key's
 * natural order (best first for numbers, A-Z for text) and ↑ reverses it. In
 * the vanilla the arrow said ↓ while the list was reversed; here the glyph and
 * the order agree.
 */
export function BasicFilters({
  filters,
  visible,
  onChange,
  onToggleOrder,
}: {
  filters: FilterState;
  visible: boolean;
  onChange: (patch: Partial<FilterState>) => void;
  onToggleOrder: () => void;
}) {
  return (
    <div id="filters-container" className={`bs-filters${visible ? '' : ' hidden'}`}>
      <div className="bs-filter-group">
        <span className="bs-filter-label">Type</span>
        {TYPE_FILTERS.map((option) => (
          <Pill
            key={option.value}
            label={option.label}
            filterType="type"
            value={option.value}
            active={filters.type === option.value}
            onClick={() => onChange({ type: option.value as TypeFilter })}
          />
        ))}
      </div>

      <div className="bs-filter-group">
        <span className="bs-filter-label">Format</span>
        {FORMAT_FILTERS.map((option) => (
          <Pill
            key={option.value}
            label={option.label}
            filterType="format"
            value={option.value}
            active={filters.format === option.value}
            onClick={() => onChange({ format: option.value as FormatFilter })}
          />
        ))}
      </div>

      <div className="bs-filter-group">
        <span className="bs-filter-label">Sort</span>
        <button
          id="sort-order-btn"
          className="filter-btn bs-filter-pill sort-order-btn"
          type="button"
          data-order={filters.reversed ? 'asc' : 'desc'}
          title={filters.reversed ? 'Reversed order' : 'Default order'}
          onClick={onToggleOrder}
        >
          {filters.reversed ? '↑' : '↓'}
        </button>
        {SORT_OPTIONS.map((option) => (
          <Pill
            key={option.value}
            label={option.label}
            filterType="sort"
            value={option.value}
            active={filters.sort === option.value}
            onClick={() => onChange({ sort: option.value as SortKey })}
          />
        ))}
      </div>
    </div>
  );
}

function Pill({
  label,
  filterType,
  value,
  active,
  onClick,
}: {
  label: string;
  filterType: string;
  value: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`filter-btn bs-filter-pill${active ? ' active' : ''}`}
      data-filter-type={filterType}
      data-value={value}
      aria-pressed={active}
      onClick={onClick}
    >
      {label}
    </button>
  );
}
