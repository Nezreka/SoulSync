/**
 * The source-vertical controller — ONE hook for what the vanilla implements
 * nine times (startXDiscoveryPolling / startXSyncPolling / handleXCardClick
 * plumbing in sync-services.js). Which source it drives comes entirely from
 * the config; the drift catalog lives there, not here.
 *
 * Transport: the HTTP poll at the source's vanilla cadence is THE guaranteed
 * path — nothing on this page emits discovery:subscribe, so the server's
 * room-scoped discovery:progress frames only reach the ss:discovery-progress
 * CustomEvent (core.js:878) when some OTHER surface subscribed the id (e.g.
 * the explorer's mirrored discoveries). The listener is kept for exactly that
 * case, filtered by frame id AND platform. Sync progress is HTTP-poll only,
 * the discover-port precedent.
 *
 * Completion announcement: the vanilla fires its toast strictly on the payload's
 * `complete` flag (9226 socket, 9268 poll) except ListenBrainz, which also
 * accepts phase 'discovered' (11062). This hook announces on EITHER, for every
 * source, and never on phase 'error' — a knowing unification that follows the
 * one already made for the stop condition: the vanilla's stricter test leaves
 * its HTTP poll spinning forever on a phase-only completion, which this port
 * deliberately stops, and stopping successfully without announcing would be
 * incoherent. It also announces at most ONCE per run, where the vanilla's
 * socket and always-on poll can both reach the block and toast twice.
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
  resetSourceDiscovery,
  startSourceSync,
  updateSourcePhase,
} from './-sync.api';
import { discoveryCompleteToast } from './-sync.core';
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
  /** Apply a pure reducer to one playlist's state (fix/unmatch flows). */
  patchState: (sourceId: string, fn: (state: SourcePlaylistState) => SourcePlaylistState) => void;
  /**
   * Drop a playlist's state entirely — the `delete youtubePlaylistStates[hash]`
   * the vanilla runs after clearing discovery (stats-automations.js 1187).
   * patchState cannot express this: it materialises a fresh state when the
   * key is absent.
   */
  dropState: (sourceId: string) => void;
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
  /**
   * The 🔄 Rediscover hard reset (resetYouTubePlaylist 10785 /
   * resetBeatportChart 10837): POST, stop polling, zero the discovery + sync
   * fields, toast, and tell the caller to close the modal.
   */
  resetDiscovery: (sourceId: string) => Promise<void>;
}

export interface SourceVerticalOptions {
  /**
   * Extra work when a discovery finishes, after the toast. The ONE production
   * user is ListenBrainz, which auto-mirrors its matched tracks at exactly this
   * point (_mirrorListenBrainzAfterDiscovery, called at 11075 socket / 11170
   * poll) — that is what puts LB and Last.fm Radio playlists in the Mirrored
   * tab and therefore on the Auto-Sync board.
   */
  onDiscoveryComplete?: (sourceId: string) => void;
}

