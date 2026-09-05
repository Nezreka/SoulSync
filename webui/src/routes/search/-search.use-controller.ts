import { useCallback, useEffect, useRef, useState } from 'react';

import type { IdLookupResponse } from './-search.api';
import type { SearchSource, SourceResults } from './-search.types';

import {
  fetchConfigStatus,
  fetchEnhancedSearch,
  fetchMetadataStatus,
  streamVideoSearch,
} from './-search.api';
import {
  canSelectSource,
  emptySourceResults,
  knownDbArtists,
  fallbackFor,
  pickerSource,
  resolveInitialSource,
  sourceResultsFromResponse,
} from './-search.helpers';
import { SOURCE_ORDER } from './-search.types';

export interface SearchControllerState {
  query: string;
  activeSource: string;
  /** Per-(query, source) cache. Wiped wholesale when the query changes. */
  sources: Record<string, SourceResults>;
  /** requested source → what the server actually served. */
  fallbacks: Record<string, string>;
  loadingSources: ReadonlySet<string>;
  configuredSources: Record<string, boolean>;
  enabledExperimental: ReadonlySet<string>;
  /**
   * Has the user clicked a source themselves?
   *
   * The init sequence resolves a default from /status and config-status, and
   * both land asynchronously. Without this flag a click made while they were in
   * flight would be overwritten a moment later.
   */
  userPickedSource: boolean;
}

export interface SearchController {
  state: SearchControllerState;
  submitQuery: (query: string) => void;
  setActiveSource: (source: string) => void;
  /** Adopt a query without searching for it — the widget handoff. */
  syncQuery: (query: string) => void;
  /**
   * The ID-lookup seam.
   *
   * The vanilla's submitIdLookup reached in and mutated controller state
   * directly — adopting the resolved source and seeding its cache. Immutable
   * state needs that as an explicit entry point rather than a reach-in.
   */
  seedFromIdLookup: (raw: string, data: IdLookupResponse) => void;
}

/** Every source starts optimistically usable, so no flash of dimmed icons. */
function optimisticConfigured(): Record<string, boolean> {
  const configured: Record<string, boolean> = {};
  for (const source of SOURCE_ORDER) configured[source] = true;
  return configured;
}

/**
 * The source picker's brain, ported from createSearchController.
 *
 * Two pieces of machinery matter more than they look:
 *
 * **Per-source request tokens, not one global sequence.** Each fetch stamps a
 * monotonic id into `tokens[source]`, and a settle bails if the id for THAT
 * source has moved on. The vanilla's comment spells out why a single token is
 * wrong: switching Spotify → Deezer aborts Spotify's fetch, and Spotify's catch
 * still needs to clear 'spotify' from loadingSources — which Deezer's request
 * never touched. Per-source ownership is what keeps a spinner from sticking.
 *
 * **Tokens are deleted, not just re-stamped, on a query change.** A settle that
 * lands after the query was cleared would otherwise still match its token and
 * write stale results into the freshly-emptied cache.
 */
/**
 * What survives leaving the page.
 *
 * The vanilla page is never torn down, so its controller keeps the last query
 * and its per-source cache; `_searchPageRestoreOnEnter` (search.js:226-234)
 * exists purely to re-render them on the way back, and its comment names the
 * problem it solves: "results vanish on navigate-back". A React route unmounts,
 * which would take the cache with it — so the cache lives out here instead.
 *
 * Deliberately NOT the whole state: loadingSources and configuredSources are
 * about a live page and would be lies by the time anyone came back.
 */
interface PersistedSearch {
  query: string;
  activeSource: string;
  sources: Record<string, SourceResults>;
  fallbacks: Record<string, string>;
  userPickedSource: boolean;
}

let persisted: PersistedSearch | null = null;

/** Test seam — a module-level cache would otherwise leak between tests. */
export function resetPersistedSearch() {
  persisted = null;
}

/**
 * The query the page came back to, for seeding the input.
 *
 * Restoring the cache without this leaves the results on screen above an EMPTY
 * search box — which reads as a bug even though the results are correct.
 */
export function getPersistedQuery(): string {
  return persisted?.query ?? '';
}

