import { useQuery } from '@tanstack/react-query';
import { useRouter, useRouterState } from '@tanstack/react-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Skeleton } from '@/components/primitives';
import { profileAsksFirst } from '@/platform/shell/download-rights';

import type { BasicSource } from '../-basic.types';
import type {
  SearchAlbum,
  SearchArtist,
  SearchLabel,
  SearchPlaylist,
  SearchTrack,
  SearchVideo,
} from '../-search.types';
import type { LibraryCheckTrack } from '../-search.types';
import type { ResultFilter } from './search-results';
import type { VideoProgress } from './video-grid';

import { startDownload } from '../-basic.actions';
import { sourceLabel as basicSourceLabel } from '../-basic.api';
import { useBasicSearchController } from '../-basic.use-controller';
import {
  openSearchAlbum,
  openSearchTrack,
  playOwnedTrack,
  streamSearchTrack,
} from '../-search.actions';
import { fetchLabels, lookupById } from '../-search.api';
import {
  browseCount,
  browseHref,
  browseTitle,
  BROWSE_TILES,
  type BrowseResult,
  fetchBrowse,
} from '../-search.browse';
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
} from '../-search.helpers';
import { useArtistImages } from '../-search.use-artist-images';
import { activeResults, getPersistedQuery, useSearchController } from '../-search.use-controller';
import { useLibraryCheck } from '../-search.use-library-check';
import { useVideoDownloads } from '../-search.use-video-downloads';
import {
  libraryScopesQueryOptions,
  libraryV2UiPreferencesQueryOptions,
} from '../../library/-library-v2.api';
import { BasicSearch } from './basic-search';
import { PlaylistPreviewModal } from './playlist-preview-modal';
import { SearchBar } from './search-bar';
import { ClockIcon } from './search-icons';
import { FilterPills, resultCounts, SearchResults } from './search-results';
import styles from './search.module.css';
import { catalogSources, ModeTabs, modeOf, SourcePicker } from './source-row';

/** Where a download started here lands, and which library "in your library"
 *  means (#1199). Only on an install with more than one library, and only when
 *  it is not simply the shared one -- or the admin can switch it. */
