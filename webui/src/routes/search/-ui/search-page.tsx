import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type {
  SearchAlbum,
  SearchArtist,
  SearchLabel,
  SearchPlaylist,
  SearchTrack,
} from '../-search.types';
import type { LibraryCheckTrack } from '../-search.types';
import type { ResultFilter } from './search-results';

import { startDownload } from '../-basic.actions';
import { useBasicSearchController } from '../-basic.use-controller';
import {
  openSearchAlbum,
  openSearchTrack,
  playOwnedTrack,
  streamSearchTrack,
} from '../-search.actions';
import { fetchLabels, lookupById } from '../-search.api';
import {
  fallbackBannerText,
  inLibraryArtistPath,
  isIdLookupQuery,
  labelDetailPath,
  libraryV2DiscoveryArtistPath,
  loadRecentSearches,
  removeRecentSearch,
  saveRecentSearch,
  SEARCH_DEBOUNCE_MS,
  shouldSearch,
  sourceLabel,
} from '../-search.helpers';
import { useArtistImages } from '../-search.use-artist-images';
import { activeResults, getPersistedQuery, useSearchController } from '../-search.use-controller';
import { useLibraryCheck } from '../-search.use-library-check';
import { useVideoDownloads } from '../-search.use-video-downloads';
import { BasicSearch } from './basic-search';
import { PlaylistPreviewModal } from './playlist-preview-modal';
import { SearchBar } from './search-bar';
import { ClockIcon } from './search-icons';
import { FilterPills, resultCounts, SearchResults } from './search-results';
import styles from './search.module.css';
import { catalogSources, ModeTabs, modeOf, SourcePicker } from './source-row';

