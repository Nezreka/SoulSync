import type { ShellProfileContext, ShellRouteDefinition, ShellPageId } from './bridge';

declare global {
  interface Window {
    SoulSyncIssueActions?: {
      addToWishlist?: (albumId: string, artistName: string, albumName: string) => void;
      downloadAlbum?: (albumId: string, artistName: string, albumName: string) => void;
    };
    SoulSyncWebRouter?: {
      routeManifest: ShellRouteDefinition[];
      getCurrentPageId: () => ShellPageId | null;
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
      getCurrentPageId: () => ShellPageId;
      getCurrentProfileContext: () => ShellProfileContext;
      isPageAllowed: (pageId: ShellPageId) => boolean;
      getProfileHomePage: () => ShellPageId;
      resolveLegacyPath: (pathname: string) => ShellPageId | null;
      setActivePageChrome: (pageId: ShellPageId) => void;
      activateLegacyPath: (pathname: string) => void;
      showReactHost: (pageId: ShellPageId) => void;
    };
  }
}

export {};