export function useSearchController({
  onSoulseekSelected,
  onUnconfiguredSource,
  initialSource = 'spotify',
}: {
  onSoulseekSelected?: (query: string) => void;
  /** Called instead of switching when a source has no credentials. */
  onUnconfiguredSource?: (source: string) => void;
  initialSource?: string;
} = {}): SearchController {
  const [state, setState] = useState<SearchControllerState>(() => ({
    query: persisted?.query ?? '',
    activeSource: persisted?.activeSource ?? pickerSource(initialSource),
    sources: persisted?.sources ?? {},
    fallbacks: persisted?.fallbacks ?? {},
    loadingSources: new Set<string>(),
    configuredSources: optimisticConfigured(),
    enabledExperimental: new Set<string>(),
    userPickedSource: persisted?.userPickedSource ?? false,
  }));

  // Mirrored on every change rather than on unmount: an unmount hook would not
  // run if the tab were closed mid-search, and this costs nothing.
  persisted = {
    query: state.query,
    activeSource: state.activeSource,
    sources: state.sources,
    fallbacks: state.fallbacks,
    userPickedSource: state.userPickedSource,
  };

  const tokensRef = useRef<Record<string, number>>(Object.create(null));
  const seqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const soulseekRef = useRef(onSoulseekSelected);
  soulseekRef.current = onSoulseekSelected;
  const unconfiguredRef = useRef(onUnconfiguredSource);
  unconfiguredRef.current = onUnconfiguredSource;

  /**
   * The init sequence, in the vanilla's order (shared-helpers.js:355-397).
   *
   * /status first — it names the user's primary source AND carries the
   * experimental flags, so a config-status failure still leaves opted-in
   * experimental sources visible. Then config-status, which is the fresher read
   * of both. Only then is the active source resolvable: it depends on which
   * sources are visible and which have credentials.
   */
  useEffect(() => {
    let live = true;
    void (async () => {
      const status = await fetchMetadataStatus();
      if (!live) return;
      const config = await fetchConfigStatus();
      if (!live) return;

      const enabledExperimental = config.enabledExperimental.size
        ? config.enabledExperimental
        : status.enabledExperimental;
      const configured = Object.keys(config.configured).length
        ? config.configured
        : optimisticConfigured();

      setState((prev) => ({
        ...prev,
        configuredSources: { ...prev.configuredSources, ...configured },
        enabledExperimental,
        // Only adopted while the user has not picked anything themselves; their
        // click always outranks the configured default.
        activeSource: prev.userPickedSource
          ? prev.activeSource
          : resolveInitialSource({
              statusSource: status.source || initialSource,
              enabledExperimental,
              configured,
            }),
      }));
    })();
    return () => {
      live = false;
    };
  }, [initialSource]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const fetchSource = useCallback(async (source: string, query: string) => {
    if (!query) return;

    const requestId = ++seqRef.current;
    tokensRef.current[source] = requestId;

    setState((prev) => {
      const loading = new Set(prev.loadingSources);
      loading.add(source);
      return { ...prev, loadingSources: loading };
    });

    // One controller across sources: switching source abandons the previous
    // fetch, which is the point — its results are for an icon you left.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const stillCurrent = () => tokensRef.current[source] === requestId;

    try {
      if (source === 'youtube_videos') {
        await streamVideoSearch(
          query,
          (videos) => {
            // Re-checked per chunk: a long stream can outlive its query.
            if (!stillCurrent()) return;
            setState((prev) => ({
              ...prev,
              sources: {
                ...prev.sources,
                [source]: {
                  ...emptySourceResults(),
                  // iss29-B04a: "In Your Library" is a LOCAL result — it comes
                  // from the catalogue, not from the provider being viewed — so
                  // it is the same for every source of the same query. The
                  // video path builds its results from scratch and so dropped
                  // it, and the whole section vanished for as long as the
                  // YouTube icon stayed active. Carry over whatever a sibling
                  // source already resolved for this query.
                  db_artists: knownDbArtists(prev.sources),
                  videos,
                },
              },
            }));
          },
          controller.signal,
        );
      } else {
        const data = await fetchEnhancedSearch(query, source, controller.signal);
        if (!stillCurrent()) return;
        const served = fallbackFor(source, data);
        setState((prev) => ({
          ...prev,
          sources: { ...prev.sources, [source]: sourceResultsFromResponse(data) },
          fallbacks: served ? { ...prev.fallbacks, [source]: served } : prev.fallbacks,
        }));
      }
    } catch {
      // Swallowed: an abort is the normal case here, and a genuine failure
      // shows as an empty result rather than an error page — the vanilla's
      // behaviour.
    } finally {
      // This source's own spinner, cleared even when a DIFFERENT source
      // superseded it. That is the whole reason the tokens are per-source.
      if (stillCurrent()) {
        setState((prev) => {
          const loading = new Set(prev.loadingSources);
          loading.delete(source);
          return { ...prev, loadingSources: loading };
        });
      }
    }
  }, []);

  /**
   * Adopt a query WITHOUT searching for it.
   *
   * This is the global download widget's handoff. It writes the basic-search
   * input itself and then clicks the Soulseek icon; all it needs from here is
   * for the controller to agree what the query is, so that the click hands off
   * the right one. The vanilla did it by assigning `state.query` directly
   * (downloads.js:5729-5731) — a plain assignment, no fetch and no callback.
   *
   * Searching here instead would run the basic search THREE times for one
   * handoff: once from this call, once from the icon click, and once more when
   * the debounce caught up.
   *
   * The cache is cleared when the query actually differs, which the vanilla's
   * raw assignment did not do — leaving it would let a later search for this
   * query serve results that belong to the previous one.
   */
  const syncQuery = useCallback((query: string) => {
    const previous = stateRef.current;
    if (query === previous.query) return;
    for (const key of Object.keys(tokensRef.current)) delete tokensRef.current[key];
    abortRef.current?.abort();
    abortRef.current = null;
    setState((prev) => ({
      ...prev,
      query,
      sources: {},
      fallbacks: {},
      loadingSources: new Set<string>(),
    }));
  }, []);

  const submitQuery = useCallback(
    (query: string) => {
      const previous = stateRef.current;
      const changed = query !== previous.query;

      if (changed) {
        // Every in-flight token is invalidated, not merely re-stamped, so a
        // settle arriving after this point cannot write into the cache we are
        // about to empty.
        //
        // Honest about its weight: the abort below already covers the common
        // case, and a mutant that removes this deletion SURVIVES the suite
        // because of that. It stays as defence in depth — a response that
        // resolved just before the abort landed would otherwise still pass the
        // per-source token check — but no test proves it, and pretending
        // otherwise would be worse than saying so.
        for (const key of Object.keys(tokensRef.current)) delete tokensRef.current[key];
        abortRef.current?.abort();
        abortRef.current = null;
        setState((prev) => ({
          ...prev,
          query,
          sources: {},
          fallbacks: {},
          loadingSources: new Set<string>(),
        }));
      }

      const source = previous.activeSource;

      // Soulseek is not a metadata source — the page hands the query to basic
      // search instead of fetching anything here.
      if (source === 'soulseek') {
        soulseekRef.current?.(query);
        return;
      }

      // Cache hit: instant, no request. Only valid when the query is unchanged,
      // because a change just emptied the cache.
      if (!changed && previous.sources[source]) return;

      void fetchSource(source, query);
    },
    [fetchSource],
  );

  const setActiveSource = useCallback(
    (source: string) => {
      const previous = stateRef.current;

      // Unknown, or an experimental source the user has not enabled — there is
      // no icon for it, so activating it would leave the row with nothing lit.
      if (!canSelectSource(source, previous.enabledExperimental)) return;

      // No credentials: hand off to Settings and DO NOT swap the active source,
      // so the user's working pick is still current when they come back
      // (shared-helpers.js:466-472). The row gates this too, but the rule
      // belongs here — a caller that is not the row must not slip past it.
      if (previous.configuredSources[source] === false) {
        unconfiguredRef.current?.(source);
        return;
      }

      setState((prev) => ({ ...prev, activeSource: source, userPickedSource: true }));

      if (source === 'soulseek') {
        // Deliberately fires even when soulseek is ALREADY active: clicking it
        // again is how the user re-runs a basic search.
        soulseekRef.current?.(previous.query);
        return;
      }
      if (!previous.query) return;
      if (previous.sources[source]) return;
      void fetchSource(source, previous.query);
    },
    [fetchSource],
  );

  const seedFromIdLookup = useCallback((raw: string, data: IdLookupResponse) => {
    const source = pickerSource(data.source);
    setState((prev) => ({
      ...prev,
      query: raw,
      activeSource: source,
      sources: {
        ...prev.sources,
        [source]: {
          ...emptySourceResults(),
          artists: data.artists ?? data.spotify_artists ?? [],
          albums: data.albums ?? data.spotify_albums ?? [],
          tracks: data.tracks ?? data.spotify_tracks ?? [],
          db_artists: data.db_artists ?? [],
        },
      },
      loadingSources: new Set<string>(),
    }));
  }, []);

  return { state, submitQuery, setActiveSource, syncQuery, seedFromIdLookup };
}

/** The slice the page renders — the active source's results, or an empty one. */
export function activeResults(state: SearchControllerState): SourceResults {
  return state.sources[state.activeSource] ?? emptySourceResults();
}

/** Has the active source answered yet for the current query? */
export function activeSourceSettled(state: SearchControllerState): boolean {
  return Boolean(state.sources[state.activeSource]);
}

export type { SearchSource };
