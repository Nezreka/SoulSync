import type { DiscographyBucket } from '../-artist-detail.types';

import { type DiscographyFilterState, liveToggleLabel } from '../-artist-detail.filters';

interface Props {
  filters: DiscographyFilterState;
  onChange: (next: DiscographyFilterState) => void;
  /** The Status group is library-only — a source artist owns nothing. */
  isSourceArtist: boolean;
}

const CATEGORIES: { value: DiscographyBucket; label: string }[] = [
  { value: 'albums', label: 'Albums' },
  { value: 'eps', label: 'EPs' },
  { value: 'singles', label: 'Singles' },
];

const OWNERSHIP: { value: DiscographyFilterState['ownership']; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'owned', label: 'Owned' },
  { value: 'missing', label: 'Missing' },
];

/**
 * The discography filter bar, from #discography-filters in index.html and the
 * delegated handler in initializeDiscographyFilters (library.js:2174).
 *
 * Two different interaction models, and mixing them up is the obvious bug:
 *   - category and content are MULTI-toggles; each button flips independently
 *   - ownership is SINGLE-select; picking one clears its siblings
 */
export function DiscographyFilters({ filters, onChange, isSourceArtist }: Props) {
  const toggleCategory = (value: DiscographyBucket) =>
    onChange({
      ...filters,
      categories: { ...filters.categories, [value]: !filters.categories[value] },
    });

  const toggleContent = (value: keyof DiscographyFilterState['content']) =>
    onChange({ ...filters, content: { ...filters.content, [value]: !filters.content[value] } });

  const selectOwnership = (value: DiscographyFilterState['ownership']) =>
    onChange({ ...filters, ownership: value });

  const cls = (active: boolean) => `discography-filter-btn${active ? ' active' : ''}`;

  return (
    <div className="discography-filters" id="discography-filters">
      <div className="filter-group">
        <span className="filter-label">Show</span>
        {CATEGORIES.map(({ value, label }) => (
          <button
            key={value}
            type="button"
            className={cls(filters.categories[value])}
            data-filter="category"
            data-value={value}
            onClick={() => toggleCategory(value)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="filter-divider" />

      <div className="filter-group">
        <span className="filter-label">Include</span>
        <button
          type="button"
          className={cls(filters.content.live)}
          data-filter="content"
          data-value="live"
          onClick={() => toggleContent('live')}
        >
          {/* Relabelled to "Non-Studio" on MusicBrainz, where the toggle governs
              the broader secondary-type set rather than just live albums. */}
          {liveToggleLabel(filters)}
        </button>
        <button
          type="button"
          className={cls(filters.content.compilations)}
          data-filter="content"
          data-value="compilations"
          onClick={() => toggleContent('compilations')}
        >
          Compilations
        </button>
        <button
          type="button"
          className={cls(filters.content.featured)}
          data-filter="content"
          data-value="featured"
          onClick={() => toggleContent('featured')}
        >
          Featured
        </button>
      </div>

      {/* The vanilla hid this group with CSS via body[data-artist-source]; here
          it is simply not rendered. A source artist has no library, so every
          release is "missing" and the filter is meaningless. */}
      {isSourceArtist ? null : (
        <>
          <div className="filter-divider" />
          <div className="filter-group">
            <span className="filter-label">Status</span>
            {OWNERSHIP.map(({ value, label }) => (
              <button
                key={value}
                type="button"
                className={cls(filters.ownership === value)}
                data-filter="ownership"
                data-value={value}
                onClick={() => selectOwnership(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
