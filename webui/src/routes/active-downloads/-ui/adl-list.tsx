import type { AdlDownload, AdlFilter } from '../-adl.types';

import { batchColorIndex } from '../-adl.helpers';
import { groupBySection } from '../-adl.use-downloads';
import { AdlRow } from './adl-row';

/** The line shown when there is nothing to list. */
export const ADL_EMPTY_TEXT =
  'No downloads yet. Start one from Search, Sync, Discover, or Library.';

/**
 * The "showing one batch" strip above the list.
 *
 * Carries the batch's colour dot so it reads as the same batch as the card and
 * the row stripes.
 */
export function BatchFilterBanner({
  batchId,
  batchName,
  onClear,
}: {
  batchId: string;
  batchName: string;
  onClear: () => void;
}) {
  const colorIdx = batchColorIndex(batchId);
  return (
    <div className="adl-batch-filter-banner" id="adl-batch-filter-banner">
      {colorIdx >= 0 ? (
        <span
          className="adl-filter-banner-dot"
          style={{ background: `rgba(var(--batch-color-${colorIdx}),0.7)` }}
        />
      ) : null}
      Showing: <strong>{batchName}</strong>{' '}
      <button type="button" className="adl-filter-banner-clear" onClick={onClear}>
        Clear filter
      </button>
    </div>
  );
}

export interface AdlListProps {
  rows: AdlDownload[];
  filter: AdlFilter;
  onCancel: (dl: AdlDownload) => void;
}

/**
 * The download list, grouped into its four sections.
 *
 * Section headers appear ONLY under the `all` filter — under a specific filter
 * every row is already that kind, so a header would just repeat the pill.
 */
export function AdlList({ rows, filter, onCancel }: AdlListProps) {
  if (rows.length === 0) {
    return (
      <div className="adl-list" id="adl-list">
        <div className="adl-empty" id="adl-empty">
          {ADL_EMPTY_TEXT}
        </div>
      </div>
    );
  }

  const sections = groupBySection(rows).filter((section) => section.items.length > 0);

  return (
    <div className="adl-list" id="adl-list">
      <div className="adl-empty" id="adl-empty" style={{ display: 'none' }}>
        {ADL_EMPTY_TEXT}
      </div>
      {sections.map((section) => (
        <div key={section.key}>
          {filter === 'all' ? (
            <div className="adl-section-header">
              {section.label} ({section.items.length})
            </div>
          ) : null}
          {section.items.map((dl) => (
            <AdlRow key={dl.task_id} dl={dl} onCancel={onCancel} />
          ))}
        </div>
      ))}
    </div>
  );
}
