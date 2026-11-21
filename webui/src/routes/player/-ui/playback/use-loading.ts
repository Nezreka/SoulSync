/**
 * the words for what the player is doing right now (describe.ts), kept
 * current: a phase's words change with time ("still looking"), so this
 * ticks once a second while there's a phase, and not at all otherwise.
 */

import { useEffect, useState } from 'react';

import type { PlayerState } from '../../-engine/engine';

import { describe, type Description } from '../../-engine/describe';

export function useLoading(state: PlayerState): Description | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!state.phase) return;
    setNow(Date.now());
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [state.phase]);
  return describe(state, now);
}
