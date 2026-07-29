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