function LibraryTargetNote() {
  const { data } = useQuery(libraryScopesQueryOptions());
  if (!data?.separated || (!data.switchable && data.target === 'shared')) return null;
  return (
    <p className={styles.libraryTarget} data-library-target={data.target}>
      Downloads land in <strong>{data.targetName}</strong>
      {data.switchable ? <span> · switch in the sidebar</span> : null}
    </p>
  );
}

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
  const router = useRouter();
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

  // a browse tile's view (/search?browse=genre:jazz). typing takes over from
  // it, and clearing the box comes back to it.
  const browseId = useRouterState({
    select: (s) => new URLSearchParams(s.location.searchStr).get('browse'),
  });
  const browsing = Boolean(browseId) && !query.trim() && !soulseekActive;
  const browseQuery = useQuery({
    queryKey: ['search-browse', browseId],
    queryFn: ({ signal }) => fetchBrowse(browseId ?? '', signal),
    enabled: browsing,
    staleTime: 5 * 60_000,
  });
  const [browseFilter, setBrowseFilter] = useState<ResultFilter>('all');
  useEffect(() => {
    setBrowseFilter('all');
  }, [browseId]);
  const browse: BrowseResult | undefined = browsing ? browseQuery.data : undefined;
  // the cards' library badges and artist photos follow whatever is on screen
  const shown = browse ?? results;

  // Catalog returns to the metadata source the user was last on, not the default
  const catalogRef = useRef('');
  if (modeOf(state.activeSource) === 'catalog') catalogRef.current = state.activeSource;
  const catalogSource = catalogRef.current || catalogSources(state)[0] || 'spotify';

  // a new question, or a new source, starts from All
  useEffect(() => {
    setFilter('all');
  }, [state.query, state.activeSource]);

  const ownership = useLibraryCheck(shown.albums, shown.tracks);
  const artistImages = useArtistImages(
    shown.db_artists,
    shown.artists,
    browse ? (browse.artists[0]?.source ?? state.activeSource) : state.activeSource,
  );
  const {
    progress: videoProgress,
    download: downloadVideo,
    request: requestVideo,
    requested: requestedVideos,
  } = useVideoDownloads();

  // a profile without download rights asks instead of downloading. read at
  // click time, not render time: a profile switch changes the answer.
  const handleVideoAcquire = useCallback(
    (video: SearchVideo) => {
      if (profileAsksFirst()) {
        void requestVideo(video);
      } else {
        downloadVideo(video);
      }
    },
    [downloadVideo, requestVideo],
  );

  // Requested videos show the saved tick so the card stops looking clickable.
  // Display-only: the grid's states are download-shaped, and a requested video
  // never enters the download flow (request() no-ops on re-click).
  const videoProgressForGrid = useMemo(() => {
    if (requestedVideos.size === 0) return videoProgress;
    const merged: Record<string, VideoProgress> = { ...videoProgress };
    for (const id of requestedVideos) {
      if (!merged[id]) merged[id] = { state: 'completed', percent: 100 };
    }
    return merged;
  }, [videoProgress, requestedVideos]);

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

  const loadingFor = useSecondsWhile(view === 'loading');

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

  /**
   * A library artist opens in Library V2 by its catalogue id; a found one on
   * the artist page under the source it came from, with its name in tow. That
   * route still checks the catalogue first, so an artist this result list did
   * not recognise lands in Library V2 all the same.
   */
  // a library artist opens in the header this profile picked for arrivals from
  // outside the library (View Settings); no preferences yet means the rich one
  const headerFromSearch =
    useQuery({ ...libraryV2UiPreferencesQueryOptions(), retry: false }).data?.artist_page
      ?.header_elsewhere ?? 'rich';
  const onArtistHref = (artist: SearchArtist, inLibrary: boolean) =>
    inLibrary
      ? inLibraryArtistPath(artist, headerFromSearch)
      : artistDetailPath(artist.id ?? '', state.activeSource, artist.name);

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
  // files mode searches whatever download source is picked, not only soulseek
  const filesSource = basic.state.singleSource
    ? basic.state.sources[0]
    : basic.state.sources.find((source) => source.name === basic.state.activeSource);
  const idle = !open && !soulseekActive && !query.trim() && !browsing;

  return (
    <div className="downloads-content">
      <div className={`downloads-main-panel ${styles.page}`}>
        <header className={styles.head} id="search-head">
          <div>
            <h1 className={styles.title}>Search</h1>
            <p className={styles.subtitle}>
              {soulseekActive
                ? filesSubtitle(filesSource)
                : videosMode
                  ? 'Music videos from YouTube, straight to your library'
                  : 'Find any artist, album or track, then download it tagged and filed'}
            </p>
            <LibraryTargetNote />
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
                    {BROWSE_TILES.map((tile) => (
                      <button
                        key={tile.id}
                        type="button"
                        className={styles.tile}
                        style={{ background: `linear-gradient(135deg, ${tile.from}, ${tile.to})` }}
                        onClick={() => void router.navigate({ href: browseHref(tile.id) })}
                      >
                        <b>{tile.label}</b>
                      </button>
                    ))}
                  </div>
                </section>
              ) : null}
            </div>
          ) : null}

          {browsing && browseId ? (
            <section
              className={styles.browse}
              id="enh-browse-view"
              aria-busy={browseQuery.isLoading || undefined}
            >
              <div className={styles.browseHead}>
                <button
                  type="button"
                  className={styles.textLink}
                  onClick={() => void router.navigate({ href: '/search' })}
                >
                  ← Browse
                </button>
                <h2 className={styles.browseTitle}>{browse?.title ?? browseTitle(browseId)}</h2>
                {browse?.subtitle ? <p className={styles.browseSub}>{browse.subtitle}</p> : null}
                {browse?.related.length ? (
                  <div className={styles.chips}>
                    {browse.related.map((genre) => (
                      <span key={genre} className={styles.chip}>
                        <button
                          type="button"
                          className={`${styles.chipLabel} ${styles.chipPlain}`}
                          onClick={() =>
                            void router.navigate({
                              href: browseHref(`genre:${genre.toLowerCase()}`),
                            })
                          }
                        >
                          {genre}
                        </button>
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
              {browseQuery.isLoading ? (
                <div className={styles.skeletons} aria-hidden="true">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <Skeleton
                      key={i}
                      className={`${styles.skel}${i < 2 ? ` ${styles.skelRound}` : ''}`}
                    />
                  ))}
                </div>
              ) : browseQuery.isError ? (
                <div className={styles.state}>
                  <p className={styles.stateTitle}>Couldn’t load this right now</p>
                  <p className={styles.stateText}>Try again in a moment, or search above.</p>
                </div>
              ) : browse && browseCount(browse) === 0 ? (
                <div className={styles.state}>
                  <p className={styles.stateTitle}>Nothing here yet</p>
                  <p className={styles.stateText}>
                    {browseId === 'new'
                      ? 'New releases fill in as your watchlist and discovery scans run.'
                      : 'This fills in as SoulSync learns more music. Try another tile, or search above.'}
                  </p>
                </div>
              ) : browse ? (
                <div className={styles.results}>
                  <SearchResults
                    activeSource={browse.artists[0]?.source ?? state.activeSource}
                    dbArtists={[]}
                    artists={browse.artists}
                    albums={browse.albums}
                    tracks={browse.tracks}
                    playlists={browse.playlists}
                    labels={[]}
                    videos={[]}
                    videoProgress={videoProgressForGrid}
                    ownership={ownership}
                    artistImages={artistImages}
                    filter={browseFilter}
                    onFilter={setBrowseFilter}
                    onArtistHref={(artist, inLibrary) =>
                      inLibrary
                        ? inLibraryArtistPath(artist, headerFromSearch)
                        : artistDetailPath(
                            artist.id ?? '',
                            artist.source ?? state.activeSource,
                            artist.name,
                          )
                    }
                    onLabelHref={onLabelHref}
                    onAlbumClick={(album: SearchAlbum) =>
                      void openSearchAlbum(album, album.source ?? state.activeSource)
                    }
                    onTrackClick={(track: SearchTrack) => void openSearchTrack(track)}
                    onPlaylistClick={(playlist: SearchPlaylist) => setPreviewPlaylist(playlist)}
                    onTrackPlay={(track: SearchTrack, row: LibraryCheckTrack | undefined) => {
                      if (row) playOwnedTrack(row);
                      else void streamSearchTrack(track);
                    }}
                    onVideoDownload={handleVideoAcquire}
                  />
                </div>
              ) : null}
            </section>
          ) : null}

          <div id="enhanced-dropdown" className={open ? undefined : 'hidden'}>
            {view === 'loading' ? (
              <div className={styles.state} id="enhanced-loading" role="status">
                <div className={styles.skeletons} aria-hidden="true">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <Skeleton
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
                {/* a search that hangs used to say "Searching Deezer…" for
                    as long as it took, two minutes once, with nothing else */}
                {loadingFor >= 8 ? (
                  <p className={styles.stateHint} role="status">
                    Taking longer than usual. SoulSync is busy, results will show as soon as it
                    answers.
                  </p>
                ) : null}
                {loadingFor >= 20 && !idLookupPending ? (
                  <button
                    type="button"
                    className={styles.stateRetry}
                    onClick={() => runSearch(query)}
                  >
                    Try again
                  </button>
                ) : null}
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
                videoProgress={videoProgressForGrid}
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
                onVideoDownload={handleVideoAcquire}
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

function filesSubtitle(source: BasicSource | undefined): string {
  // before the source list loads there's nothing to name, and guessing
  // soulseek is what put "from Soulseek" over deezer results (#1313)
  if (!source) return 'Raw files from your download sources, grabbed as they come';
  if (source.name === 'soulseek') return 'Raw files from Soulseek, grabbed exactly as shared';
  return `Files straight from ${basicSourceLabel(source)}, grabbed as they come`;
}

/** whole seconds since `active` turned true, 0 while it is false */
function useSecondsWhile(active: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    setSeconds(0);
    if (!active) return;
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return seconds;
}
