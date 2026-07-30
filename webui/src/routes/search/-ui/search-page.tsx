import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { SearchAlbum, SearchArtist, SearchLabel, SearchTrack } from '../-search.types';
import type { LibraryCheckTrack } from '../-search.types';

import { fetchLabels, lookupById } from '../-search.api';
import {
  openSearchAlbum,
  openSearchTrack,
  playOwnedTrack,
  streamSearchTrack,
} from '../-search.actions';
import {
  artistDetailPath,
  fallbackBannerText,
  isIdLookupQuery,
  labelDetailPath,
  SEARCH_DEBOUNCE_MS,
  shouldSearch,
  sourceLabel,
} from '../-search.helpers';
import { useAdoptedBasicSection } from '../-search.use-basic-section';
import { useArtistImages } from '../-search.use-artist-images';
import { activeResults, getPersistedQuery, useSearchController } from '../-search.use-controller';
import { useDismissOnOutsideClick } from '../-search.use-dismiss';
import { useLibraryCheck } from '../-search.use-library-check';
import { useVideoDownloads } from '../-search.use-video-downloads';
import { SearchBar } from './search-bar';
import { SearchResults } from './search-results';
import { SourceRow } from './source-row';

/** Which of the dropdown's three bodies is showing. */
type DropdownView = 'hidden' | 'loading' | 'empty' | 'results';

/**
 * The enhanced search page.
 *
 * The state machine is the vanilla's `_renderFromState` (search.js:109-200),
 * and its order matters: mid-fetch with no cache is LOADING even though a
 * previous source's results may still be in the cache; a settled fetch with
 * nothing in it is EMPTY; and an empty query hides the dropdown outright rather
 * than showing "no results" for a question nobody asked.
 *
 * Labels are counted out of that total on purpose. They come from a separate
 * endpoint and are purely additive, so a query that matched ONLY labels shows
 * the empty state — exactly as it does today.
 */
