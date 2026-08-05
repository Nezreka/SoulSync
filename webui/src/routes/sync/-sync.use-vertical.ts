/**
 * The source-vertical controller — ONE hook for what the vanilla implements
 * nine times (startXDiscoveryPolling / startXSyncPolling / handleXCardClick
 * plumbing in sync-services.js). Which source it drives comes entirely from
 * the config; the drift catalog lives there, not here.
 *
 * Transport normalization (declared in -sync.sources.ts): discovery frames
 * arrive via the ss:discovery-progress CustomEvent (core.js re-broadcasts
 * every socket frame there, core.js:878) plus an HTTP backstop poll at the
 * source's vanilla cadence. The vanilla's three per-source poll policies
 * collapse into event + backstop — the guaranteed path is the poll, the event
 * makes it live. Sync progress is HTTP-poll only, the discover-port precedent
 * (its socket frames are not re-broadcast for modules).
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { SourceVerticalConfig } from './-sync.sources';
import type { DiscoveryPayload, SourcePlaylistState } from './-sync.state';

import {
  cancelSourceSync,
  fetchSourceDiscoveryStatus,
  fetchSourceState,
  fetchSourceSyncStatus,
  startSourceDiscovery,
  startSourceSync,
  updateSourcePhase,
} from './-sync.api';
import {
  applyDiscovery,
  applySyncStarted,
  applySyncStatus,
  freshSourceState,
  fromBackendState,
  resetAfterModalClose,
} from './-sync.state';

/** The window event core.js mirrors each discovery:progress frame onto. */
export const SYNC_DISCOVERY_EVENT = 'ss:discovery-progress';

export interface SourceVertical {
  /** Per-playlist state, keyed by the SOURCE's own id (not the fake hash). */
  states: Record<string, SourcePlaylistState>;
  /** Register a playlist so frames/polls for it are recognised. */
  seed: (sourceId: string, playlist?: Record<string, unknown>) => void;
  /** Replace a playlist's state from a backend state payload. */
  hydrate: (sourceId: string, backend: Record<string, unknown>) => void;
  /**
   * Start discovery: optimistic 'discovering' (the #867/YT pattern — the card
   * flips before the backend acks), revert to fresh on error, then event +
   * backstop poll until complete.
   */
  startDiscovery: (sourceId: string, body?: unknown) => Promise<void>;
  /** Resume polling for a playlist already discovering (rehydration). */
  resumeDiscovery: (sourceId: string) => void;
  /** Start the source sync engine and poll it to a terminal phase. */
  startSync: (sourceId: string) => Promise<void>;
  resumeSync: (sourceId: string) => void;
  /** Cancel: POST (LB rides the youtube endpoint via config), revert to discovered. */
  cancelSync: (sourceId: string) => Promise<void>;
  /**
   * The unified close-reset: from sync_complete/download_complete the modal
   * close reverts to discovered locally AND writes the phase back (the
   * config's update-phase endpoint, hyphen drift included). Other phases: no-op,
   * matching closeYouTubeDiscoveryModal's gate (10237).
   */
  closeModalReset: (sourceId: string) => Promise<void>;
}

