import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { BasicResult, BasicSource, FilterState } from './-basic.types';

import { fetchBasicSources, isSingleSourceMode, performBasicSearch } from './-basic.api';
import { applyFiltersAndSort } from './-basic.helpers';
import { DEFAULT_FILTERS } from './-basic.types';

/** What the status bar says before anything has been searched for. */
export const IDLE_STATUS = 'Enter an artist, album, or track name to search';

export interface BasicSearchState {
  /** The query the CURRENT results belong to — not what is in the input. */
  query: string;
  /** Everything the server returned, before filtering. */
  results: BasicResult[];
  filters: FilterState;
  status: string;
  searching: boolean;
  sources: BasicSource[];
  /**
   * The source to search, or null to let the server choose.
   *
   * Null is meaningful rather than a missing value: in single-source mode the
   * vanilla explicitly sent no `source` at all and let the orchestrator route
   * (downloads.js:4329), so null is passed through to the request untouched.
   */
  activeSource: string | null;
  /** One source (or a non-hybrid mode) — the chip row is a label, not a picker. */
  singleSource: boolean;
  /**
   * Latches true on the first search that finds anything.
   *
   * The vanilla only ever removed `hidden` from #filters-container and never
   * put it back, so once the pills appear they stay for the session. Filtering
   * a list down to nothing must not take the pills away with it — that would
   * strand the user with no way to undo the filter.
   */
  filtersVisible: boolean;
}

export interface BasicSearchController {
  state: BasicSearchState;
  /** Filtered and sorted — what the page renders, and what indices refer to. */
  visible: BasicResult[];
  search: (query: string) => void;
  cancel: () => void;
  setFilters: (patch: Partial<FilterState>) => void;
  toggleSortOrder: () => void;
  selectSource: (name: string) => void;
}

const INITIAL: BasicSearchState = {
  query: '',
  results: [],
  filters: DEFAULT_FILTERS,
  status: IDLE_STATUS,
  searching: false,
  sources: [],
  activeSource: null,
  singleSource: true,
  filtersVisible: false,
};

/**
 * What survives leaving the page.
 *
 * The vanilla panel was a persistent DOM node — the React page borrowed it and
 * put it back rather than owning it, so it was never destroyed and its rendered
 * results were still on screen when you came back. Owning the markup means an
 * unmount really does throw everything away, so the cache has to live out here
 * to keep that behaviour. The enhanced half does the same thing for the same
 * reason.
 *
 * Deliberately NOT the whole state: `searching` is about a request that the
 * unmount aborted, and `sources` is re-fetched on mount.
 */
interface PersistedBasicSearch {
  query: string;
  results: BasicResult[];
  filters: FilterState;
  filtersVisible: boolean;
  status: string;
  activeSource: string | null;
}

let persisted: PersistedBasicSearch | null = null;

/** Test seam — a module-level cache would otherwise leak between tests. */
export function resetPersistedBasicSearch() {
  persisted = null;
}

/** `✨ Found 12 results • 3 albums, 9 singles` */
function foundStatus(results: BasicResult[]): string {
  const albums = results.filter((result) => result.result_type === 'album').length;
  const singles = results.length - albums;
  return `✨ Found ${results.length} results • ${albums} albums, ${singles} singles`;
}

/**
 * Basic search's brain, ported from `performDownloadsSearch`
 * (downloads.js:4341) plus the filter handlers in wishlist-tools.js.
 *
 * One request at a time, by design — this is a file search against a P2P
 * network, and the vanilla kept a single `searchAbortController` for it. A new
 * search or a Cancel click aborts the one in flight. The generation counter
 * does the rest: an abort's rejection can arrive after the next search has
 * already started, and without it that late rejection would write "Search was
 * cancelled." over a search that is happily running.
 */
