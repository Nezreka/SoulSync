import { useEffect, useState } from 'react';

import type { DownloadTarget } from '../-basic.types';
import type { BasicSearchController } from '../-basic.use-controller';

import { sourceLabel } from '../-basic.api';
import { isYoutubeUrl, useYoutubeLink } from '../-youtube-link';
import { BasicFilters } from './basic-filters';
import { BasicResults } from './basic-results';
import { BasicSearchBar } from './basic-search-bar';
import { BasicWarnings } from './basic-warnings';
import styles from './basic.module.css';
import { DownloadChooser } from './download-chooser';
import { EnrichedModal } from './enriched-modal';
import { ManualModal } from './manual-modal';
import { YoutubeLinkPanel } from './youtube-link-panel';

/** before anything has been searched for */
export const EMPTY_PLACEHOLDER =
  'Search a download source directly, or paste a YouTube link, then pick how each file comes in.';
/** results came back, the filters hide all of them */
export const FILTERED_OUT_PLACEHOLDER = 'Nothing matches these filters.';

/**
 * The basic (download-source file) search panel.
 *
 * `.search-section` + `.active` is what the page stylesheet keys visibility on,
 * only the active one of the two panels is displayed. rendering this without
 * `active` leaves a correct panel that is invisible, which no jsdom test can
 * see because jsdom applies no CSS.
 */
export function BasicSearch({
  controller,
  onDownload,
  active,
}: {
  controller: BasicSearchController;
  /** an as-is download. enriched never leaves this panel, it opens its modal */
  onDownload: (target: DownloadTarget) => void;
  active: boolean;
}) {
  const { state, visible, search, cancel, setFilters, toggleSortOrder, selectSource } = controller;
  const [query, setQuery] = useState('');
  const [target, setTarget] = useState<DownloadTarget | null>(null);
  const [enriched, setEnriched] = useState<DownloadTarget | null>(null);
  const [manual, setManual] = useState<DownloadTarget | null>(null);
  // a pasted youtube link shows that video instead of searching (#2322)
  const link = useYoutubeLink();

  // the handoffs (wishlist "search manually", the global download widget) run
  // a search for a query this input never saw. following state.query keeps the
  // box showing what the results below it are for
  useEffect(() => {
    if (state.query) setQuery(state.query);
  }, [state.query]);

  const current = state.singleSource
    ? state.sources[0]
    : state.sources.find((source) => source.name === state.activeSource);

  return (
    <div
      id="basic-search-section"
      className={`search-section${active ? ' active' : ''} ${styles.panel}`}
    >
      <BasicSearchBar
        query={query}
        searching={state.searching || link.state.status === 'loading'}
        sources={state.sources}
        activeSource={state.activeSource}
        singleSource={state.singleSource}
        onQueryChange={setQuery}
        onSubmit={() => {
          if (isYoutubeUrl(query)) {
            cancel();
            void link.resolve(query.trim());
            return;
          }
          link.clear();
          search(query);
        }}
        onCancel={() => {
          link.clear();
          cancel();
        }}
        onSelectSource={selectSource}
      />

      <BasicFilters
        filters={state.filters}
        visible={state.filtersVisible && !state.searching && link.state.status === 'idle'}
        results={state.results}
        sourceName={current ? sourceLabel(current) : ''}
        onChange={setFilters}
        onToggleOrder={toggleSortOrder}
      />

      {!state.searching && link.state.status === 'idle' && (
        <BasicWarnings warnings={state.warnings} />
      )}

      {link.state.status !== 'idle' ? (
        <YoutubeLinkPanel state={link.state} onDownload={setTarget} />
      ) : (
        <BasicResults
          results={visible}
          // one message at a time: the hint before any search, a filter that
          // hides everything, else whatever the controller says (searching,
          // nothing found, failed, cancelled)
          placeholder={
            state.results.length
              ? FILTERED_OUT_PLACEHOLDER
              : state.query || state.searching
                ? state.status
                : EMPTY_PLACEHOLDER
          }
          onDownload={setTarget}
        />
      )}

      <DownloadChooser
        target={target}
        onClose={() => setTarget(null)}
        // enriched and tag-it-yourself are their own flows in their own modals,
        // as-is goes straight out
        onChoose={(picked, mode) =>
          mode === 'enriched'
            ? setEnriched(picked)
            : mode === 'manual'
              ? setManual(picked)
              : onDownload(picked)
        }
      />
      <EnrichedModal target={enriched} onClose={() => setEnriched(null)} />
      <ManualModal target={manual} onClose={() => setManual(null)} />
    </div>
  );
}
