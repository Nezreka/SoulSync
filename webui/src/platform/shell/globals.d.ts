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
    clearEntireWishlist?: () => void;
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