export function SearchPage() {
  const [basicHost, setBasicHost] = useState<HTMLElement | null>(null);
  // Seeded from the restored cache: coming back to results sitting above an
  // empty search box reads as a bug even when the results are right.
  const [query, setQuery] = useState(getPersistedQuery);
  const [idValue, setIdValue] = useState('');
  const [dismissed, setDismissed] = useState(false);
  const [labels, setLabels] = useState<SearchLabel[]>([]);
  const [idLookupPending, setIdLookupPending] = useState(false);

  const controller = useSearchController({
    onSoulseekSelected: (handoffQuery) => {
      // Basic search owns this one: fill its input and run the vanilla's search.
      const input = document.getElementById('downloads-search-input') as HTMLInputElement | null;
      if (input && handoffQuery) input.value = handoffQuery;
      setDismissed(true);
      if (input?.value) window.performDownloadsSearch?.();
    },
    onUnconfiguredSource: (source) => window.openSettingsForSource?.(source),
  });
  const { state, submitQuery, setActiveSource, syncQuery, seedFromIdLookup } = controller;

  const results = activeResults(state);
  const soulseekActive = state.activeSource === 'soulseek';

  useAdoptedBasicSection(basicHost, soulseekActive);

  const ownership = useLibraryCheck(results.albums, results.tracks);
  const artistImages = useArtistImages(results.db_artists, results.artists, state.activeSource);
  const { progress: videoProgress, download: downloadVideo } = useVideoDownloads();

  /**
   * Resolve a pasted link or id on its owning source (#775).
   *
   * The backend answers with the single hit plus the source that served it, and
   * that source is adopted so the album re-fetch and download flow route the
   * same way a normal search would.
   */
  const runIdLookup = useCallback(
    async (raw: string) => {
      const value = raw.trim();
      if (!value) return;
      setDismissed(false);
      setIdLookupPending(true);
      try {
        const data = await lookupById(value);
        if (data?.available && data.source) {
          seedFromIdLookup(value, data);
        } else {
          // The backend's own hint, surfaced without destroying what is on
          // screen — the vanilla toasts and shows the empty state.
          window.showToast?.(data?.message || 'No match for that link.', 'warning');
        }
      } catch {
        window.showToast?.('Link/ID lookup failed.', 'error');
      } finally {
        setIdLookupPending(false);
      }
    },
    [seedFromIdLookup],
  );

  /**
   * Enter and the debounce share this.
   *
   * A bare MusicBrainz UUID is an exact identifier, not a name, so it goes to
   * the id resolver from BOTH paths rather than being fuzzy-searched.
   */
  const runSearch = useCallback(
    (raw: string) => {
      const trimmed = raw.trim();
      if (!shouldSearch(trimmed)) return;
      if (isIdLookupQuery(trimmed)) {
        void runIdLookup(trimmed);
        return;
      }
      setDismissed(false);
      submitQuery(trimmed);
    },
    [submitQuery, runIdLookup],
  );

  /** The debounce. Raised to 600ms for #751 so a name coalesces into ONE search. */
  useEffect(() => {
    const trimmed = query.trim();
    if (!shouldSearch(trimmed)) {
      setDismissed(true);
      return;
    }
    const timer = setTimeout(() => runSearch(trimmed), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, runSearch]);

  /**
   * Labels, fetched once per query and rendered alongside the source results.
   *
   * Only when there is something else to show: the vanilla reaches
   * _maybeFetchLabels AFTER the empty-state return, so a labels-only match never
   * appears on its own.
   */
  const hasSourceResults =
    results.db_artists.length + results.artists.length + results.albums.length +
      results.tracks.length + results.videos.length >
    0;
  useEffect(() => {
    const trimmed = state.query.trim();
    if (!trimmed || !hasSourceResults) {
      setLabels([]);
      return;
    }
    let live = true;
    void fetchLabels(trimmed).then((found) => {
      if (live) setLabels(found);
    });
    return () => {
      live = false;
    };
  }, [state.query, hasSourceResults]);

  const loading = state.loadingSources.has(state.activeSource) || idLookupPending;
  const cached = Boolean(state.sources[state.activeSource]);

  const view: DropdownView = useMemo(() => {
    if (dismissed || soulseekActive) return 'hidden';
    if (idLookupPending) return 'loading';
    if (loading && !cached) return 'loading';
    if (!cached) return state.query ? 'empty' : 'hidden';
    return hasSourceResults ? 'results' : 'empty';
  }, [dismissed, soulseekActive, idLookupPending, loading, cached, state.query, hasSourceResults]);

  const open = view !== 'hidden';
  const onDismiss = useCallback(() => setDismissed(true), []);
  useDismissOnOutsideClick(open, onDismiss);

  /**
   * The global download widget syncs its query here before clicking the
   * Soulseek icon (downloads.js:5710-5740). Without it the click hands off THIS
   * page's last query — which, now that results survive navigation, can be a
   * stale one — and overwrites what the widget just typed.
   *
   * It SYNCS and does not search. The widget writes the basic input and then
   * clicks the icon, which is what runs the search; searching here as well
   * would run it twice, and updating the enhanced input would start the
   * debounce and run it a third time.
   */
  const syncRef = useRef(syncQuery);
  syncRef.current = syncQuery;
  useEffect(() => {
    window._searchPageSetQuery = (next: string) => syncRef.current(next.trim());
    return () => {
      delete window._searchPageSetQuery;
    };
  }, []);

  const served = state.fallbacks[state.activeSource];

  /**
   * A library artist resolves under 'library'; a found one under the source it
   * came from, with its name in tow. Both need the source SEGMENT — the route is
   * /artist-detail/$source/$id and a two-segment path resolves to nothing.
   */
  const onArtistHref = (artist: SearchArtist, inLibrary: boolean) =>
    inLibrary
      ? artistDetailPath(artist.id ?? '')
      : artistDetailPath(artist.id ?? '', state.activeSource, artist.name);

  const onLabelHref = (label: SearchLabel) => labelDetailPath(label.id ?? '', label.name);

  return (
    <div className="downloads-content">
      <div className="downloads-main-panel">
        <div className={`downloads-header${open ? ' enh-results-active-hide' : ''}`}>
          <div className="downloads-header-content">
            <div className="downloads-header-text">
              <h2 className="downloads-title">
                <img src="/static/search.png" className="page-header-icon" alt="" />
                <span>Search</span>
              </h2>
              <p className="downloads-subtitle">
                Find artists, albums, and tracks from any metadata source
              </p>
            </div>
          </div>
        </div>

        <SourceRow state={state} onSelect={setActiveSource} onOpenSettings={openSettings} />

        {/*
          The vanilla basic-search panel is MOVED in here; see the hook.
          display:contents so this wrapper adds nothing to the layout — the
          panel keeps the box it has as a direct child of the main panel.
        */}
        <div ref={setBasicHost} style={{ display: 'contents' }} />

        <div className={`search-section${soulseekActive ? '' : ' active'}`} id="enhanced-search-section">
          <SearchBar
            query={query}
            onQueryChange={setQuery}
            onSubmit={() => runSearch(query)}
            onClear={() => {
              setQuery('');
              setDismissed(true);
            }}
            idValue={idValue}
            onIdChange={setIdValue}
            onIdSubmit={() => void runIdLookup(idValue)}
          />

          <div id="enhanced-dropdown" className={`enhanced-dropdown${open ? '' : ' hidden'}`}>
            <div className="enhanced-dropdown-content">
              <button
                id="enhanced-dropdown-close"
                className="enhanced-dropdown-close"
                type="button"
                onClick={(event) => {
                  // The document-level dismiss would fire on this same click.
                  event.stopPropagation();
                  setDismissed(true);
                }}
              >
                <span>✕</span> Close Results
              </button>

              <div className={`enhanced-loading${view === 'loading' ? '' : ' hidden'}`} id="enhanced-loading">
                <div className="spinner" />
                <p id="enhanced-loading-text">
                  {idLookupPending
                    ? 'Resolving link…'
                    : `Searching ${sourceLabel(state.activeSource)} and your library...`}
                </p>
              </div>

              <div className={`enhanced-empty${view === 'empty' ? '' : ' hidden'}`} id="enhanced-empty">
                <div className="empty-icon">🔍</div>
                <p>No results found</p>
              </div>

              <div
                id="enhanced-results-container"
                className={`enhanced-results-container${view === 'results' ? '' : ' hidden'}`}
              >
                <div
                  id="enh-fallback-banner"
                  className={`enh-fallback-banner${served ? '' : ' hidden'}`}
                >
                  {served ? fallbackBannerText(state.activeSource, served) : null}
                </div>

                <SearchResults
                  activeSource={state.activeSource}
                  dbArtists={results.db_artists}
                  artists={results.artists}
                  albums={results.albums}
                  tracks={results.tracks}
                  labels={labels}
                  videos={results.videos}
                  videoProgress={videoProgress}
                  ownership={ownership}
                  artistImages={artistImages}
                  onArtistHref={onArtistHref}
                  onLabelHref={onLabelHref}
                  onAlbumClick={(album: SearchAlbum) => void openSearchAlbum(album, state.activeSource)}
                  onTrackClick={(track: SearchTrack) => void openSearchTrack(track)}
                  onTrackPlay={(track: SearchTrack, row: LibraryCheckTrack | undefined) => {
                    if (row) playOwnedTrack(row);
                    else void streamSearchTrack(track);
                  }}
                  onVideoDownload={downloadVideo}
                />
              </div>
            </div>
          </div>

          {/*
            NOT decoration. showSearchDownloadBubbles (shared-helpers.js:2385)
            renders the download-progress bubbles into #enhanced-main-results-area,
            fed by the registerSearchDownload call every album and track download
            makes. Without this container those downloads have nowhere to draw and
            the function silently returns. Its children are static so React never
            re-renders over what the vanilla writes there.
          */}
          <div className={`search-results-container${open ? ' enh-results-active-hide' : ''}`}>
            <div className="search-results-header">
              <h3>Search Results</h3>
            </div>
            <div className="search-results-scroll-area" id="enhanced-main-results-area">
              <div className="search-results-placeholder">
                <p>Search results will appear here when you select an album or track.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Jump to the Settings card for a source that has no credentials. */
function openSettings(source: string) {
  window.openSettingsForSource?.(source);
}
