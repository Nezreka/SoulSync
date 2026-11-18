/**
 * The live feed for Now playing. Prefers the socket push (one upstream poll
 * shared by every open drawer) and falls back to a 3 s http poll; a watchdog
 * goes back to polling when the socket is connected but pushes stop. With the
 * drawer on another tab the feed slows to one poll every 20 s, enough for the
 * tab's count.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { ActivityPayload, Session } from '@/shell/server-activity-types';

import { getActivity } from './api';

export const LIVE_MS = 3000;
export const SLOW_MS = 20000;
export const WATCHDOG_MS = 9000;

type Listener = (payload: ActivityPayload) => void;
const listeners = new Set<Listener>();

/** core.js's activity:update lands here through window.ServerActivity._onSocket */
export function pushActivity(payload: unknown): void {
  if (!payload || typeof payload !== 'object') return;
  for (const listener of listeners) listener(payload as ActivityPayload);
}

function onPush(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export interface LiveState {
  data: ActivityPayload | null;
  /** Date.now() when data arrived: the progress bars glide from here */
  receivedAt: number;
  error: string | null;
  loading: boolean;
}

/** the shell's badge hears every payload the drawer gets */
function tellShell(payload: ActivityPayload) {
  try {
    window.ServerActivity?._sawActivity?.(payload);
  } catch {
    // the badge is a nicety; never let it break the drawer
  }
}

export function useLiveActivity(open: boolean, fast: boolean) {
  const [state, setState] = useState<LiveState>({
    data: null,
    receivedAt: 0,
    error: null,
    loading: true,
  });
  const lastPush = useRef(0);

  const accept = useCallback((payload: ActivityPayload) => {
    tellShell(payload);
    // a shared snapshot says how old it is; the playheads start from when it was taken
    const age = Math.max(0, Math.min(payload.age_ms ?? 0, 60000));
    setState({ data: payload, receivedAt: Date.now() - age, error: null, loading: false });
  }, []);

  const refresh = useCallback(async () => {
    try {
      const result = await getActivity();
      if (result.ok) accept(result.data);
      else
        setState((prev) => ({
          ...prev,
          // a failed poll keeps what is on screen; only an empty drawer shows the error
          error: result.error,
          loading: false,
        }));
    } catch {
      // aborted
    }
  }, [accept]);

  useEffect(() => {
    if (!open) return;
    let poll: ReturnType<typeof setInterval> | null = null;
    let watchdog: ReturnType<typeof setInterval> | null = null;
    let subscribed = false;
    const socket = window.SoulSyncActivitySocket;

    const startPoll = (ms: number) => {
      if (poll) clearInterval(poll);
      poll = setInterval(() => {
        if (typeof document !== 'undefined' && document.hidden) return;
        void refresh();
      }, ms);
    };

    void refresh();
    const off = onPush((payload) => {
      lastPush.current = Date.now();
      accept(payload);
    });

    if (fast && socket?.isConnected()) {
      socket.subscribe();
      subscribed = true;
      lastPush.current = Date.now();
      watchdog = setInterval(() => {
        if (!poll && Date.now() - lastPush.current > WATCHDOG_MS) startPoll(LIVE_MS);
        else if (poll && Date.now() - lastPush.current < LIVE_MS) {
          // pushes are back: stop the http poll
          clearInterval(poll);
          poll = null;
        }
      }, LIVE_MS);
    } else {
      startPoll(fast ? LIVE_MS : SLOW_MS);
    }

    return () => {
      off();
      if (poll) clearInterval(poll);
      if (watchdog) clearInterval(watchdog);
      if (subscribed) socket?.unsubscribe();
    };
  }, [open, fast, refresh, accept]);

  return { ...state, refresh };
}

/** ticks every `ms` while on, so progress bars glide between polls */
export function useNow(on: boolean, ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(timer);
  }, [on, ms]);
  return now;
}

/**
 * Where a session's playhead is now. Small corrections from the server are
 * absorbed (the bar never steps backwards for a 2 s difference); a real seek
 * moves it at once.
 */
export function useGlide(session: Session, receivedAt: number, now: number): number {
  const anchor = useRef({ offset: session.offset_ms, at: receivedAt, id: session.id });
  const playing = session.state === 'playing';
  const a = anchor.current;
  if (a.id !== session.id || a.at !== receivedAt) {
    const predicted = a.offset + (playing ? receivedAt - a.at : 0);
    const drift = Math.abs(session.offset_ms - predicted);
    if (a.id !== session.id || !playing || drift > 4000) {
      anchor.current = { offset: session.offset_ms, at: receivedAt, id: session.id };
    } else {
      // keep gliding; fold the correction in gently
      anchor.current = {
        offset: predicted + (session.offset_ms - predicted) * 0.25,
        at: receivedAt,
        id: session.id,
      };
    }
  }
  const { offset, at } = anchor.current;
  const live = offset + (playing ? Math.max(0, now - at) : 0);
  return session.duration_ms ? Math.min(live, session.duration_ms) : live;
}

/**
 * Keyed list with a short goodbye: a stream that ends stays for `ms` marked
 * leaving, so it can fade out instead of the list jumping.
 */
export function usePresence<T>(items: T[], key: (item: T) => string, ms = 360) {
  const [gone, setGone] = useState<Map<string, { item: T; index: number }>>(new Map());
  const prev = useRef<T[]>(items);
  useEffect(() => {
    const now = new Set(items.map(key));
    const leaving = new Map<string, { item: T; index: number }>();
    prev.current.forEach((item, index) => {
      const k = key(item);
      if (!now.has(k)) leaving.set(k, { item, index });
    });
    prev.current = items;
    if (!leaving.size) return;
    setGone((current) => new Map([...current, ...leaving]));
    const timer = setTimeout(() => {
      setGone((current) => {
        const next = new Map(current);
        for (const k of leaving.keys()) next.delete(k);
        return next;
      });
    }, ms);
    return () => clearTimeout(timer);
    // key is a stable accessor from the caller
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, ms]);

  const out: { item: T; key: string; leaving: boolean }[] = items.map((item) => ({
    item,
    key: key(item),
    leaving: false,
  }));
  const live = new Set(out.map((o) => o.key));
  for (const [k, { item, index }] of gone) {
    if (live.has(k)) continue;
    out.splice(Math.min(index, out.length), 0, { item, key: k, leaving: true });
  }
  return out;
}
