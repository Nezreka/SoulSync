import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { BasicAlbum, BasicTrack } from '../-basic.types';
import type { SearchAlbum, SearchArtist, SearchLabel, SearchTrack } from '../-search.types';
import type { LibraryCheckTrack } from '../-search.types';

import {
  downloadAlbum,
  downloadAlbumTrack,
  downloadTrack,
  downloadUnmatched,
  matchedDownloadAlbum,
  matchedDownloadAlbumTrack,
  matchedDownloadTrack,
  streamAlbumTrack,
  streamTrack,
} from '../-basic.actions';
import { useBasicSearchController } from '../-basic.use-controller';
import {
  openSearchAlbum,
  openSearchTrack,
  playOwnedTrack,
  streamSearchTrack,
} from '../-search.actions';
import { fetchLabels, lookupById } from '../-search.api';
import {
  artistDetailPath,
  fallbackBannerText,
  inLibraryArtistPath,
  isIdLookupQuery,
  labelDetailPath,
  loadRecentSearches,
  removeRecentSearch,
  saveRecentSearch,
  SEARCH_DEBOUNCE_MS,
  shouldSearch,
  sourceLabel,
  splitAlbums,
} from '../-search.helpers';
import { useArtistImages } from '../-search.use-artist-images';
import { activeResults, getPersistedQuery, useSearchController } from '../-search.use-controller';
import { useDismissOnOutsideClick } from '../-search.use-dismiss';
import { useLibraryCheck } from '../-search.use-library-check';
import { useVideoDownloads } from '../-search.use-video-downloads';
import { BasicSearch } from './basic-search';
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
  // Seeded from the restored cache: coming back to results sitting above an
  // empty search box reads as a bug even when the results are right.
  const [query, setQuery] = useState(getPersistedQuery);
  const [idValue, setIdValue] = useState('');
  const [dismissed, setDismissed] = useState(false);
  const [labels, setLabels] = useState<SearchLabel[]>([]);
  const [idLookupPending, setIdLookupPending] = useState(false);
  const [recents, setRecents] = useState<string[]>(loadRecentSearches);

  const basic = useBasicSearchController();
  const basicSearchRef = useRef(basic.search);
  basicSearchRef.current = basic.search;

  const controller = useSearchController({
    onSoulseekSelected: (handoffQuery) => {
      // Soulseek is not a metadata source: selecting it shows the basic panel
      // and runs the file search there.
      setDismissed(true);
      // Only with something to search for — clicking the icon on an empty page
      // should switch panels, not scold the user for an empty query.
      if (handoffQuery) basicSearchRef.current(handoffQuery);
    },
    onUnconfiguredSource: (source) => window.openSettingsForSource?.(source),
  });
  const { state, submitQuery, setActiveSource, syncQuery, seedFromIdLookup } = controller;

  const results = activeResults(state);
  const soulseekActive = state.activeSource === 'soulseek';

  const basicActions = useMemo(
    () => ({
      onDownloadTrack: (track: BasicTrack) => void downloadTrack(track),
      onStreamTrack: (track: BasicTrack) => void streamTrack(track),
      onMatchedTrack: (track: BasicTrack) => matchedDownloadTrack(track),
      onDownloadAlbum: (albumRow: BasicAlbum) => void downloadAlbum(albumRow),
      onMatchedAlbum: (albumRow: BasicAlbum) => matchedDownloadAlbum(albumRow),
      onDownloadAlbumTrack: (albumRow: BasicAlbum, _albumIndex: number, trackIndex: number) =>
        void downloadAlbumTrack(albumRow, trackIndex),
      onStreamAlbumTrack: (albumRow: BasicAlbum, _albumIndex: number, trackIndex: number) =>
        void streamAlbumTrack(albumRow, trackIndex),
      onMatchedAlbumTrack: (albumRow: BasicAlbum, _albumIndex: number, trackIndex: number) =>
        matchedDownloadAlbumTrack(albumRow, trackIndex),
    }),
    [],
  );

  /**
   * The matched-download modal's "Skip Matching" button reaches back here.
   *
   * That modal is still vanilla (wishlist-tools.js) and has no way to run a
   * download itself — the path it used to take was broken three ways over; see
   * downloadUnmatched.
   */
  useEffect(() => {
    window._basicDownloadUnmatched = (result) =>
      void downloadUnmatched(result as Parameters<typeof downloadUnmatched>[0]);
    return () => {
      delete window._basicDownloadUnmatched;
    };
  }, []);

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
      setRecents(saveRecentSearch(trimmed));
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
    results.db_artists.length +
      results.artists.length +
      results.albums.length +
      results.tracks.length +
      results.videos.length >
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

  /**
   * Repaint the download bubbles on mount. The bubble STORE and painter are
   * vanilla (shared-helpers.js showSearchDownloadBubbles → innerHTML into
   * #enhanced-main-results-area); the vanilla page never unmounted, so
   * painting only on download events was enough. This page recreates the
   * container with a static placeholder every mount — without this ask,
   * in-flight downloads vanish after navigating away and back, and the boot
   * hydrate (init.js) paints into a container that doesn't exist yet when the
   * app starts on any other page.
   */
  useEffect(() => {
    window.showSearchDownloadBubbles?.();
  }, []);

  const served = state.fallbacks[state.activeSource];

  /**
   * A library artist opens in Library v2 when v2 knows it, and otherwise
   * resolves under 'library'; a found one resolves under the source it came
   * from, with its name in tow. Both artist-detail forms need the source
   * SEGMENT — the route is /artist-detail/$source/$id and a two-segment path
   * resolves to nothing.
   */
  const onArtistHref = (artist: SearchArtist, inLibrary: boolean) =>
    inLibrary
      ? inLibraryArtistPath(artist)
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

        <BasicSearch controller={basic} actions={basicActions} active={soulseekActive} />

        <div
          className={`search-section${soulseekActive ? '' : ' active'}`}
          id="enhanced-search-section"
        >
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

          {/* Recent searches — the idle surface. Shown only when nothing else
              is: no open results, no soulseek panel, nothing typed. */}
          {!open && !soulseekActive && !query.trim() && recents.length > 0 ? (
            <div className="enh-recent" id="enh-recent-searches">
              <div className="enh-section-header">
                <h4 className="enh-section-title">Recent searches</h4>
              </div>
              <div className="enh-recent-chips">
                {recents.map((entry) => (
                  <span key={entry} className="enh-recent-chip">
                    <button
                      type="button"
                      className="enh-recent-chip-label"
                      onClick={() => {
                        setQuery(entry);
                        runSearch(entry);
                      }}
                    >
                      {entry}
                    </button>
                    <button
                      type="button"
                      className="enh-recent-chip-x"
                      title="Remove from recent searches"
                      onClick={() => setRecents(removeRecentSearch(entry))}
                    >
                      ✕
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ) : null}

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

              <div
                className={`enhanced-loading${view === 'loading' ? '' : ' hidden'}`}
                id="enhanced-loading"
              >
                <div className="spinner" />
                {/* Skeleton ghosts in the shapes the results will take. */}
                <div className="enh-skel-strip" aria-hidden="true">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <span key={`c${i}`} className="enh-skel enh-skel--circle" />
                  ))}
                </div>
                <div className="enh-skel-strip" aria-hidden="true">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <span key={`s${i}`} className="enh-skel enh-skel--square" />
                  ))}
                </div>
                <p id="enhanced-loading-text">
                  {idLookupPending
                    ? 'Resolving link…'
                    : `Searching ${sourceLabel(state.activeSource)} and your library...`}
                </p>
              </div>

              <div
                className={`enhanced-empty${view === 'empty' ? '' : ' hidden'}`}
                id="enhanced-empty"
              >
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

                {view === 'results' ? (
                  <JumpChips
                    artists={results.db_artists.length + results.artists.length}
                    albums={splitAlbums(results.albums).albums.length}
                    singles={splitAlbums(results.albums).singlesAndEps.length}
                    tracks={results.tracks.length}
                    labels={labels.length}
                  />
                ) : null}

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
                  onAlbumClick={(album: SearchAlbum) =>
                    void openSearchAlbum(album, state.activeSource)
                  }
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

/** The chip row that scrolls to a section — rendered only for sections with
 *  rows, anchored on the section ids the results already carry. */
function JumpChips({
  artists,
  albums,
  singles,
  tracks,
  labels,
}: {
  artists: number;
  albums: number;
  singles: number;
  tracks: number;
  labels: number;
}) {
  const chips: [string, number, string][] = [
    ['Artists', artists, 'enh-spotify-artists-section'],
    ['Albums', albums, 'enh-albums-section'],
    ['Singles & EPs', singles, 'enh-singles-section'],
    ['Tracks', tracks, 'enh-tracks-section'],
    ['Labels', labels, 'enh-labels-section'],
  ];
  const visible = chips.filter(([, count]) => count > 0);
  if (visible.length < 2) return null;
  return (
    <div className="enh-jump-chips" id="enh-jump-chips">
      {visible.map(([label, , sectionId]) => (
        <button
          key={sectionId}
          type="button"
          className="enh-jump-chip"
          onClick={() =>
            document
              .getElementById(sectionId)
              ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/** Jump to the Settings card for a source that has no credentials. */
function openSettings(source: string) {
  window.openSettingsForSource?.(source);
}
