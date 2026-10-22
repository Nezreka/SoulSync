import { batchColorIndex } from '../-adl.helpers';

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
