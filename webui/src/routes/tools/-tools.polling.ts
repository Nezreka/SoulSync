/**
 * The polling lifecycle the long-running tool cards share.
 *
 * The vanilla shape, which this reproduces: hydrate once on mount, and if the
 * job comes back `running`, start an interval that keeps hydrating until it
 * stops. Three details are deliberate rather than incidental:
 *
 * 1. **A failed poll does NOT stop the poll.** The vanilla comments say so
 *    explicitly ("Don't stop polling on network errors - keep trying"), and the
 *    api layer already returns null rather than throwing for exactly this. A
 *    blip must not strand a running job's card on a frozen bar.
 *
 * 2. **A null result is not "finished".** It means "no news" — the previous
 *    state is kept. Treating it as a terminal state is how a card ends up
 *    claiming a job completed that is still running.
 *
 * 3. **The interval is cleared on unmount.** The vanilla relied on
 *    `loadPageData` clearing two of these on navigation and on the UI updaters
 *    self-clearing the rest — but those updaters early-return when their
 *    elements are missing, so once the markup is gone the intervals would run
 *    forever. React unmount is the honest place for it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export interface PolledStatus<T> {
  /** Latest known state, or null before the first successful hydrate. */
  state: T | null;
  /** True while the interval is armed. */
  polling: boolean;
  /** Hydrate now — used after a start/stop action. */
  refresh: () => Promise<T | null>;
  /** Arm the poll without waiting for a hydrate to report `running` first. */
  arm: () => void;
}

export interface PollOptions<T> {
  fetcher: () => Promise<T | null>;
  /** Whether this state means "keep polling". */
  isRunning: (state: T) => boolean;
  intervalMs: number;
  /** Called with each new state, including the first. */
  onState?: (state: T, previous: T | null) => void;
  /** Skip the mount hydrate (the media scan starts on click, not on load). */
  hydrateOnMount?: boolean;
}

export function usePolledStatus<T>({
  fetcher,
  isRunning,
  intervalMs,
  onState,
  hydrateOnMount = true,
}: PollOptions<T>): PolledStatus<T> {
  const [state, setState] = useState<T | null>(null);
  const [polling, setPolling] = useState(false);

  // Refs so the interval callback never closes over a stale fetcher/state and
  // the effect below doesn't need them as dependencies.
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stateRef = useRef<T | null>(null);
  const fetcherRef = useRef(fetcher);
  const isRunningRef = useRef(isRunning);
  const onStateRef = useRef(onState);
  const mountedRef = useRef(true);

  fetcherRef.current = fetcher;
  isRunningRef.current = isRunning;
  onStateRef.current = onState;

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    setPolling(false);
  }, []);

  const refresh = useCallback(async (): Promise<T | null> => {
    const next = await fetcherRef.current();
    if (!mountedRef.current) return next;
    // null means "no news", NOT "finished" — keep the last known state.
    if (next === null) return null;
    const previous = stateRef.current;
    stateRef.current = next;
    setState(next);
    onStateRef.current?.(next, previous);
    if (!isRunningRef.current(next)) stop();
    return next;
  }, [stop]);

  const arm = useCallback(() => {
    if (timerRef.current) return;
    setPolling(true);
    timerRef.current = setInterval(() => {
      void refresh();
    }, intervalMs);
  }, [intervalMs, refresh]);

  useEffect(() => {
    mountedRef.current = true;
    if (hydrateOnMount) {
      void refresh().then((first) => {
        if (mountedRef.current && first && isRunningRef.current(first)) arm();
      });
    }
    return () => {
      mountedRef.current = false;
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
    // Mount only: `refresh`/`arm` are stable and re-running this would restart
    // the hydrate on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { state, polling, refresh, arm };
}

/**
 * The db updater's socket-independent safety net (#859).
 *
 * The 1s WebSocket broadcast normally drives that card, but if the socket goes
 * quiet or half-open it can wedge on "Starting…" with a frozen bar and no way
 * back. This polls /status directly on a slower cadence and disarms itself once
 * the job is no longer running — same contract as `armDbUpdateSafetyPoll`.
 */
export function useSafetyPoll<T>(
  fetcher: () => Promise<T | null>,
  isRunning: (state: T) => boolean,
  onState: (state: T) => void,
  intervalMs = 5000,
) {
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fetcherRef = useRef(fetcher);
  const isRunningRef = useRef(isRunning);
  const onStateRef = useRef(onState);

  fetcherRef.current = fetcher;
  isRunningRef.current = isRunning;
  onStateRef.current = onState;

  const disarm = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const arm = useCallback(() => {
    disarm();
    const tick = async () => {
      const next = await fetcherRef.current();
      // A transient failure keeps the net armed — that is the whole point of it.
      if (!next) return;
      onStateRef.current(next);
      if (!isRunningRef.current(next)) disarm();
    };
    // Immediate: flips the card off "Starting…" as soon as the server confirms.
    void tick();
    timerRef.current = setInterval(() => void tick(), intervalMs);
  }, [disarm, intervalMs]);

  useEffect(() => disarm, [disarm]);

  return { arm, disarm };
}
