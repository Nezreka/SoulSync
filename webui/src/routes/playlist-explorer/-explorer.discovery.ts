/**
 * Discovery: the live card updates, the Discover button, and the backstop
 * poller (initExplorer :18, explorerStartDiscovery :193,
 * _explorerStartDiscoveryPoller :214, explorerRedirectToDiscover :184).
 *
 * The vanilla subscribed to `socket` directly and read `youtubePlaylistStates`
 * for the finished phase. Both are module-scoped `let`s in core.js, invisible
 * to a module — so core.js re-broadcasts the frame as `ss:discovery-progress`
 * and this tracks the phase itself. That is one bridge instead of two, and the
 * frame already carries everything the poller needed.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { DiscoverButtonState } from './-ui/explorer-picker';

/** The window event core.js mirrors each `discovery:progress` frame onto. */
export const EXPLORER_DISCOVERY_EVENT = 'ss:discovery-progress';

/** initExplorer (:31) — let the DB commit before re-reading the cards. */
export const DISCOVERY_REFRESH_DELAY_MS = 1500;
/** _explorerStartDiscoveryPoller (:216) — the backstop cadence. */
export const DISCOVERY_POLL_MS = 5000;
/** The phases that mean this playlist is done (:227). */
export const DISCOVERY_DONE_PHASES = ['discovered', 'sync_complete'];

export interface DiscoveryFrame {
  id?: unknown;
  phase?: unknown;
  progress?: unknown;
  complete?: unknown;
}

/**
 * initExplorer (:35) — the frame ids are `mirrored_<playlistId>`. Anything
 * else on the channel belongs to another page.
 */
export function mirroredPlaylistIdFromEventId(id: unknown): number | null {
  if (typeof id !== 'string' || !id.startsWith('mirrored_')) return null;
  const parsed = Number.parseInt(id.slice('mirrored_'.length), 10);
  return Number.isNaN(parsed) ? null : parsed;
}

/** initExplorer (:29) — any of the three means "re-read the playlist cards". */
export function isDiscoveryComplete(frame: DiscoveryFrame): boolean {
  return frame.phase === 'discovered' || frame.phase === 'sync_complete' || Boolean(frame.complete);
}

export function isDiscoveryDonePhase(phase: unknown): boolean {
  return typeof phase === 'string' && DISCOVERY_DONE_PHASES.includes(phase);
}

export interface ExplorerDiscovery {
  /** playlist id -> live percent, which replaces that card's meta line. */
  liveDiscovery: Record<number, number>;
  discoverStates: Record<number, DiscoverButtonState>;
  startDiscovery: (playlistId: number) => Promise<void>;
}

/**
 * `onRefresh` re-reads the playlist cards; the hook calls it 1.5s after a
 * completion frame and on every poll tick.
 */
export function useExplorerDiscovery(onRefresh: () => void): ExplorerDiscovery {
  const [liveDiscovery, setLiveDiscovery] = useState<Record<number, number>>({});
  const [discoverStates, setDiscoverStates] = useState<Record<number, DiscoverButtonState>>({});

  const refreshRef = useRef(onRefresh);
  refreshRef.current = onRefresh;
  /** Latest phase seen per playlist — what youtubePlaylistStates gave the vanilla. */
  const phases = useRef<Record<number, string>>({});
  const poller = useRef<ReturnType<typeof setInterval> | null>(null);
  const refreshTimers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const stopPoller = useCallback(() => {
    if (poller.current !== null) clearInterval(poller.current);
    poller.current = null;
  }, []);

  useEffect(() => {
    const onFrame = (event: Event) => {
      const frame = (event as CustomEvent<DiscoveryFrame>).detail ?? {};

      if (isDiscoveryComplete(frame)) {
        const timer = setTimeout(() => refreshRef.current(), DISCOVERY_REFRESH_DELAY_MS);
        refreshTimers.current.push(timer);
      }

      const playlistId = mirroredPlaylistIdFromEventId(frame.id);
      if (playlistId === null) return;
      if (typeof frame.phase === 'string') phases.current[playlistId] = frame.phase;
      // `!= null` deliberately: 0% is a real reading and must still paint.
      if (frame.progress != null) {
        setLiveDiscovery((current) => ({ ...current, [playlistId]: Number(frame.progress) }));
      }
    };

    window.addEventListener(EXPLORER_DISCOVERY_EVENT, onFrame);
    return () => window.removeEventListener(EXPLORER_DISCOVERY_EVENT, onFrame);
  }, []);

  useEffect(
    () => () => {
      stopPoller();
      refreshTimers.current.forEach(clearTimeout);
      refreshTimers.current = [];
    },
    [stopPoller],
  );

  const startPoller = useCallback(
    (playlistId: number) => {
      // ONE poller, as the vanilla had: starting discovery on a second
      // playlist replaces the first one's.
      stopPoller();
      poller.current = setInterval(() => {
        refreshRef.current();
        if (isDiscoveryDonePhase(phases.current[playlistId])) stopPoller();
      }, DISCOVERY_POLL_MS);
    },
    [stopPoller],
  );

  const startDiscovery = useCallback(
    async (playlistId: number) => {
      setDiscoverStates((current) => ({ ...current, [playlistId]: 'starting' }));
      try {
        if (typeof window.discoverMirroredPlaylist === 'function') {
          await window.discoverMirroredPlaylist(playlistId);
          setDiscoverStates((current) => ({ ...current, [playlistId]: 'open' }));
          startPoller(playlistId);
          return;
        }
        // explorerRedirectToDiscover (:184) — no discovery modal on this page,
        // so hand over to the Sync page's mirrored tab.
        setDiscoverStates((current) => ({ ...current, [playlistId]: 'idle' }));
        window.showToast?.(
          'This playlist needs more tracks discovered before exploring. Redirecting to Sync...',
          'info',
        );
        void window.SoulSyncWebRouter?.navigateToPage('sync');
        setTimeout(() => {
          document.querySelector<HTMLElement>('.sync-tab-button[data-tab="mirrored"]')?.click();
        }, 200);
      } catch (error) {
        setDiscoverStates((current) => ({ ...current, [playlistId]: 'idle' }));
        window.showToast?.(`Discovery failed: ${(error as Error)?.message}`, 'error');
      }
    },
    [startPoller],
  );

  return { liveDiscovery, discoverStates, startDiscovery };
}
