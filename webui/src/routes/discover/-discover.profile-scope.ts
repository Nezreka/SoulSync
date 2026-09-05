import { useEffect, useState } from 'react';

import {
  getShellProfileContext,
  SHELL_PROFILE_CONTEXT_CHANGED_EVENT,
} from '@/platform/shell/bridge';

/**
 * The active profile, as a query-key ingredient.
 *
 * Discover's caches were keyed by nothing but the endpoint name, with infinite
 * stale and gc times. Every shelf here is personal to a profile, so a switch
 * left the previous profile's answer on screen until a reload — and an in-flight
 * request begun before the switch could land afterwards and overwrite the new
 * profile's data.
 *
 * Putting the profile in the key fixes both: a switch is a different cache
 * entry, and a late response resolves into the key it was issued under.
 *
 * `null` means the shell has not told us yet (a standalone render, a test).
 * Callers treat that as "unscoped" rather than guessing profile 1.
 */
export function useProfileScope(): number | null {
  const [profileId, setProfileId] = useState<number | null>(
    () => getShellProfileContext()?.profileId ?? null,
  );

  useEffect(() => {
    const sync = () => setProfileId(getShellProfileContext()?.profileId ?? null);
    sync();
    window.addEventListener(SHELL_PROFILE_CONTEXT_CHANGED_EVENT, sync);
    return () => window.removeEventListener(SHELL_PROFILE_CONTEXT_CHANGED_EVENT, sync);
  }, []);

  return profileId;
}

/** The key fragment. Stable string so a key never contains `undefined`. */
export function profileKey(profileId: number | null): string {
  return profileId === null ? 'unscoped' : String(profileId);
}
