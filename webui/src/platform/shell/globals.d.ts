import type {
  DownloadMissingAlbumWorkflowInput,
  WishlistAlbumWorkflowInput,
} from '@/platform/workflows/album-workflows';
import type { IssueDomainBridge } from '@/routes/issues/-issues.types';

import type { ShellProfileContext, ShellRouteDefinition, ShellPageId } from './bridge';

declare global {
  interface Window {
    showToast?: (message: string, type?: string, durationOrContext?: number | string) => void;
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
      showReactHost: (pageId: ShellPageId) => void;
      navigateToArtistDetail: (
        artistId: string | number,
        artistName: string,
        sourceOverride?: string | null,
        options?: Record<string, unknown>,
      ) => void;
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
