/**
 * The Auto-Sync pipeline controller — runMirroredPlaylistPipeline and
 * pollMirroredPipelineStatus (auto-sync.js 2467-2525) as one hook.
 *
 * The vanilla keeps its pollers in a script-scoped `mirroredPipelinePollers`
 * map keyed by the mirrored hash, which is exactly the guard the render-time
 * resume needs (stats-automations.js 653-655): a card whose row says the
 * pipeline is running restarts the poll, unless one is already in flight.
 * That map is a ref here, so `resume` is idempotent no matter how many times
 * the list re-renders.
 *
 * DECLARED DEFERRAL: after a successful start the vanilla also patches
 * _autoSyncScheduleState, re-renders the Auto-Sync schedule modal and kicks
 * its status polling (2478-2483). That board is its own wave and does not
 * exist yet; there is nothing to update, so those three calls have no
 * counterpart here. Same for refreshAutoSyncScheduleModal on the terminal
 * arms (2506, 2512).
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';

import type { MirroredPipelineState } from './-sync.pipeline';

import { fetchMirroredPipelineStatus, runMirroredPipeline } from './-sync.api';
import {
  PIPELINE_POLL_MS,
  PIPELINE_STARTING_STATE,
  pipelineStartedToast,
  pipelineStatusErrorToast,
  pipelineTickOutcome,
} from './-sync.pipeline';

export interface PipelineController {
  /** The Auto-Sync button: POST, apply the state, then poll (2467). */
  run: (playlistId: number, name: string) => Promise<void>;
  /**
   * Restart polling for a row the backend already reports as running, unless
   * a poller for it is live (653-655). Safe to call on every render.
   */
  resume: (playlistId: number, name: string) => void;
}

export interface UsePipelineOptions {
  /** Write the pipeline fields onto the per-hash state (applyMirroredPipelineState). */
  onState: (playlistId: number, state: MirroredPipelineState) => void;
  /** loadMirroredPlaylists — the terminal arms refetch the list (2505, 2511). */
  reload: () => void;
}

export function useMirroredPipeline({ onState, reload }: UsePipelineOptions): PipelineController {
  const pollers = useRef(new Map<number, ReturnType<typeof setInterval>>());
  const alive = useRef(true);

  /**
   * The collaborators live in refs so the returned controller is STABLE.
   * Both callers pass closures over per-render state, so taking them as
   * dependencies would rebuild `resume` on every render — and the tab drives
   * the render-time poller resume from an effect keyed on it.
   */
  const latest = useRef({ onState, reload });
  useEffect(() => {
    latest.current = { onState, reload };
  });

  useEffect(() => {
    alive.current = true;
    const live = pollers.current;
    return () => {
      alive.current = false;
      live.forEach(clearInterval);
      live.clear();
    };
  }, []);

  const stop = useCallback((playlistId: number) => {
    const poller = pollers.current.get(playlistId);
    if (poller !== undefined) clearInterval(poller);
    pollers.current.delete(playlistId);
  }, []);

  /**
   * One status tick. The vanilla runs it once immediately and then on a 2.5s
   * interval (2521-2524), so a card that is already finished settles without
   * waiting a full period.
   */
  const poll = useCallback(
    (playlistId: number, name: string) => {
      stop(playlistId);

      const tick = async () => {
        try {
          const state = await fetchMirroredPipelineStatus(playlistId);
          if (!alive.current) return;
          latest.current.onState(playlistId, state);
          const outcome = pipelineTickOutcome(state, name);
          if (!outcome.terminal) return;
          stop(playlistId);
          if (outcome.toast) window.showToast?.(outcome.toast.message, outcome.toast.type);
          if (outcome.reload) latest.current.reload();
        } catch (err) {
          if (!alive.current) return;
          // A failed tick STOPS the poller — the vanilla does not retry (2520).
          stop(playlistId);
          window.showToast?.(
            pipelineStatusErrorToast(err instanceof Error ? err.message : 'unknown error'),
            'error',
          );
        }
      };

      void tick();
      // The immediate tick cannot have resolved yet (it awaits a fetch), so
      // the interval always arms here — and a tick that later turns out to be
      // terminal clears it through stop().
      pollers.current.set(
        playlistId,
        setInterval(() => void tick(), PIPELINE_POLL_MS),
      );
    },
    [stop],
  );

  const run = useCallback(
    async (playlistId: number, name: string) => {
      try {
        const data = await runMirroredPipeline(playlistId);
        if (!alive.current) return;
        latest.current.onState(playlistId, data.state || PIPELINE_STARTING_STATE);
        window.showToast?.(pipelineStartedToast(name), 'success');
        poll(playlistId, name);
      } catch (err) {
        if (!alive.current) return;
        window.showToast?.(
          `Error: ${err instanceof Error ? err.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [poll],
  );

  const resume = useCallback(
    (playlistId: number, name: string) => {
      if (pollers.current.has(playlistId)) return;
      poll(playlistId, name);
    },
    [poll],
  );

  return useMemo(() => ({ run, resume }), [run, resume]);
}
