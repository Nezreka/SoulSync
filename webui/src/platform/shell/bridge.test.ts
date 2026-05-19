import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ShellProfileContext } from './bridge';

import { SHELL_PROFILE_CONTEXT_CHANGED_EVENT, bindWindowWebRouter, waitForShellContext } from './bridge';

describe('waitForShellContext', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = undefined;
  });

  it('resolves immediately when the shell already has a profile', async () => {
    window.SoulSyncWebShellBridge = {
      getProfileHomePage: vi.fn(() => 'discover'),
      isPageAllowed: vi.fn(() => true),
      activateLegacyPath: vi.fn(),
      getCurrentProfileContext: vi.fn(() => ({ profileId: 2, isAdmin: true })),
      resolveLegacyPath: vi.fn(() => 'issues'),
      setActivePageChrome: vi.fn(),
      showReactHost: vi.fn(),
    } as NonNullable<typeof window.SoulSyncWebShellBridge>;

    await expect(waitForShellContext()).resolves.toEqual({
      bridge: window.SoulSyncWebShellBridge,
      profile: {
        profileId: 2,
        isAdmin: true,
      },
    });
  });

  it('waits for the legacy shell to publish profile context', async () => {
    const getCurrentProfileContext = vi.fn<() => ShellProfileContext | null>(() => null);
    window.SoulSyncWebShellBridge = {
      getProfileHomePage: vi.fn(() => 'discover'),
      isPageAllowed: vi.fn(() => true),
      activateLegacyPath: vi.fn(),
      getCurrentProfileContext,
      resolveLegacyPath: vi.fn(() => 'issues'),
      setActivePageChrome: vi.fn(),
      showReactHost: vi.fn(),
    } as NonNullable<typeof window.SoulSyncWebShellBridge>;

    const contextPromise = waitForShellContext();

    getCurrentProfileContext.mockReturnValue({ profileId: 5, isAdmin: false });
    window.dispatchEvent(new CustomEvent(SHELL_PROFILE_CONTEXT_CHANGED_EVENT));

    await expect(contextPromise).resolves.toEqual({
      bridge: window.SoulSyncWebShellBridge,
      profile: {
        profileId: 5,
        isAdmin: false,
      },
    });
  });
});

describe('bindWindowWebRouter', () => {
  it('navigates artist detail pages with source-aware URLs', async () => {
    const navigate = vi.fn().mockResolvedValue(undefined);

    bindWindowWebRouter({ navigate } as never);

    await window.SoulSyncWebRouter?.navigateToPage('artist-detail', {
      artistId: '2YZyLoL8N0Wb9xBt1NhZWg',
      artistSource: 'spotify',
    });

    expect(navigate).toHaveBeenCalledWith({
      href: '/artist-detail/spotify/2YZyLoL8N0Wb9xBt1NhZWg',
      replace: false,
    });
  });

  it('falls back artist detail URLs to library source when none is supplied', async () => {
    const navigate = vi.fn().mockResolvedValue(undefined);

    bindWindowWebRouter({ navigate } as never);

    await window.SoulSyncWebRouter?.navigateToPage('artist-detail', {
      artistId: '42',
      replace: true,
    });

    expect(navigate).toHaveBeenCalledWith({
      href: '/artist-detail/library/42',
      replace: true,
    });
  });

  it('refuses artist detail navigation without an artist id', async () => {
    const navigate = vi.fn().mockResolvedValue(undefined);

    bindWindowWebRouter({ navigate } as never);

    await expect(window.SoulSyncWebRouter?.navigateToPage('artist-detail', {} as never)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });
});
