import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ShellProfileContext } from './bridge';

import { SHELL_PROFILE_CONTEXT_CHANGED_EVENT, waitForShellContext } from './bridge';

describe('waitForShellContext', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = undefined;
  });

  it('resolves immediately when the shell already has a profile', async () => {
    window.SoulSyncWebShellBridge = {
      getProfileHomePage: vi.fn(() => 'discover'),
      isPageAllowed: vi.fn(() => true),
      activateLegacyPath: vi.fn(),
      getCurrentPageId: vi.fn(() => 'issues'),
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
      getCurrentPageId: vi.fn(() => 'issues'),
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