export function useSourceVertical(config: SourceVerticalConfig): SourceVertical {
  const [states, setStates] = useState<Record<string, SourcePlaylistState>>({});
  const statesRef = useRef(states);
  statesRef.current = states;

  const discoveryPollers = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  const syncPollers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  const patch = useCallback(
    (sourceId: string, fn: (s: SourcePlaylistState) => SourcePlaylistState) => {
      setStates((current) => ({
        ...current,
        [sourceId]: fn(current[sourceId] ?? freshSourceState(config, sourceId)),
      }));
    },
    [config],
  );

  const stopDiscoveryPoll = useCallback((sourceId: string) => {
    const poller = discoveryPollers.current[sourceId];
    if (poller !== undefined) clearInterval(poller);
    delete discoveryPollers.current[sourceId];
  }, []);

  const stopSyncPoll = useCallback((sourceId: string) => {
    const poller = syncPollers.current[sourceId];
    if (poller !== undefined) clearInterval(poller);
    delete syncPollers.current[sourceId];
  }, []);

  /* ── Discovery frames (the socket path, via the ss:* bridge) ────────────── */

  useEffect(() => {
    const onFrame = (event: Event) => {
      const frame = (event as CustomEvent<Record<string, unknown>>).detail ?? {};
      const id =
        typeof frame.id === 'string' || typeof frame.id === 'number' ? String(frame.id) : '';
      // Frame ids are the identifiers the verticals subscribe with — the
      // source's own id (623 tidal, 4725 beatport, 11026 LB...). Anything not
      // seeded here belongs to another source or page.
      if (!(id in statesRef.current)) return;
      if (frame.error) {
        stopDiscoveryPoll(id);
        return;
      }
      patch(id, (s) => applyDiscovery(s, config, frame as DiscoveryPayload));
      if (frame.complete || frame.phase === 'discovered') stopDiscoveryPoll(id);
    };
    window.addEventListener(SYNC_DISCOVERY_EVENT, onFrame);
    return () => window.removeEventListener(SYNC_DISCOVERY_EVENT, onFrame);
  }, [config, patch, stopDiscoveryPoll]);

  /* ── The HTTP backstop ──────────────────────────────────────────────────── */

  const startDiscoveryPoll = useCallback(
    (sourceId: string) => {
      stopDiscoveryPoll(sourceId);
      discoveryPollers.current[sourceId] = setInterval(async () => {
        try {
          const status = await fetchSourceDiscoveryStatus(config, sourceId);
          if (status.error) {
            stopDiscoveryPoll(sourceId);
            return;
          }
          patch(sourceId, (s) => applyDiscovery(s, config, status));
          if (status.complete || status.phase === 'discovered') stopDiscoveryPoll(sourceId);
        } catch {
          stopDiscoveryPoll(sourceId);
        }
      }, config.discovery.pollMs);
    },
    [config, patch, stopDiscoveryPoll],
  );

  const startSyncPoll = useCallback(
    (sourceId: string) => {
      stopSyncPoll(sourceId);
      syncPollers.current[sourceId] = setInterval(async () => {
        try {
          const status = await fetchSourceSyncStatus(config, sourceId);
          if (status.error) {
            stopSyncPoll(sourceId);
            return;
          }
          patch(sourceId, (s) => applySyncStatus(s, status));
          const terminal =
            status.complete ||
            status.status === 'finished' ||
            status.status === 'error' ||
            status.status === 'cancelled' ||
            status.sync_status === 'error' ||
            status.sync_status === 'cancelled';
          if (terminal) stopSyncPoll(sourceId);
        } catch {
          stopSyncPoll(sourceId);
        }
      }, config.sync.pollMs);
    },
    [config, patch, stopSyncPoll],
  );

  /* ── Actions ────────────────────────────────────────────────────────────── */

  const seed = useCallback(
    (sourceId: string, playlist?: Record<string, unknown>) => {
      patch(sourceId, (s) => (playlist ? { ...s, playlist } : s));
    },
    [patch],
  );

  const hydrate = useCallback(
    (sourceId: string, backend: Record<string, unknown>) => {
      setStates((current) => ({
        ...current,
        [sourceId]: fromBackendState(config, sourceId, backend),
      }));
    },
    [config],
  );

  const startDiscovery = useCallback(
    async (sourceId: string, body?: unknown) => {
      patch(sourceId, (s) => ({ ...s, phase: 'discovering' }));
      try {
        const response = await startSourceDiscovery(config, sourceId, body);
        if (response.error) {
          // The beatport/LB pattern: revert to fresh on a failed start (4700, 11304).
          patch(sourceId, (s) => ({ ...s, phase: 'fresh' }));
          return;
        }
      } catch {
        patch(sourceId, (s) => ({ ...s, phase: 'fresh' }));
        return;
      }
      startDiscoveryPoll(sourceId);
    },
    [config, patch, startDiscoveryPoll],
  );

  const resumeDiscovery = useCallback(
    (sourceId: string) => startDiscoveryPoll(sourceId),
    [startDiscoveryPoll],
  );

  const startSync = useCallback(
    async (sourceId: string) => {
      const response = await startSourceSync(config, sourceId);
      if (response.error) return;
      patch(sourceId, (s) => applySyncStarted(s, response));
      startSyncPoll(sourceId);
    },
    [config, patch, startSyncPoll],
  );

  const resumeSync = useCallback((sourceId: string) => startSyncPoll(sourceId), [startSyncPoll]);

  const cancelSync = useCallback(
    async (sourceId: string) => {
      stopSyncPoll(sourceId);
      try {
        await cancelSourceSync(config, sourceId);
      } catch {
        // The vanilla reverts locally even when the cancel POST fails (1112ff).
      }
      patch(sourceId, (s) => ({ ...s, phase: 'discovered' }));
    },
    [config, patch, stopSyncPoll],
  );

  const closeModalReset = useCallback(
    async (sourceId: string) => {
      const state = statesRef.current[sourceId];
      if (!state) return;
      if (state.phase !== 'sync_complete' && state.phase !== 'download_complete') return;
      patch(sourceId, resetAfterModalClose);
      try {
        await updateSourcePhase(config, sourceId, { phase: 'discovered' });
      } catch {
        // Best-effort, as in the vanilla reset blocks.
      }
    },
    [config, patch],
  );

  /* ── Lifecycle ──────────────────────────────────────────────────────────── */

  useEffect(() => {
    const discovery = discoveryPollers.current;
    const sync = syncPollers.current;
    return () => {
      for (const poller of Object.values(discovery)) clearInterval(poller);
      for (const poller of Object.values(sync)) clearInterval(poller);
    };
  }, []);

  return {
    states,
    seed,
    hydrate,
    startDiscovery,
    resumeDiscovery,
    startSync,
    resumeSync,
    cancelSync,
    closeModalReset,
  };
}

/**
 * Fetch + hydrate one playlist's full backend state on demand — the
 * 'discovered'-with-empty-results fallback in handleTidalCardClick (128-228)
 * and its clones.
 */
export async function fetchAndHydrateState(
  config: SourceVerticalConfig,
  sourceId: string,
  hydrate: (sourceId: string, backend: Record<string, unknown>) => void,
): Promise<void> {
  const backend = await fetchSourceState(config, sourceId);
  if (backend && Object.keys(backend).length) hydrate(sourceId, backend);
}