export function useSourceVertical(
  config: SourceVerticalConfig,
  options: SourceVerticalOptions = {},
): SourceVertical {
  const [states, setStates] = useState<Record<string, SourcePlaylistState>>({});
  const statesRef = useRef(states);
  statesRef.current = states;

  const discoveryPollers = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  const syncPollers = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  /**
   * Ids whose completion has already been announced. The vanilla can announce
   * twice — its socket callback and its always-on HTTP poll both run the
   * completion block (9233 and 9281) — but a doubled toast is a defect, not a
   * behaviour to transcribe, so the port announces once per discovery run. The
   * entry is cleared when a new discovery starts.
   */
  const announced = useRef<Set<string>>(new Set());
  /** Ids whose announcement is waiting for the patch that triggered it. */
  const pendingAnnounce = useRef<Set<string>>(new Set());
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const patch = useCallback(
    (sourceId: string, fn: (s: SourcePlaylistState) => SourcePlaylistState) => {
      setStates((current) => ({
        ...current,
        [sourceId]: fn(current[sourceId] ?? freshSourceState(config, sourceId)),
      }));
    },
    [config],
  );

  /**
   * Queue the completion side effects. They must not run here: the caller has
   * just patched the payload in, and the vanilla writes those counters BEFORE
   * it toasts (9224 then 9233) — reading statesRef now would report the
   * PRE-completion match count, which is exactly wrong for the #815 retry
   * message. The effect below runs them once the patch has committed.
   */
  const announceComplete = useCallback((sourceId: string) => {
    if (announced.current.has(sourceId)) return;
    announced.current.add(sourceId);
    pendingAnnounce.current.add(sourceId);
  }, []);

  useEffect(() => {
    if (pendingAnnounce.current.size === 0) return;
    const ids = [...pendingAnnounce.current];
    pendingAnnounce.current.clear();
    for (const sourceId of ids) {
      const state = states[sourceId];
      const toast = discoveryCompleteToast(
        config.ux.discoveryCompleteToast,
        state?.spotifyMatches ?? 0,
        state?.retryDiscovery,
      );
      if (state?.retryDiscovery) {
        // Consumed — the vanilla deletes the baseline at 9198 so the next
        // plain discovery reports plainly.
        patch(sourceId, ({ retryDiscovery: _consumed, ...rest }) => rest);
      }
      if (toast) window.showToast?.(toast.message, toast.type);
      optionsRef.current.onDiscoveryComplete?.(sourceId);
    }
  }, [config, patch, states]);

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
      // source's own id (623 tidal, 4725 beatport, 11026 LB...). Rooms are not
      // platform-namespaced and qobuz/deezer numeric ids can collide, so the
      // frame's platform must match too (web_server.py 41887; mirrored
      // discoveries run on the youtube platform).
      if (!(id in statesRef.current)) return;
      const platform = frame.platform;
      if (typeof platform === 'string') {
        const matches =
          platform === config.id || (config.id === 'mirrored' && platform === 'youtube');
        if (!matches) return;
      }
      if (frame.error) {
        stopDiscoveryPoll(id);
        return;
      }
      patch(id, (s) => applyDiscovery(s, config, frame as DiscoveryPayload));
      if (frame.complete || frame.phase === 'discovered' || frame.phase === 'error') {
        stopDiscoveryPoll(id);
        if (frame.phase !== 'error') announceComplete(id);
      }
    };
    window.addEventListener(SYNC_DISCOVERY_EVENT, onFrame);
    return () => window.removeEventListener(SYNC_DISCOVERY_EVENT, onFrame);
  }, [announceComplete, config, patch, stopDiscoveryPoll]);

  /* ── The HTTP backstop ──────────────────────────────────────────────────── */

  const startDiscoveryPoll = useCallback(
    (sourceId: string) => {
      stopDiscoveryPoll(sourceId);
      // A new observation window: this run gets its own completion announcement.
      announced.current.delete(sourceId);
      discoveryPollers.current[sourceId] = setInterval(async () => {
        try {
          const status = await fetchSourceDiscoveryStatus(config, sourceId);
          if (status.error) {
            stopDiscoveryPoll(sourceId);
            return;
          }
          patch(sourceId, (s) => applyDiscovery(s, config, status));
          // Backends park failures on phase 'error' with no error field and
          // complete still false (core/discovery/endpoints.py) — the vanilla
          // HTTP polls spin forever on it; stop here.
          if (status.complete || status.phase === 'discovered' || status.phase === 'error') {
            stopDiscoveryPoll(sourceId);
            if (status.phase !== 'error') announceComplete(sourceId);
          }
        } catch {
          stopDiscoveryPoll(sourceId);
        }
      }, config.discovery.pollMs);
    },
    [announceComplete, config, patch, stopDiscoveryPoll],
  );

  const startSyncPoll = useCallback(
    (sourceId: string) => {
      stopSyncPoll(sourceId);
      const tick = async () => {
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
      };
      // The vanilla runs the poll body immediately on start/resume (1105).
      void tick();
      syncPollers.current[sourceId] = setInterval(tick, config.sync.pollMs);
    },
    [config, patch, stopSyncPoll],
  );

  /* ── Actions ────────────────────────────────────────────────────────────── */

  const dropState = useCallback((sourceId: string) => {
    setStates((current) => {
      if (!(sourceId in current)) return current;
      const next = { ...current };
      delete next[sourceId];
      return next;
    });
  }, []);

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
      try {
        const response = await startSourceSync(config, sourceId);
        if (response.error) return;
        patch(sourceId, (s) => applySyncStarted(s, response));
        startSyncPoll(sourceId);
      } catch {
        // The vanilla toasts and stays put on a failed start (1013-1016).
      }
    },
    [config, patch, startSyncPoll],
  );

  const resumeSync = useCallback((sourceId: string) => startSyncPoll(sourceId), [startSyncPoll]);

  const cancelSync = useCallback(
    async (sourceId: string) => {
      // The vanilla returns WITHOUT reverting when the cancel fails
      // (cancelTidalSync 1129-1132 and twins) — the sync is still running and
      // the poller keeps tracking it.
      try {
        const response = await cancelSourceSync(config, sourceId);
        if (response.error) return;
      } catch {
        return;
      }
      stopSyncPoll(sourceId);
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

  const resetDiscovery = useCallback(
    async (sourceId: string) => {
      const state = statesRef.current[sourceId];
      // 10787 / 10841 — no state, nothing to reset.
      if (!state) return;
      const name = (state.playlist?.name as string) ?? '';
      try {
        await resetSourceDiscovery(config, sourceId);
        stopDiscoveryPoll(sourceId);
        stopSyncPoll(sourceId);
        // The field-by-field zeroing at 10809-10815 / 10872-10881. Beatport
        // writes both key styles; this store keeps one, so one assignment
        // covers both.
        patch(sourceId, (s) => ({
          ...s,
          phase: 'fresh',
          rawResults: [],
          rows: [],
          discoveryProgress: 0,
          spotifyMatches: 0,
          syncPlaylistId: undefined,
          lastSyncProgress: undefined,
          convertedSpotifyPlaylistId: undefined,
        }));
        // A fresh run must be announceable again.
        announced.current.delete(sourceId);
        window.showToast?.(`Reset "${name}" to fresh state`, 'success');
      } catch (err) {
        const message = err instanceof Error ? err.message : 'unknown error';
        window.showToast?.(`Error resetting ${config.ux.resetErrorNoun}: ${message}`, 'error');
      }
    },
    [config, patch, stopDiscoveryPoll, stopSyncPoll],
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
    patchState: patch,
    dropState,
    startDiscovery,
    resumeDiscovery,
    startSync,
    resumeSync,
    cancelSync,
    closeModalReset,
    resetDiscovery,
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
  try {
    const backend = await fetchSourceState(config, sourceId);
    if (backend && Object.keys(backend).length) hydrate(sourceId, backend);
  } catch {
    // Hydration is best-effort; the card just stays on its current state.
  }
}
