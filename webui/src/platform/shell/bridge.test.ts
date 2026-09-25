import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createShellBridge } from '@/test/shell-bridge';

import type { ShellProfileContext } from './bridge';

import {
  SHELL_PROFILE_CONTEXT_CHANGED_EVENT,
  bindWindowWebRouter,
  getProfileHomePath,
  waitForShellContext,
} from './bridge';

describe('getProfileHomePath', () => {
  it('returns the profile home page when it is allowed', () => {
    const bridge = createShellBridge({
      getProfileHomePage: vi.fn(() => 'wishlist' as const),
    });

    expect(getProfileHomePath(bridge)).toBe('/wishlist');
  });

  it('never returns a home path the profile is not allowed to open (iss29-B10)', () => {
    // Every React route guard redirects here when it denies access. A home page
    // the profile may not open therefore sends the router straight back to the
    // page that just refused it. `library` is the reachable case: the legacy
    // `library-v2` page id normalizes to `library`, so a profile whose home is
    // the old id lands on a page its allowed_pages may well not contain.
    const bridge = createShellBridge({
      getProfileHomePage: vi.fn(() => 'library' as const),
      isPageAllowed: vi.fn((pageId) => pageId !== 'library'),
    });

    expect(getProfileHomePath(bridge)).not.toBe('/library');
  });

  it('falls back to a page every profile may open when nothing else is allowed', () => {
    const bridge = createShellBridge({
      getProfileHomePage: vi.fn(() => 'library' as const),
      isPageAllowed: vi.fn(() => false),
    });

    // `help` and `issues` are unconditionally permitted by the shell's own
    // gate, so one of them is always a truthful landing place.
    expect(['/help', '/issues']).toContain(getProfileHomePath(bridge));
  });
});

describe('waitForShellContext', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = undefined;
  });

  it('resolves immediately when the shell already has a profile', async () => {
    window.SoulSyncWebShellBridge = createShellBridge();

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
    window.SoulSyncWebShellBridge = createShellBridge({
      getCurrentProfileContext,
    });

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

  it('appends ?name= for sources with no numeric-ID lookup API', async () => {
    const navigate = vi.fn().mockResolvedValue(undefined);

    bindWindowWebRouter({ navigate } as never);

    await window.SoulSyncWebRouter?.navigateToPage('artist-detail', {
      artistId: '3957198221',
      artistSource: 'bandcamp',
      artistName: 'Radiohead',
    });

    expect(navigate).toHaveBeenCalledWith({
      href: '/artist-detail/bandcamp/3957198221?name=Radiohead',
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

    await expect(
      window.SoulSyncWebRouter?.navigateToPage('artist-detail', {} as never),
    ).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });
});
