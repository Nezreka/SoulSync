import type { BasicSource } from '../-basic.types';

import { sourceLabel } from '../-basic.api';

/**
 * The download-source chip row.
 *
 * In single-source mode the chips are still rendered but disabled — the user
 * needs to see WHICH source they are searching even when there is nothing to
 * choose (downloads.js:4306-4310).
 */
export function BasicSourceRow({
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
  if (!sources.length) return <div className="bs-source-row" id="bs-source-row" role="tablist" />;

  return (
    <div className="bs-source-row" id="bs-source-row" aria-label="Search source" role="tablist">
      {sources.map((source, index) => {
        // In single-source mode nothing is "active" in state (activeSource is
        // null so the request omits it), but the one chip still reads as the
        // current one — the vanilla marked index 0 active before deciding.
        const active = singleSource ? index === 0 : source.name === activeSource;
        return (
          <button
            key={source.name}
            type="button"
            className={`bs-source-chip${active ? ' active' : ''}${singleSource ? ' single' : ''}`}
            data-source={source.name}
            role="tab"
            aria-selected={active}
            title={source.display_name}
            disabled={singleSource}
            onClick={() => onSelect(source.name)}
          >
            <span className="bs-source-label">{sourceLabel(source)}</span>
          </button>
        );
      })}
    </div>
  );
}
