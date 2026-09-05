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
    _handoffLibrarySearchToEnhancedSearch?: (query: string) => void;
    showLibraryDownloadsSection?: () => void;
    currentMusicSourceName?: string;
    /**
     * library-globals.js — repaints the sidebar "Library / <Artist>"
     * breadcrumb. init.js calls it on every page change; the React artist page
     * calls it again once the payload lands, because the name it reads is only
     * synced after that.
     */
    _updateSidebarLibraryBreadcrumb?: () => void;
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
    /** media-player.js — seedless Library Radio: queues a ranked-random batch
     *  from the whole library and arms radio mode for refills. */
    startLibraryRadio?: () => void | Promise<void>;
    /** media-player.js — play a resolved library track list (radio-row shape)
     *  as the queue, labeled with a "Playing from" context. */
    cancelPendingPlayback?: () => void;
    playTrackList?: (
      tracks: unknown[],
      contextName?: string,
      options?: { isCurrent: () => boolean },
    ) =>
      | void
      | { status: string; error?: string }
      | Promise<void | { status: string; error?: string }>;
    /** sync-services.js — the WHOLE ListenBrainz playlist sync: fetch, virtual
     *  playlist, status polling into the discover-lb-playlist-<id>-sync-*
     *  spans. Shared (survives discover.js's deletion), so the React page
     *  calls it and renders the span block it writes into. */
    startListenBrainzPlaylistSync?: (identifier: string) => void | Promise<void>;
    /** sync-spotify.js's per-playlist LB sync states — read to tell a
     * dead Sync (state never hydrated, e.g. a fresh lastfm radio) from a
     * live one. */
    listenbrainzPlaylistStates?: Record<string, unknown>;
    /** library.js — artist photo picker, opened from the hero image. */
    openArtistArtPicker?: () => void;
    /** shared-helpers.js / core.js — similar-artists section + its abort. */
    loadSimilarArtists?: (artistName: string) => void;
    cancelSimilarArtistsLoad?: () => void;
    /** core.js — full-page loading overlay used while a release opens. */
    showLoadingOverlay?: (message?: string) => void;
    hideLoadingOverlay?: () => void;
    /** library.js — quality-enhance eligibility probe (library artists only). */
    /** stats-automations.js — the Enhance Quality modal, opened from the hero. */
    openEnhanceQualityModal?: () => void;
    /**
     * The Enhanced view's album actions. All of these still live in library.js
     * (showReportIssueModal in stats-automations.js) and are invoked through
     * window until the modals slice ports them; two of them take the button
     * element itself, because they render progress onto it.
     */
    openAlbumArtPicker?: (album: unknown) => void;
    /** library.js's shared page state, exported for the React artist-detail page. */
    artistDetailPageState?: { enhancedData?: unknown; [key: string]: unknown };
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
    showTrackSourceInfo?: (track: unknown, button: HTMLElement) => void;
    showTrackRedownloadModal?: (track: unknown, album: unknown) => void;
    deleteLibraryTrack?: (trackId: unknown, albumId: unknown) => void;
    openMissingTrackManageModal?: (track: unknown, album: unknown) => void;
    _showMobileTrackActions?: (track: unknown, album: unknown) => void;
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
     * shared-helpers.js 3390 — registers a Beatport download so its bubble
     * appears in `#beatport-downloads-section`.
     *
     * It writes into `beatportDownloadBubbles`, a top-level `let` in core.js
     * (555), so no module can maintain that registry itself — the React
     * Beatport tab must call this and must keep the section div for it to
     * render into.
     */
    registerBeatportDownload?: (
      chartName: string,
      chartImage: string,
      virtualPlaylistId: string,
    ) => void;
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
     * THE app-wide download flow for an ACCOUNT playlist — sync-spotify.js
     * 2193. Twelve call sites across both worlds; the Spotify and Deezer-ARL
     * tabs hand off to it rather than reimplementing any of it.
     *
     * It reads the playlist out of the script-scoped `spotifyPlaylists` array,
     * which is why an ARL playlist must be shimmed into that array first
     * (sync-services.js 2646-2654) — see arlShimRow in -sync.accounts.ts.
     */
    openDownloadMissingModal?: (playlistId: string) => Promise<void> | void;
    /**
     * core.js — seed `spotifyPlaylists` so openDownloadMissingModal can find a
     * playlist React rendered. That array is a top-level `let` (core.js:33), so
     * a module cannot push to it; this is the same bridge shape as
     * startDiscoverVirtualSync, which the discover port added for this exact
     * trap. Idempotent by id.
     */
    registerSyncAccountPlaylist?: (row: {
      id: string;
      name?: string;
      track_count?: number;
      image_url?: string;
      owner?: string;
    }) => void;
    /**
     * The account sync engine's two card writers (downloads.js 4139 / 3969).
     * The Deezer-ARL tab calls BOTH on load for any playlist the backend
     * reports mid-sync (sync-services.js 2473-2474), which is how a sync
     * started before the page loaded keeps painting.
     *
     * They write `#progress-<id>` and the card's status/action nodes directly,
     * so the React card must render those ids and then leave them alone.
     */
    updateCardToSyncing?: (playlistId: string, percent: number, progress?: unknown) => void;
    startSyncPolling?: (playlistId: string) => void;
    /**
     * The generic engine entry (misnamed ForTidal) that tidal/qobuz/deezer/
     * spotify-public/itunes use — sync-services.js 1312. Unlike the YouTube
     * one it takes options and hydrates the organize preference (1494).
     */
    openDownloadMissingModalForTidal?: (
      virtualPlaylistId: string,
      playlistName: string,
      spotifyTracks: unknown[],
      options?: { forcePlaylistFolder?: boolean },
    ) => Promise<void> | void;
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
    /**
     * shared-helpers.js — the per-playlist quality-profile <select>. The
     * Auto-Sync board renders it through the same `typeof === 'function'`
     * guard the vanilla uses (auto-sync.js 1927-1929), so an absent global
     * simply yields no select. `compact` is the third argument.
     */
    /**
     * stats-automations.js 4154-4186 — the trigger-label formatter. The port
     * reimplements it; this is consulted only for the block-definition branch
     * it cannot reproduce. See autoSyncFormatTrigger.
     */
    _autoFormatTrigger?: (type: string, config: unknown) => string;
    /**
     * downloads.js — runs a mirrored playlist through the pipeline engine.
     * The engine stays vanilla for this port, so Auto-Sync's 'Run now' calls
     * across to it (auto-sync.js 2336).
     */
    runMirroredPlaylistPipeline?: (
      playlistId: number,
      playlistName: string,
    ) => Promise<void> | void;
    /**
     * manual-library-match.js — the Library Match tool. Stays vanilla: its
     * file is untouched by the sync flip (index.html header button, 2239).
     */
    openManualLibraryMatchTool?: () => void;
    /** wishlist-tools.js — the Sync History modal (index.html 2240). */
    openSyncHistoryModal?: () => void;
    /**
     * origin-history.js — the Download Origins modal. The sync page passes the
     * literal 'playlist'; the modal is shared and filters on it (2241).
     */
    openDownloadOriginsModal?: (scope: string) => void;
    playlistQualityProfileSelectHtml?: (
      playlistId: string | number | undefined,
      source: string | undefined,
      compact?: boolean,
    ) => string;
    /**
     * shared-helpers.js — fills those selects with the real profile list after
     * the markup lands (auto-sync.js 1089-1096).
     */
    hydratePlaylistQualityProfileSelects?: (
      playlistId: string | number | undefined,
      source: string | undefined,
      currentProfileId?: string | number | null,
    ) => Promise<void> | void;
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
    /** Repaints the search download bubbles from the vanilla bubble store into
     *  #enhanced-main-results-area (shared-helpers.js). The React search page
     *  calls it on mount because it recreates that container each visit. */
    showSearchDownloadBubbles?: () => void;
    /** Active profile's display name, mirrored by init.js setCurrentProfile
     *  (the script-scoped `currentProfile` is unreachable from modules).
     *  Updated on profile switch alongside ss:webui-profile-context-changed. */
    _currentProfileName?: string;
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
    /** downloads.js — the Add to Wishlist modal, opened from a release card. */
    openAddToWishlistModal?: (
      album: unknown,
      artist: unknown,
      tracks: unknown[],
      albumType: unknown,
      /** Per-track ownership map; the missing-track flow passes {name: false}. */
      ownedMap?: Record<string, boolean>,
    ) => Promise<void> | void;
    /** downloads.js — "Download Now" inside the wishlist modal just opened. */
    handleWishlistDownloadNow?: () => void;
    /** shared-helpers.js — records the album under Active Artist Downloads. */
    registerArtistDownload?: (
      artist: unknown,
      album: unknown,
      virtualPlaylistId: string,
      albumType: string,
    ) => void;
    /** shared-helpers.js — backfills per-track ownership behind the modal. */
    lazyLoadTrackOwnership?: (
      artistName: string,
      tracks: unknown[],
      card: unknown,
      albumName: unknown,
    ) => void;
    /**
     * stats-automations.js — opens the mirrored-playlist discovery modal.
     *
     * The explorer's Discover button calls it when it exists and falls back to
     * sending the user to the Sync page's mirrored tab when it does not, which
     * is exactly what the vanilla explorer did (it was already typeof-guarded
     * there).
     */
    discoverMirroredPlaylist?: (playlistId: number) => void | Promise<void>;
    openWatchlistHistoryModal?: () => void;
    openBlocklistModal?: (initialType: string) => void;
    /** the rest of the blocklist modal surface - lives in src/shell since the
     * aug 26 TS port; inline onclick handlers in its generated HTML use these. */
    closeBlocklistModal?: () => void;
    switchBlocklistTab?: (type: string) => void;
    onBlocklistSearchInput?: () => void;
    blockFromSearch?: (payloadEnc: string) => Promise<void>;
    unblockEntry?: (id: number) => Promise<void>;
    /** shared-helpers.js html escaper (also re-declared by downloads.js) */
    escapeHtml?: (text: unknown) => string;
    /** init.js - the active profile, or null before profiles load */
    getCurrentProfileContext?: () => ShellProfileContext | null;
    /** shared-helpers.js - refresh the sidebar service dots */
    fetchAndUpdateServiceStatus?: () => void;
    /** src/shell ports (aug 26): origin-history, watchlist-history (already
     * declared piecemeal below where react used them), my-accounts,
     * service-switch - inline onclick handlers in their generated markup */
    closeDownloadOriginsModal?: () => void;
    switchDownloadOriginTab?: (tab: string) => void;
    toggleOriginGroup?: (btn: HTMLElement) => void;
    toggleOriginEntry?: (id: number, on: boolean) => void;
    toggleAllOriginEntries?: (on: boolean) => void;
    deleteSelectedOriginEntries?: (singleId?: number) => Promise<void>;
    openMyAccountsModal?: () => void;
    closeMyAccountsModal?: () => void;
    connectMyAccount?: (serviceId: string) => void;
    saveMyAccountToken?: (serviceId: string) => Promise<void>;
    disconnectMyAccount?: (serviceId: string) => Promise<void>;
    openServiceSwitchModal?: (tab?: string) => void;
    closeServiceSwitchModal?: () => void;
    switchServiceSwitchTab?: (tab: string) => void;
    setActiveSource?: (kind: string, id: string) => Promise<void>;
    setDownloadMode?: (which: string) => Promise<void>;
    closeWatchlistHistoryModal?: () => void;
    toggleWatchlistHistoryRun?: (runId: string, btn: HTMLElement) => Promise<void>;
    /** src/shell ports (aug 26, batch 3): library-globals, track-detail,
     * manual-library-match, server-activity */
    artistDetailBackLabels?: Record<string, string>;
    artistDetailLabelStack?: Array<
      { type: 'page'; pageId: string } | { type: 'artist'; name: string }
    >;
    navigateToArtistDetail?: (
      artistId: string | number,
      artistName: string,
      sourceOverride?: string | null,
      options?: { skipRouteChange?: boolean },
    ) => void;
    playLibraryTrack?: (
      track: Record<string, unknown>,
      albumTitle?: string,
      artistName?: string,
    ) => Promise<void>;
    clearArtistDetailPageState?: () => void;
    openTrackDetail?: (taskId: string) => Promise<void>;
    closeTrackDetail?: () => void;
    _mlmClose?: () => void;
    _mlmSourceDebounce?: (q: string) => void;
    _mlmLibraryDebounce?: (q: string) => void;
    _mlmSelectSource?: (idx: number) => void;
    _mlmSelectLibrary?: (idx: number) => void;
    _mlmSaveMatch?: () => Promise<void>;
    _mlmDeleteMatch?: (id: number) => Promise<void>;
    ServerActivity?: {
      toggle: () => void;
      open: () => void;
      close: () => void;
      refresh: () => Promise<unknown>;
      _onSocket: (d: unknown) => void;
      _wantsLive: () => boolean;
    };
    SoulSyncActivitySocket?: {
      isConnected: () => boolean;
      subscribe: () => void;
      unsubscribe: () => void;
    };
    SoulSyncVideo?: {
      openDetail?: (args: { kind: string; id: number | string; source?: string }) => void;
    };
    SoulSyncIssueDomain?: IssueDomainBridge;
    SoulSyncWorkflowActions?: {
      openDownloadMissingAlbum: (input: DownloadMissingAlbumWorkflowInput) => void | Promise<void>;
      openAddToWishlistAlbum: (input: WishlistAlbumWorkflowInput) => void | Promise<void>;
      notify?: (message: string, type?: string) => void;
    };
    /** init.js — the performance switches' appliers, the exact functions the
     *  Settings checkboxes call (body classes + canvas loops + localStorage).
     *  The dashboard's quick-settings strip reuses them so both surfaces stay
     *  in perfect agreement. Max performance overrides Reduce effects. */
    applyReduceEffects?: (enabled: boolean) => void;
    applyMaxPerformance?: (enabled: boolean) => void;
    /** init.js:43 — recomputes the --accent[-light|-neon]-rgb custom
     *  properties app-wide and persists the per-device localStorage copy. */
    applyAccentColor?: (hex: string) => void;
    /** init.js:104/126 — the other two per-device appearance switches
     *  (canvas particles / header worker orbs), same applier contract. */
    applyParticlesSetting?: (enabled: boolean) => void;
    applyWorkerOrbsSetting?: (enabled: boolean) => void;
    /** init.js:3256 — THE cross-page navigation entry: permission guard,
     *  sidebar chrome (setActivePageChrome), currentPage bookkeeping, then
     *  the router. React components navigating BETWEEN pages must call
     *  this, not the raw SoulSyncWebRouter bridge below — the bridge moves
     *  the URL but leaves the sidebar marking the old page. */
    navigateToPage?: (
      pageId: string,
      options?: Record<string, unknown>,
    ) => boolean | Promise<boolean> | void;
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
      /**
       * Navigate to a full in-app href, query string included.
       *
       * `navigateToPage` addresses a page by id and cannot carry search params,
       * so a plain `<a href="/library?artist=7">` — which is what a search
       * result card is — had no way in and fell through to the browser as a
       * full document load (iss29-B03).
       */
      navigateToHref: (href: string, options?: { replace?: boolean }) => Promise<boolean>;
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
          id?: string | number | null;
          lib2_track_id?: string | number | null;
          legacy_track_id?: string | number | null;
          server_track_id?: string | number | null;
          title: string;
          file_path: string;
          bitrate?: string | number | null;
          artist_id?: string | number | null;
          /**
           * iss29-B08: the LIB2 artist id, when the track came from Library V2.
           * `artist_id` above is a legacy id and is correctly null for a
           * V2-native track — which left the player's "Go to artist" button
           * permanently disabled during V2 playback, because nothing routed to
           * `/library?artist=`.
           */
          lib2_artist_id?: string | number | null;
          album_id?: string | number | null;
          _stats_image?: string | null;
          /** Play this exact file: skip the title+artist re-resolve. */
          exact_path?: boolean;
        },
        albumTitle: string,
        artistName: string,
      ) => void | Promise<void>;
      startStream: (searchResult: Record<string, unknown>) => void | Promise<void>;
      showLoadingOverlay: (message?: string) => void;
      hideLoadingOverlay: () => void;
    };
    /**
     * Tools page — modals that stay VANILLA and are opened, not reimplemented.
     *
     * Each one is either self-contained in its own file, shared with another
     * page, or backed by markup that lives outside #tools-page:
     *
     * - openToolHelpModal reads TOOL_HELP_CONTENT and drives #tool-help-modal,
     *   which sits outside the tools region in index.html
     * - openDiscoveryPoolModal is in stats-automations.js and is also opened
     *   from the sync page's per-playlist menu. Its Wing It twin
     *   (openWingItPoolModal) is sync-page-only but lives in the same file and
     *   shares the whole fix/rematch flow, so it is adopted the same way — the
     *   React sync tab OPENS both; the flip must not delete either.
     * - openManualLibraryMatchTool is a self-contained file also opened from
     *   the sync-history markup, so it cannot move with this page
     * - openConfigExportModal is a self-contained IIFE covering BOTH sides
     *   (music config + video settings)
     * - openMetadataCacheModal drives #mcache-browse-modal, also outside the
     *   tools region
     * - openCacheHealthModal is in enrichment.js and is opened from the repair
     *   findings dashboard as well as the Metadata Cache card
     */
    openToolHelpModal?: (toolId: string) => void;
    openDiscoveryPoolModal?: (playlistId?: string | null) => void;
    openWingItPoolModal?: (playlistId?: string | null) => void;
    openManualLibraryMatchTool?: (prefill?: string) => void;
    openConfigExportModal?: () => void;
    openMetadataCacheModal?: () => void;
    openCacheHealthModal?: () => void;
    openBlacklistModal?: () => void;
    /**
     * Dashboard header seams, all owned by vanilla and INVOKED:
     * - openEnrichmentManager (enrichment-manager.js) — the Manage Workers
     *   modal, self-contained with markup outside the dashboard region
     * - openRepairModal (enrichment.js) — the repair orb's click: navigate to
     *   tools + scroll to the maintenance hero (kept through the tools flip
     *   exactly for this)
     * - isJiosaavnExperimentalEnabled (shared-helpers.js) — the JioSaavn orb's
     *   initial visibility; live toggles arrive via ss:jiosaavn-experimental
     * - openWishlistFromHero (init.js) — the wishlist hero button's
     *   fast/slow-path behaviour; reads activeDownloadProcesses /
     *   WishlistModalState / rehydrateModal, all script-scoped `let`s no
     *   module can reach
     */
    openEnrichmentManager?: () => void;
    openRepairModal?: () => void;
    isJiosaavnExperimentalEnabled?: () => boolean;
    openWishlistFromHero?: () => void | Promise<void>;
    /**
     * P5 seams:
     * - testDashboardConnection (settings.js) — the service cards' Test
     *   buttons: overlay + POST + toast + status refresh, all vanilla
     * - getActiveMetadataSource (core.js) — _lastStatusPayload's source with
     *   the 'spotify' fallback; the metadata Test button's pre-payload target
     * - switchSettingsTab (settings.js) — the chips' click-to-configure path
     * - isBandcampExperimentalEnabled (shared-helpers.js) — chip filtering
     * - _openRateModal (api-monitor.js) — the equalizer bars' detail modal;
     *   appends to document.body and reads its own _rateMonitorState
     * - _reduceEffectsActive/_maxPerfActive (init.js) — the performance
     *   switches that suppress the equalizer's ember particles
     */
    testDashboardConnection?: (service: string) => void | Promise<void>;
    getActiveMetadataSource?: () => string;
    switchSettingsTab?: (tab: string) => void;
    isBandcampExperimentalEnabled?: () => boolean;
    _openRateModal?: (serviceKey: string) => void;
    _reduceEffectsActive?: boolean;
    _maxPerfActive?: boolean;
    /** core.js's mirror of its script-scoped `socketConnected` — the React
     *  fallback pollers apply the same skip-while-socket-pushes gate the
     *  vanilla poller twins do. Kept in lockstep at every write site. */
    _socketConnected?: boolean;
    /**
     * P7 seams:
     * - openAutoSyncScheduleModal (auto-sync.js) — the Quick Actions hero tile
     * - openSyncDetailModal (pages-extra.js) — a sync card's detail modal;
     *   appends to document.body
     * - showLoginScreen / showLaunchPinScreen (init.js) — the unlock screens a
     *   lapsed session surfaces from the sync-history 401 path
     * - checkForActiveProcesses (sync-spotify.js) — rehydrates the download
     *   bubble registries from /api/active-processes
     * - updateDashboardDownloads (wishlist-tools.js) — renders the Active
     *   Downloads ADOPTED REGION from the four script-scoped bubble
     *   registries; React provides only the shell
     */
    /**
     * worker-orbs.js — the canvas orb layer. Published unconditionally at
     * script load; setPage('dashboard') lazily re-anchors against the
     * React-rendered header (the page component re-pings it post-mount).
     */
    workerOrbs?: {
      setPage: (pageId: string) => void;
      onStatus: (id: string, data: unknown) => void;
    };
    openAutoSyncScheduleModal?: () => void | Promise<void>;
    /** auto-sync.js:471 — the schedule board's state builder; the dashboard's
     *  Auto Sync card reuses it so schedule semantics live in one place. */
    buildAutoSyncScheduleState?: (
      playlists: unknown[],
      automations: unknown[],
      historyData?: Record<string, unknown>,
    ) => Record<string, unknown>;
    openSyncDetailModal?: (entryId: number) => void | Promise<void>;
    showLoginScreen?: () => void;
    showLaunchPinScreen?: () => void;
    checkForActiveProcesses?: () => Promise<void>;
    updateDashboardDownloads?: () => void;

    /* ── The sync page's download-engine interface (downloads.js / core.js) ──
     * The engine stays vanilla across the flip; the React page injects these
     * as a typed object rather than reaching for them at each call site.
     * `isPlaylistSyncing` and `getSyncAccountPlaylists` are ACCESSORS added for
     * the port — `activeSyncPollers` and `spotifyPlaylists` are top-level
     * `let`s, which create no window property at all. */
    startPlaylistSync?: (playlistId: string) => Promise<void> | void;
    isPlaylistSyncing?: (playlistId: string) => boolean;
    disablePlaylistSelection?: (disabled: boolean) => void;
    updateRefreshButtonState?: () => void;
    getSyncAccountPlaylists?: () => { id: string | number; name?: string }[];
    /** Ask the tools page to show whichever tab holds `selector`. True when it
     *  had to switch, so the caller knows to wait a frame before measuring. */
    revealToolsTabFor?: (selector: string) => boolean;
  }
}

export {};
