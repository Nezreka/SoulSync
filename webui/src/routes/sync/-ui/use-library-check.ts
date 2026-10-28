/**
 * Checks a mirror's unchecked tracks against the library when its detail
 * opens (#1910).
 *
 * The track statuses read an ownership answer only a sync used to write, so a
 * playlist pushed some other way said "not synced yet" on every track while
 * the server playlist held all of them. The check stamps the same answer the
 * sync does, a slice per call, then the detail refetches.
 *
 * One run per playlist at a time, shared across mounts: reopening the modal
 * mid-run doesn't start a second one.
 */

import { useEffect, useRef, useState } from 'react';

import { checkMirroredLibrary } from '../-sync.api';

/** each call checks for ~15s; 40 of them covers a few thousand tracks, past that the next open carries on */
export const LIBRARY_CHECK_MAX_ROUNDS = 40;

const running = new Map<number, Promise<void>>();

async function runCheck(playlistId: number): Promise<void> {
  for (let round = 0; round < LIBRARY_CHECK_MAX_ROUNDS; round++) {
    const res = await checkMirroredLibrary(playlistId);
    if (!res.success || !res.checked || !res.remaining) return;
  }
}

export function useLibraryCheck(
  playlistId: number,
  unchecked: number,
  onChecked: () => void,
): boolean {
  const [checking, setChecking] = useState(false);
  const onCheckedRef = useRef(onChecked);
  onCheckedRef.current = onChecked;
  const triedFor = useRef<number | null>(null);

  useEffect(() => {
    // once per open: a row the check couldn't settle stays unchecked, and
    // asking again on every refetch would loop
    if (unchecked <= 0 || triedFor.current === playlistId) return undefined;
    triedFor.current = playlistId;
    let alive = true;
    let run = running.get(playlistId);
    if (!run) {
      run = runCheck(playlistId)
        .catch(() => undefined)
        .finally(() => running.delete(playlistId));
      running.set(playlistId, run);
    }
    let settled = false;
    setChecking(true);
    void run.then(() => {
      settled = true;
      if (!alive) return;
      setChecking(false);
      onCheckedRef.current();
    });
    return () => {
      alive = false;
      // torn down mid-run (a strict-mode remount, a playlist switch): let the
      // next mount pick the same run back up instead of skipping it
      if (!settled) triedFor.current = null;
    };
  }, [playlistId, unchecked]);

  return checking;
}