/** the idle page's browse tiles. quiet two-stop gradients, no emoji */
const EXPLORE_CATEGORIES = [
  { id: 'trending', label: 'Top trending', query: 'Trending', from: '#7a1f3d', to: '#3b0f1f' },
  {
    id: 'new_releases',
    label: 'New releases',
    query: 'New Releases',
    from: '#3a3a8c',
    to: '#1c1c48',
  },
  {
    id: 'electronic',
    label: 'Electronic & dance',
    query: 'Electronic',
    from: '#0f5e6e',
    to: '#082f38',
  },
  { id: 'rock', label: 'Rock & alternative', query: 'Rock', from: '#7a4a12', to: '#3d250a' },
  { id: 'hiphop', label: 'Hip-hop & rap', query: 'Hip Hop', from: '#5a2d8c', to: '#2c1646' },
  { id: 'chill', label: 'Lo-fi & chill', query: 'Chill', from: '#1f6b4f', to: '#0f3528' },
  { id: 'jazz', label: 'Jazz & soul', query: 'Jazz', from: '#80305e', to: '#401830' },
  {
    id: 'soundtracks',
    label: 'Soundtracks & score',
    query: 'Soundtrack',
    from: '#284f8c',
    to: '#142846',
  },
];

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
  const [filter, setFilter] = useState<ResultFilter>('all');
  const [dismissed, setDismissed] = useState(false);
  const [labels, setLabels] = useState<SearchLabel[]>([]);
  const [idLookupPending, setIdLookupPending] = useState(false);
  const [recents, setRecents] = useState<string[]>(loadRecentSearches);
  const [previewPlaylist, setPreviewPlaylist] = useState<SearchPlaylist | null>(null);

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

  // Catalog returns to the metadata source the user was last on, not the default
  const catalogRef = useRef('');
  if (modeOf(state.activeSource) === 'catalog') catalogRef.current = state.activeSource;
  const catalogSource = catalogRef.current || catalogSources(state)[0] || 'spotify';

  // a new question, or a new source, starts from All
  useEffect(() => {
    setFilter('all');
  }, [state.query, state.activeSource]);

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
   * A bare MusicBrainz UUID or a pasted link is an exact identifier, not a
   * name, so it goes to the id resolver from BOTH paths rather than being
   * fuzzy-searched. that is why there is no separate link box any more.
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
      results.playlists.length +
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

  // results are the page now, not a dropdown over it, so a click elsewhere no
  // longer throws them away. clearing the box does.
  const open = view !== 'hidden';

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

  /** Library matches and provider discoveries both open in their intended
   * Library V2 presentation. The old artist-detail route remains a fallback
   * only when a local result unexpectedly lacks its V2 catalogue id. */
  const onArtistHref = (artist: SearchArtist, inLibrary: boolean) =>
    inLibrary
      ? inLibraryArtistPath(artist)
      : libraryV2DiscoveryArtistPath(artist.id ?? '', state.activeSource, artist.name);

  const onLabelHref = (label: SearchLabel) => labelDetailPath(label.id ?? '', label.name);

  const counts = resultCounts({
    dbArtists: results.db_artists,
    artists: results.artists,
    albums: results.albums,
    tracks: results.tracks,
    playlists: results.playlists,
    labels,
    activeSource: state.activeSource,
  });
  const videosMode = state.activeSource === 'youtube_videos';
  const idle = !open && !soulseekActive && !query.trim();

  return (
    <div className="downloads-content">
      <div className={`downloads-main-panel ${styles.page}`}>
        <header className={styles.head} id="search-head">
          <div>
            <h1 className={styles.title}>Search</h1>
            <p className={styles.subtitle}>
              {soulseekActive
                ? 'Raw files from Soulseek, grabbed exactly as shared'
                : videosMode
                  ? 'Music videos from YouTube, straight to your library'
                  : 'Find any artist, album or track, then download it tagged and filed'}
            </p>
          </div>
          <ModeTabs state={state} catalogSource={catalogSource} onSelect={setActiveSource} />
        </header>

        <BasicSearch controller={basic} onDownload={startDownload} active={soulseekActive} />

        <div
          className={`search-section${soulseekActive ? '' : ' active'}`}
          id="enhanced-search-section"
          style={soulseekActive ? undefined : { display: 'flex', flexDirection: 'column', gap: 18 }}
        >
          <SearchBar
            query={query}
            onQueryChange={setQuery}
            onSubmit={() => runSearch(query)}
            onClear={() => {
              setQuery('');
              setDismissed(true);
            }}
            searching={loading}
            placeholder={
              videosMode ? 'Search music videos' : 'Artists, albums, tracks, or paste a link'
            }
            picker={
              <SourcePicker
                state={state}
                onSelect={setActiveSource}
                onOpenSettings={openSettings}
              />
            }
          />

          {/* the idle page: recent searches and browse tiles. only when nothing
              else is showing and nothing is typed */}
          {idle ? (
            <div className={styles.idle}>
              {recents.length > 0 ? (
                <section id="enh-recent-searches">
                  <div className={styles.sectionHead}>
                    <h2 className={styles.sectionTitle}>Recent</h2>
                    <button
                      type="button"
                      className={styles.textLink}
                      onClick={() => {
                        try {
                          window.localStorage.removeItem('soulsyncRecentSearches');
                        } catch {}
                        setRecents([]);
                      }}
                    >
                      Clear all
                    </button>
                  </div>
                  <div className={styles.chips}>
                    {recents.map((entry) => (
                      <span key={entry} className={styles.chip}>
                        <button
                          type="button"
                          className={styles.chipLabel}
                          onClick={() => {
                            setQuery(entry);
                            runSearch(entry);
                          }}
                        >
                          <ClockIcon />
                          {entry}
                        </button>
                        <button
                          type="button"
                          className={styles.chipX}
                          aria-label={`Remove ${entry} from recent searches`}
                          title="Remove"
                          onClick={() => setRecents(removeRecentSearch(entry))}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                </section>
              ) : null}

              {!videosMode ? (
                <section id="enh-explore-section">
                  <div className={styles.sectionHead}>
                    <h2 className={styles.sectionTitle}>Browse</h2>
                  </div>
                  <div className={styles.explore}>
                    {EXPLORE_CATEGORIES.map((cat) => (
                      <button
                        key={cat.id}
                        type="button"
                        className={styles.tile}
                        style={{ background: `linear-gradient(135deg, ${cat.from}, ${cat.to})` }}
                        onClick={() => {
                          setQuery(cat.query);
                          runSearch(cat.query);
                        }}
                      >
                        <b>{cat.label}</b>
                      </button>
                    ))}
                  </div>
                </section>
              ) : null}
            </div>
          ) : null}

          <div id="enhanced-dropdown" className={open ? undefined : 'hidden'}>
            {view === 'loading' ? (
              <div className={styles.state} id="enhanced-loading" role="status">
                <div className={styles.skeletons} aria-hidden="true">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <span
                      key={i}
                      className={`${styles.skel}${i < 2 ? ` ${styles.skelRound}` : ''}`}
                    />
                  ))}
                </div>
                <p className={styles.stateText} id="enhanced-loading-text" style={{ margin: 0 }}>
                  {idLookupPending
                    ? 'Looking up that link…'
                    : `Searching ${sourceLabel(state.activeSource)}…`}
                </p>
              </div>
            ) : null}

            {view === 'empty' ? (
              <div className={styles.state} id="enhanced-empty">
                <p className={styles.stateTitle}>
                  {query.trim() ? `Nothing for “${query.trim()}”` : 'No results'}
                </p>
                <p className={styles.stateText}>
                  Check the spelling, try fewer words, or search another source.
                </p>
              </div>
            ) : null}

            <div
              id="enhanced-results-container"
              className={view === 'results' ? styles.results : 'hidden'}
            >
              {served ? (
                <div className={styles.notice} id="enh-fallback-banner">
                  {fallbackBannerText(state.activeSource, served)}
                </div>
              ) : null}

              {view === 'results' && !videosMode ? (
                <FilterPills
                  counts={counts}
                  filter={filter}
                  onFilter={(next) => {
                    setFilter(next);
                    document
                      .getElementById('enhanced-results-container')
                      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                  }}
                  servedBy={served ? sourceLabel(served) : sourceLabel(state.activeSource)}
                />
              ) : null}

              <SearchResults
                activeSource={state.activeSource}
                dbArtists={results.db_artists}
                artists={results.artists}
                albums={results.albums}
                tracks={results.tracks}
                playlists={results.playlists}
                labels={labels}
                query={state.query}
                videos={results.videos}
                videoProgress={videoProgress}
                ownership={ownership}
                artistImages={artistImages}
                filter={filter}
                onFilter={setFilter}
                onArtistHref={onArtistHref}
                onLabelHref={onLabelHref}
                onAlbumClick={(album: SearchAlbum) =>
                  void openSearchAlbum(album, state.activeSource)
                }
                onTrackClick={(track: SearchTrack) => void openSearchTrack(track)}
                onPlaylistClick={(playlist: SearchPlaylist) => setPreviewPlaylist(playlist)}
                onTrackPlay={(track: SearchTrack, row: LibraryCheckTrack | undefined) => {
                  if (row) playOwnedTrack(row);
                  else void streamSearchTrack(track);
                }}
                onVideoDownload={downloadVideo}
              />
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

      {previewPlaylist && (
        <PlaylistPreviewModal playlist={previewPlaylist} onClose={() => setPreviewPlaylist(null)} />
      )}
    </div>
  );
}

/** Jump to the Settings card for a source that has no credentials. */
function openSettings(source: string) {
  window.openSettingsForSource?.(source);
}
