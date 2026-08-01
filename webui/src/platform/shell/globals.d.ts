import type {
  DownloadMissingAlbumWorkflowInput,
  WishlistAlbumWorkflowInput,
} from '@/platform/workflows/album-workflows';
import type { IssueDomainBridge } from '@/routes/issues/-issues.types';

import type { ShellProfileContext, ShellRouteDefinition, ShellPageId } from './bridge';

declare global {
  interface Window {
    showToast?: (message: string, type?: string, durationOrContext?: number | string) => void;
    showConfirmDialog?: (options?: {
      title?: string;
      message?: string;
      confirmText?: string;
      cancelText?: string;
      destructive?: boolean;
    }) => Promise<boolean>;
    /**
     * Refreshes the watchlist nav badge and hero-button count.
     *
     * Owned by the vanilla shell (api-monitor.js) because those elements live
     * outside any React route — Library and Artist Detail call it too. The
     * React watchlist page calls it after a mutation for the same reason the
     * vanilla page did, and treats a failure as non-fatal.
     */
    updateWatchlistButtonCount?: () => void;
    /** Wishlist twin of updateWatchlistButtonCount — nav badge + hero count. */
    updateWishlistCount?: () => void;
    /**
     * Shared modals owned by other vanilla files and used from several pages
     * (origin-history.js, watchlist-history.js, blocklist.js). Declared as
     * top-level `function`s in classic scripts, so they are window properties.
     */
    openDownloadOriginsModal?: (tab: string) => void;
    /**
     * Wishlist -> search-page handoffs. These drive the VANILLA search page's
     * DOM (polling for the Soulseek source icon, filling #enhanced-search-input),
     * so they stay where they are rather than being reimplemented in React —
     * the same call they made from the vanilla wishlist page.
     */
    _searchWishlistTrackManually?: (artistName: string, trackName: string) => void;
    _navigateToArtistFromWishlist?: (artistName: string) => void;
    /**
     * The wishlist download flow. Cannot move to React: it reads
     * `activeDownloadProcesses` and `WishlistModalState`, both module-scoped in
     * core.js, to decide between rehydrating an in-flight batch and offering the
     * category choice. Reads #wishlist-stat-albums / #wishlist-stat-singles for
     * the counts in its dialog, so the React page renders those ids.
     */
    _nebulaDownload?: () => void | Promise<void>;
    /**
     * Writes the "Next Auto" line into #wishlist-next-auto-timer and self-cancels
     * via `wishlistCountdownInterval`. Also module-scope bound (socketConnected,
     * _lastWishlistStats), so it stays in downloads.js.
     */
    startWishlistCountdownTimer?: (currentCycle: string, initialSeconds: number) => void;
    openWishlistIgnoreModal?: () => void;
    cleanupWishlistOverview?: () => void;
    /**
     * The automation builder (create/edit) stays in stats-automations.js and is
     * deliberately NOT ported: showVideoAutomationBuilder opens the very same
     * builder with a video context, so a React copy would be a second
     * implementation of something the video page still needs. The React page
     * hands the shell over for the edit instead — see -automations.builder.ts.
     */
    showAutomationBuilder?: (automationId?: number) => void;
    /**
     * Closes the shared builder. Wrapped by the React automations page so it
     * can reclaim the shell — every exit path (Back, Cancel, Save) calls it.
     */
    hideAutomationBuilder?: () => void;
    /**
     * Builds the Automation Hub section (pipelines, recipes, guides, reference,
     * tips) and returns the node. Shared verbatim with the VIDEO automations
     * page, so React mounts what it returns rather than restating its content.
     */
    _buildAutomationHub?: () => HTMLElement;
    /**
     * The "Runs: N" run-history modal. Appends itself to document.body rather
     * than into the page container, so it works unchanged from the React page.
     */
    showAutomationHistory?: (
      automationId: number,
      automationName: string,
      actionType: string,
    ) => void;
    clearEntireWishlist?: () => void;
    /**
     * Library page handoffs, all owned by vanilla and INVOKED rather than
     * reimplemented:
     *   - the two export / watch-all modals live in library.js
     *   - the empty-state CTA drives the vanilla /search page's DOM
     *   - showLibraryDownloadsSection is bound to `artistDownloadBubbles`,
     *     module state in core.js, so it cannot move into a module
     *   - currentMusicSourceName decides which provider id makes an artist
     *     watchable
     */
    openArtistExportModal?: () => void;
    openWatchAllUnwatchedModal?: () => void;
    _handoffLibrarySearchToEnhancedSearch?: (query: string) => void;
    showLibraryDownloadsSection?: () => void;
    currentMusicSourceName?: string;
    updateWatchlistCount?: () => void;
    /** shared-helpers.js — drops JioSaavn entries unless the experimental
     *  source is enabled. Kept as the single source of truth for that flag. */
    filterJiosaavnServiceEntries?: <T>(items: T[], idKey?: string) => T[];
    /** core.js — points the shared IntersectionObserver at every [data-bg-src]
     *  inside a container. Cards render the attribute; without this call the
     *  artwork is never fetched and every tile stays blank. */
    observeLazyBackgrounds?: (container: Element | null) => void;
    /** core.js — reads the AudioDB logo off an existing img.audiodb-logo. */
    getAudioDBLogoURL?: () => string | null;
    /** stats-automations.js — reads artistDetailPageState for the artist id.
     *  Must be given the id explicitly once React owns the page. */
    playArtistRadio?: (artistId?: string | number, artistName?: string) => void;
    /** stats-automations.js — the parameterized radio core the Artist Web's
     *  "Play radio" hands off to (survives the discover.js deletion). */
    startArtistRadioById?: (artistId: string | number, artistName: string) => void | Promise<void>;
    /** sync-services.js — the WHOLE ListenBrainz playlist sync: fetch, virtual
     *  playlist, status polling into the discover-lb-playlist-<id>-sync-*
     *  spans. Shared (survives discover.js's deletion), so the React page
     *  calls it and renders the span block it writes into. */
    startListenBrainzPlaylistSync?: (identifier: string) => void | Promise<void>;
    /** library.js — artist photo picker, opened from the hero image. */
    openArtistArtPicker?: () => void;
    /** library.js — the Download Discography modal. */
    openDiscographyModal?: () => void;
    /** shared-helpers.js / core.js — similar-artists section + its abort. */
    loadSimilarArtists?: (artistName: string) => void;
    cancelSimilarArtistsLoad?: () => void;
    /** core.js — full-page loading overlay used while a release opens. */
    showLoadingOverlay?: (message?: string) => void;
    hideLoadingOverlay?: () => void;
    /** library.js — quality-enhance eligibility probe (library artists only). */
    checkArtistEnhanceEligibility?: (artistId: unknown) => void;
    /** stats-automations.js — the Enhance Quality modal, opened from the hero. */
    openEnhanceQualityModal?: () => void;
    /**
     * The Enhanced view's album actions. All of these still live in library.js
     * (showReportIssueModal in stats-automations.js) and are invoked through
     * window until the modals slice ports them; two of them take the button
     * element itself, because they render progress onto it.
     */
    openAlbumArtPicker?: (album: unknown) => void;
    openManualMatchModal?: (
      entityType: string,
      entityId: unknown,
      service: string,
      title: string,
      artistId: unknown,
    ) => void;
    runEnrichment?: (
      entityType: string,
      entityId: unknown,
      service: string,
      title: string,
      artistName: string,
      artistId: unknown,
    ) => void;
    writeAlbumTags?: (albumId: unknown) => void;
    analyzeAlbumReplayGain?: (albumId: unknown, button: HTMLElement) => void;
    showReorganizeModal?: (albumId: unknown) => void;
    redownloadLibraryAlbum?: (album: unknown, artistName: string, button: HTMLElement) => void;
    deleteLibraryAlbum?: (albumId: unknown) => void;
    showReportIssueModal?: (
      entityType: string,
      entityId: unknown,
      title: string,
      artistName: string,
      /** Track reports add the album name; album reports omit it. */
      albumName?: string,
    ) => void;
    /**
     * The Enhanced view's per-track actions, still in library.js. The mobile
     * popover keeps its underscore name: it is a private helper being called
     * across the boundary until the popover itself is ported.
     */
    showTagPreview?: (trackId: unknown) => void;
    analyzeTrackReplayGain?: (trackId: unknown, button: HTMLElement) => void;
    showTrackSourceInfo?: (track: unknown, button: HTMLElement) => void;
    openReidentifyModal?: (
      trackId: unknown,
      trackTitle: string,
      artistName: string,
      albumTitle: string,
      albumArt: string,
    ) => void;
    showTrackRedownloadModal?: (track: unknown, album: unknown) => void;
    deleteLibraryTrack?: (trackId: unknown, albumId: unknown) => void;
    openMissingTrackManageModal?: (track: unknown, album: unknown) => void;
    _showMobileTrackActions?: (track: unknown, album: unknown) => void;
    /**
     * library.js — the batch tag-preview modal. Safe to call across the
     * boundary because it takes the track ids explicitly rather than reading
     * the vanilla's selection state.
     */
    showBatchTagPreview?: (trackIds: unknown[], albumId: unknown) => void;
    /** library.js — polls the batch ReplayGain job this page just started. */
    _pollBatchRgStatus?: () => void;
    /** media-player.js — the play queue. */
    addToQueue?: (payload: unknown) => void;
    playNext?: (payload: unknown) => void;
    /**
     * shared-helpers.js — the download-missing modal. Called directly rather
     * than through the shell bridge because the bridge wrapper fixes the last
     * two arguments, and the top-tracks bulk download needs contextType
     * 'playlist' to render the playlist hero and route per-track album folders.
     */
    openDownloadMissingModalForArtistAlbum?: (
      virtualPlaylistId: string,
      playlistName: string,
      tracks: unknown[],
      album: unknown,
      artist: unknown,
      showLoadingOverlay?: boolean,
      contextType?: string,
    ) => void | Promise<void>;
    /**
     * core.js — shows the modal of an already-active download process.
     *
     * `activeDownloadProcesses` is a top-level `let` in a classic script, so it
     * is not a window property and no module can read it. The search page needs
     * the answer BEFORE fetching album detail: on a re-click while the source is
     * down, the fetch fails and the modal the user already had would never come
     * back. Returns true when a modal was shown.
     */
    /**
     * downloads.js:429 — the shared download-missing modal, YouTube-track
     * flavour. Discover's mixes, recent/seasonal/cache albums and the playlist
     * builder all hand their converted tracks to it; artist/album context is
     * optional and switches the modal into album mode.
     */
    openDownloadMissingModalForYouTube?: (
      virtualPlaylistId: string,
      playlistName: string,
      spotifyTracks: unknown[],
      artist?: unknown,
      album?: unknown,
    ) => void | Promise<void>;
    /** init.js:1465 — the My Accounts / personal settings modal. */
    openPersonalSettings?: () => void | Promise<void>;
    /**
     * The React download-bar store's PUBLISHED globals — the names downloads.js
     * (954, 1806) and core.js call. Assigned at module load in
     * -discover.use-download-bar.ts; module scripts run after classic scripts,
     * so these assignments replace the vanilla function declarations and every
     * bare cross-file call resolves to the one store.
     */
    discoverDownloads?: unknown;
    addDiscoverDownload?: (
      playlistId: string,
      playlistName: string,
      playlistType: string,
      imageUrl?: string | null,
    ) => void;
    removeDiscoverDownload?: (playlistId: string) => void;
    updateDiscoverDownloadBar?: () => void;
    hydrateDiscoverDownloadsFromSnapshot?: () => Promise<void>;
    /** core.js bridge: one discover download's process record, or null. */
    discoverDownloadProcess?: (
      virtualPlaylistId: string,
    ) => { status?: string; modalElement?: unknown; modalId?: string } | null;
    rehydrateDiscoverDownloadModal?: (virtualPlaylistId: string) => Promise<boolean>;
    reopenActiveDownloadModal?: (virtualPlaylistId: string) => boolean;
    /**
     * sync-spotify.js — hydrates listenbrainzPlaylistStates from the backend
     * (/api/listenbrainz/playlists). The vanilla discover init called this
     * (discover.js 244); without it the discovery flow's cross-restart
     * resume/rehydration silently finds no state.
     */
    loadListenBrainzPlaylistsFromBackend?: () => Promise<void>;
    /**
     * core.js bridge: the LB/Last.fm playlist DISCOVERY download flow, moved
     * verbatim from discover.js (3934-4137) with tracks as a parameter. It
     * rehydrates an in-flight session or seeds a fresh discovery and opens the
     * sync-services discovery modal — the exact vanilla behaviour.
     */
    openLbPlaylistDiscovery?: (
      identifier: string,
      title: string,
      tracks: unknown[],
    ) => Promise<void>;
    /** core.js bridge: seed a virtual playlist + tracks, start the shared sync. */
    startDiscoverVirtualSync?: (
      virtualPlaylistId: string,
      name: string,
      spotifyTracks: unknown[],
    ) => Promise<unknown>;
    /**
     * The basic-search results currently on screen, published for the vanilla
     * matched-download modal.
     *
     * `skipMatching` and the three `matchedDownload*` handlers in
     * wishlist-tools.js read this by INDEX — and one of them by `indexOf` on
     * the object — so it must be the same array the page renders, in the same
     * order, holding the same object references. Owned by the React basic
     * search controller; it goes away when that modal is ported too.
     */
    currentSearchResults?: unknown[];
    /**
     * wishlist-tools.js — opens the matched-download modal for a search result.
     *
     * `isAlbumDownload` drives whether the modal asks for an album selection;
     * `albumResult` is the album context a track came from, or null.
     */
    openMatchingModal?: (
      searchResult: unknown,
      isAlbumDownload?: boolean,
      albumResult?: unknown,
    ) => void;
    /**
     * Download a basic-search result the user declined to match.
     *
     * Set by the React search page and called by the still-vanilla
     * matched-download modal's "Skip Matching" button
     * (wishlist-tools.js:skipMatching). It exists because the call that used to
     * be there could not work: it looked the result up by index in an array
     * nothing populates, after the state holding the result had already been
     * cleared, and POSTed a route that does not exist.
     */
    _basicDownloadUnmatched?: (result: unknown) => void | Promise<void>;
    /**
     * wishlist-tools.js — groups quarantined entries that are alternative
     * candidates for the SAME track.
     *
     * Shared with the library-history quarantine tab, so the downloads page
     * calls it rather than reimplementing the rule; two copies of "are these
     * the same track" would drift apart.
     */
    _groupQuarantineEntries?: (entries: unknown[]) => { key: string | null; members: unknown[] }[];
    /**
     * core.js — opens the download modal for a batch on the Downloads page.
     *
     * Cannot move into React: it reads `activeDownloadProcesses`,
     * `rehydrateModal` and `WishlistModalState`, all script-scoped `let`s that
     * a module cannot reach. Moved there verbatim from _adlOpenBatchModal.
     */
    openDownloadBatchModal?: (batchId: string, playlistId: string, batchName: string) => void;
    /** wishlist-tools.js — relative time for a history row ("5h ago"). */
    formatHistoryTime?: (iso: string) => string;
    /** stats-automations.js — the per-download audit trail modal. */
    openDownloadAuditModal?: (entry: unknown) => void;
    /** library.js — the full download + import history modal. */
    openLibraryHistoryModal?: () => void;
    /** media-player.js — fills the player chrome before playback starts. */
    setTrackInfo?: (info: Record<string, unknown>) => void;
    showLoadingAnimation?: () => void;
    hideLoadingAnimation?: () => void;
    /** media-player.js — plays whatever the server just staged. */
    startAudioPlayback?: () => void | Promise<void>;
    /** media-player.js — starts streaming a search result in the player. */
    startStream?: (searchResult: unknown) => void | Promise<void>;
    /** media-player.js — 'flac' from 'a/b/c.flac'; '' when there is no extension. */
    getFileExtension?: (filename: string) => string;
    /** media-player.js — can this browser play that file at all? */
    isAudioFormatSupported?: (filename: string) => boolean;
    /**
     * shared-helpers.js — sends the user to the Settings card for a source that
     * has no credentials, rather than firing a search that cannot succeed.
     */
    openSettingsForSource?: (source: string) => void;
    /**
     * Set by the React search page so the global download widget can sync the
     * query BEFORE clicking the Soulseek icon. Without it the icon click hands
     * off whatever the search page last had, overwriting the widget's query.
     */
    _searchPageSetQuery?: (query: string) => void;
    /** shared-helpers.js — registers a download in the search bubbles. */
    registerSearchDownload?: (
      item: Record<string, unknown>,
      type: string,
      virtualPlaylistId: string,
      artistName?: string,
    ) => void;
    /** media-player.js — can this browser decode that file's format? */
    isAudioFormatSupported?: (filename: string) => boolean;
    getFileExtension?: (filename: string) => string;
    /**
     * shared-helpers.js — samples an image and hands back its palette, which
     * applyDynamicGlow turns into the card's shadow. Callback-style, not a
     * promise.
     */
    extractImageColors?: (imageUrl: string, callback: (colors: unknown) => void) => void;
    applyDynamicGlow?: (cardElement: HTMLElement, colors: unknown) => void;
    /**
     * search.js — the shared enhanced-search call. Label detail uses it to
     * re-resolve a MusicBrainz release onto a source whose images actually
     * load; Cover Art Archive does not.
     */
    enhancedSearchFetch?: (
      query: string,
      options?: Record<string, unknown>,
    ) => Promise<{
      albums?: { id?: string; name?: string; artist?: string; source?: string }[];
      metadata_source?: string;
    }>;
    /**
     * init.js — where the label-detail Back button returns to.
     *
     * navigateToLabelDetail records the page you came from, because raw
     * history.back() is unreliable through the SPA router.
     */
    _labelDetailReturnTo?: string;
    /** library.js — wires the hero watchlist button to an identity. */
    initializeLibraryWatchlistButton?: (artistId: unknown, artistName: string) => void;
    /** downloads.js — the Add to Wishlist modal, opened from a release card. */
    openAddToWishlistModal?: (
      album: unknown,
      artist: unknown,
      tracks: unknown[],
      albumType: unknown,
    ) => Promise<void> | void;
    /** shared-helpers.js — backfills per-track ownership behind the modal. */
    lazyLoadTrackOwnership?: (
      artistName: string,
      tracks: unknown[],
      card: unknown,
      albumName: unknown,
    ) => void;
    openWatchlistHistoryModal?: () => void;
    openBlocklistModal?: (initialType: string) => void;
    SoulSyncIssueDomain?: IssueDomainBridge;
    SoulSyncWorkflowActions?: {
      openDownloadMissingAlbum: (input: DownloadMissingAlbumWorkflowInput) => void | Promise<void>;
      openAddToWishlistAlbum: (input: WishlistAlbumWorkflowInput) => void | Promise<void>;
      notify?: (message: string, type?: string) => void;
    };
    SoulSyncWebRouter?: {
      routeManifest: ShellRouteDefinition[];
      getCurrentPath: () => string;
      resolvePageId: (pathname: string) => ShellPageId | null;
      navigateToPage: (
        pageId: ShellPageId,
        options?: {
          replace?: boolean;
          artistId?: string | number;
          artistSource?: string | null;
          artistName?: string;
          labelId?: string | number;
          labelName?: string;
        },
      ) => Promise<boolean>;
    };
    SoulSyncWebShellBridge?: {
      getCurrentProfileContext: () => ShellProfileContext | null;
      isPageAllowed: (pageId: ShellPageId) => boolean;
      getProfileHomePage: () => ShellPageId;
      resolveLegacyPath: (pathname: string) => ShellPageId | null;
      setActivePageChrome: (pageId: ShellPageId) => void;
      activateLegacyPath: (pathname: string) => void;
      navigateToArtistDetail: (
        artistId: string | number,
        artistName: string,
        sourceOverride?: string | null,
        options?: {
          skipRouteChange?: boolean;
        },
      ) => void;
      navigateToLabelDetail: (
        labelId: string,
        labelName: string,
        options?: {
          skipRouteChange?: boolean;
        },
      ) => void;
      cancelSimilarArtistsLoad: () => void;
      showReactHost: (pageId: ShellPageId) => void;
      playLibraryTrack: (
        track: {
          id: string | number;
          title: string;
          file_path: string;
          bitrate?: string | number | null;
          artist_id?: string | number | null;
          album_id?: string | number | null;
          _stats_image?: string | null;
        },
        albumTitle: string,
        artistName: string,
      ) => void | Promise<void>;
      startStream: (searchResult: Record<string, unknown>) => void | Promise<void>;
      showLoadingOverlay: (message?: string) => void;
      hideLoadingOverlay: () => void;
    };
  }
}

export {};
