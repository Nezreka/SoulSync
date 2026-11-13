import {
  tagChangeAfter,
  tagChangeBefore,
  tagChangeLabel,
  type TagChange,
} from '../-tools.finding-copy';

/**
 * Tags side by side: the value on the left goes, the one on the right stays.
 *
 * A re-tag finding used to say "differs from the library, which has
 * Electronic" and never what the file itself held, so there was nothing to
 * judge the change by.
 */
export function TagChangeTable({
  rows: allRows,
  beforeLabel,
  afterLabel,
}: {
  rows: TagChange[];
  beforeLabel: string;
  afterLabel: string;
}) {
  // a row with nothing on either side says nothing
  const rows = allRows.filter((row) => row.before || row.after);
  if (!rows.length) return null;
  return (
    <div className="tag-changes" role="table" aria-label="Tag changes">
      <div className="tag-changes-head" role="row">
        <span role="columnheader">Tag</span>
        <span role="columnheader">{beforeLabel}</span>
        <span aria-hidden="true" />
        <span role="columnheader">{afterLabel}</span>
      </div>
      {rows.map((row, index) => {
        const before = tagChangeBefore(row);
        return (
          <div className="tag-changes-row" role="row" key={`${row.key}-${index}`}>
            <span className="tag-changes-tag" role="cell">
              {tagChangeLabel(row)}
            </span>
            <span className={`tag-changes-before${before ? '' : ' empty'}`} role="cell">
              {before || 'Empty'}
            </span>
            <span className="tag-changes-arrow" aria-hidden="true">
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </span>
            <span className="tag-changes-after" role="cell">
              {tagChangeAfter(row) || 'Empty'}
              {row.manual ? <span className="tag-changes-note">set by hand</span> : null}
            </span>
          </div>
        );
      })}
    </div>
  );
}
