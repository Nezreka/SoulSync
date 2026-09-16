import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { SHELL_PROFILE_CONTEXT_CHANGED_EVENT } from '@/platform/shell/bridge';

import { profileKey, useProfileScope } from './-discover.profile-scope';

/**
 * The profile in the query key.
 *
 * Discover's shelves are personal, and its caches were keyed by nothing but
 * the endpoint name with infinite stale and gc times: switching profiles left
 * the previous profile's recommendations on screen until a reload.
 */

afterEach(() => {
  delete window.SoulSyncWebShellBridge;
});

function setProfile(profileId: number, isAdmin = true) {
  window.SoulSyncWebShellBridge = {
    getCurrentProfileContext: () => ({ profileId, isAdmin }),
  } as never;
}

describe('profileKey', () => {
  it('is a stable string, never undefined inside a key', () => {
    expect(profileKey(3)).toBe('3');
    expect(profileKey(null)).toBe('unscoped');
  });
});

describe('useProfileScope', () => {
  it('reads the shell profile at mount', () => {
    setProfile(4);
    const { result } = renderHook(() => useProfileScope());
    expect(result.current).toBe(4);
  });

  it('is null when the shell has not spoken — never a guessed 1', () => {
    const { result } = renderHook(() => useProfileScope());
    expect(result.current).toBeNull();
  });

  it('follows a profile switch, so the key changes with it', () => {
    setProfile(1);
    const { result } = renderHook(() => useProfileScope());
    expect(result.current).toBe(1);

    setProfile(2);
    act(() => {
      window.dispatchEvent(new CustomEvent(SHELL_PROFILE_CONTEXT_CHANGED_EVENT));
    });
    expect(result.current).toBe(2);
    expect(profileKey(result.current)).toBe('2');
  });

  it('stops listening when it unmounts', () => {
    setProfile(1);
    const { result, unmount } = renderHook(() => useProfileScope());
    unmount();
    setProfile(9);
    act(() => {
      window.dispatchEvent(new CustomEvent(SHELL_PROFILE_CONTEXT_CHANGED_EVENT));
    });
    expect(result.current).toBe(1);
  });
});
