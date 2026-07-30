import { useEffect, useState } from 'react';

import type { BasicSearchController } from '../-basic.use-controller';
import type { BasicResultActions } from './basic-results';

import { BasicFilters } from './basic-filters';
import { BasicResults } from './basic-results';
import { BasicSearchBar, BasicStatusBar } from './basic-search-bar';
import { BasicSourceRow } from './basic-source-row';

/** The static markup's line, before anything has been searched for. */
export const EMPTY_PLACEHOLDER = 'Enter a search term to get started.';
/** displayDownloadsResults' line, after a search that found nothing. */
export const NO_RESULTS_PLACEHOLDER = 'No search results found.';

/**
 * The basic (download-source file) search panel.
 *
 * `.search-section` + `.active` is what the stylesheet keys visibility on, and
 * only the active one of the two panels is displayed — the same class the
 * vanilla toggled when the source picker switched modes. Rendering this without
 * `active` leaves a fully-correct panel that is invisible, which no jsdom test
 * can see because jsdom applies no CSS.
 */
export function BasicSearch({
  controller,
  actions,
  active,
}: {
  controller: BasicSearchController;
  actions: BasicResultActions;
  active: boolean;
}) {
  const { state, visible, search, cancel, setFilters, toggleSortOrder, selectSource } = controller;
  const [query, setQuery] = useState('');

  // The handoffs (wishlist "search manually", the global download widget) run
  // a search for a query this input never saw. Following state.query keeps the
  // box showing what the results below it are for.
  useEffect(() => {
    if (state.query) setQuery(state.query);
  }, [state.query]);

  return (
    <div id="basic-search-section" className={`search-section${active ? ' active' : ''}`}>
      <BasicSourceRow
        sources={state.sources}
        activeSource={state.activeSource}
        singleSource={state.singleSource}
        onSelect={selectSource}
      />

      <BasicSearchBar
        query={query}
        searching={state.searching}
        onQueryChange={setQuery}
        onSubmit={() => search(query)}
        onCancel={cancel}
      />

      <BasicStatusBar status={state.status} searching={state.searching} />

      <BasicFilters
        filters={state.filters}
        visible={state.filtersVisible}
        onChange={setFilters}
        onToggleOrder={toggleSortOrder}
      />

      <BasicResults
        results={visible}
        actions={actions}
        placeholder={state.query ? NO_RESULTS_PLACEHOLDER : EMPTY_PLACEHOLDER}
      />
    </div>
  );
}