export function useBasicSearchController(): BasicSearchController {
  const [state, setState] = useState<BasicSearchState>(() =>
    persisted ? { ...INITIAL, ...persisted } : INITIAL,
  );

  // Mirrored on every change rather than on unmount: an unmount hook would not
  // run if the tab were closed mid-search, and this costs nothing.
  persisted = {
    query: state.query,
    results: state.results,
    filters: state.filters,
    filtersVisible: state.filtersVisible,
    status: state.status,
    activeSource: state.activeSource,
  };

  const abortRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    let live = true;
    void (async () => {
      const response = await fetchBasicSources();
      if (!live || !response.sources.length) return;
      const single = isSingleSourceMode(response);
      setState((prev) => {
        // A source the user picked before navigating away wins over the
        // default — but only while it is still in the chain, since the
        // configuration can have changed since.
        const restored =
          prev.activeSource && response.sources.some((s) => s.name === prev.activeSource)
            ? prev.activeSource
            : null;
        return {
          ...prev,
          sources: response.sources,
          singleSource: single,
          // The default is the first source in the chain. In single-source mode
          // it stays null so the request omits `source` entirely.
          activeSource: single ? null : (restored ?? response.sources[0]?.name ?? null),
        };
      });
    })();
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  const runSearch = useCallback(async (query: string, source: string | null) => {
    const generation = ++generationRef.current;
    const current = () => generationRef.current === generation;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState((prev) => ({
      ...prev,
      query,
      results: [],
      searching: true,
      status: `Searching for '${query}'...`,
    }));

    try {
      const results = await performBasicSearch(query, source, controller.signal);
      // Honest about its weight: a new search aborts the previous request
      // first, and that abort cancels the body read, so a superseded request
      // reaches the CATCH rather than here. A mutant deleting this line
      // survives the suite, and I could not construct a case that reaches it —
      // the guard stays as defence in depth against a future caller that
      // supersedes a search without aborting it, but no test proves it and
      // pretending otherwise would be worse than saying so. The same guard in
      // the catch below IS proven (see the cancel tests).
      if (!current()) return;

      setState((prev) => ({
        ...prev,
        results,
        // Every search resets the pills — `resetFilters()` ran before the
        // results were rendered, so a format filter from the last search never
        // silently hides the new one's results.
        filters: DEFAULT_FILTERS,
        status: results.length ? foundStatus(results) : `No results found for '${query}'`,
        filtersVisible: prev.filtersVisible || results.length > 0,
      }));

      if (results.length) {
        window.showToast?.(`Found ${results.length} results`, 'success');
      } else {
        window.showToast?.('No results found', 'error');
      }
    } catch (error) {
      if (!current()) return;

      if ((error as Error)?.name === 'AbortError') {
        setState((prev) => ({ ...prev, results: [], status: 'Search was cancelled.' }));
        window.showToast?.('Search cancelled', 'info');
      } else {
        setState((prev) => ({
          ...prev,
          status: `Search failed: ${(error as Error)?.message ?? 'unknown error'}`,
        }));
        window.showToast?.('Search failed', 'error');
      }
    } finally {
      if (current()) {
        abortRef.current = null;
        setState((prev) => ({ ...prev, searching: false }));
      }
    }
  }, []);

  const search = useCallback(
    (query: string) => {
      const trimmed = query.trim();
      if (!trimmed) {
        window.showToast?.('Please enter a search term', 'error');
        return;
      }
      void runSearch(trimmed, stateRef.current.activeSource);
    },
    [runSearch],
  );

  /**
   * Abort the search in flight.
   *
   * The status and the toast are the abort handler's job, not this one's —
   * aborting rejects the request, which lands in the catch. Writing them here
   * as well would show the user two cancellations for one click.
   */
  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const setFilters = useCallback((patch: Partial<FilterState>) => {
    setState((prev) => ({ ...prev, filters: { ...prev.filters, ...patch } }));
  }, []);

  const toggleSortOrder = useCallback(() => {
    setState((prev) => ({
      ...prev,
      filters: { ...prev.filters, reversed: !prev.filters.reversed },
    }));
  }, []);

  /**
   * Switch source, and re-run the query if there is something on screen.
   *
   * The re-run condition is the vanilla's (downloads.js:4319): a query AND
   * existing results. Switching source before searching anything just arms the
   * picker — it must not fire a search the user never asked for.
   */
  const selectSource = useCallback(
    (name: string) => {
      const previous = stateRef.current;
      if (previous.singleSource || name === previous.activeSource) return;

      setState((prev) => ({ ...prev, activeSource: name }));
      if (previous.query && previous.results.length) void runSearch(previous.query, name);
    },
    [runSearch],
  );

  const visible = useMemo(
    () => applyFiltersAndSort(state.results, state.filters, state.query),
    [state.results, state.filters, state.query],
  );

  /**
   * Publish what is on screen for the vanilla matched-download modal.
   *
   * `skipMatching` and the three `matchedDownload*` handlers in
   * wishlist-tools.js read `window.currentSearchResults` — by INDEX, and in one
   * case by `indexOf` on the object — so it has to be the same array the page
   * renders, in the same order, holding the same object references. This is the
   * one piece of basic search that cannot move into React until that modal does.
   */
  useEffect(() => {
    window.currentSearchResults = visible;
  }, [visible]);

  return { state, visible, search, cancel, setFilters, toggleSortOrder, selectSource };
}
