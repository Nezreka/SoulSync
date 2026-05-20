import { vi } from 'vitest';

import type { ShellBridge, ShellPageId } from '@/platform/shell/bridge';

export function createShellBridge(overrides: Partial<ShellBridge> = {}): ShellBridge {
  const bridge: ShellBridge = {
    getCurrentProfileContext: vi.fn(() => ({ profileId: 2, isAdmin: true })),
    isPageAllowed: vi.fn(() => true),
    getProfileHomePage: vi.fn<() => ShellPageId>(() => 'discover'),
    resolveLegacyPath: vi.fn<(pathname: string) => ShellPageId | null>(() => 'search'),
    setActivePageChrome: vi.fn(),
    activateLegacyPath: vi.fn(),
    cancelSimilarArtistsLoad: vi.fn(),
    navigateToArtistDetail: vi.fn(),
    playLibraryTrack: vi.fn(),
    showReactHost: vi.fn(),
    startStream: vi.fn(),
    showLoadingOverlay: vi.fn(),
    hideLoadingOverlay: vi.fn(),
  };

  Object.assign(bridge, overrides);
  return bridge;
}
