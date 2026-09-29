import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';

/**
 * one music video at a time, and only the one you're looking at.
 *
 * every banner that could play a video registers a slot with how much of it
 * is on screen. the stage picks ONE: the most visible slot that has a video,
 * and only if at least half of it is showing. only that slot mounts a player,
 * so there's never more than one video element on the page, and scrolling a
 * banner away tears its player down. nothing plays while the tab is hidden,
 * with reduced motion on, or when you've switched video backgrounds off.
 */

export interface SlotState {
  /** 0..1 of the slot on screen */
  ratio: number;
  /** it has a video to play */
  hasVideo: boolean;
  /** the pointer is on it: a hovered card with a video jumps the queue */
  hover?: boolean;
}

/** at least this much of a banner must be showing for its video to play */
export const MIN_VISIBLE = 0.5;

/**
 * the slot that gets the stage, or null. a hovered slot with a video wins
 * outright (you pointed at it); otherwise the most visible one with a video,
 * if at least half of it shows. ties go to the one registered first.
 */
export function chooseActive(slots: Map<string, SlotState>, enabled: boolean): string | null {
  if (!enabled) return null;
  for (const [id, s] of slots) {
    if (s.hover && s.hasVideo && s.ratio > 0) return id;
  }
  let best: string | null = null;
  let bestRatio = MIN_VISIBLE - 1e-9;
  for (const [id, s] of slots) {
    if (s.hasVideo && s.ratio > bestRatio) {
      best = id;
      bestRatio = s.ratio;
    }
  }
  return best;
}

const PREF_KEY = 'soulsync.discover.videoBackdrops';

function readPref(): boolean {
  try {
    return window.localStorage.getItem(PREF_KEY) !== 'off';
  } catch {
    return true;
  }
}

function prefersReducedMotion(): boolean {
  return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

/** the stage itself: slots, the conditions, and whoever's listening. */
export class VideoStage {
  private slots = new Map<string, SlotState>();
  private listeners = new Set<() => void>();
  private active: string | null = null;
  userEnabled = true;
  pageVisible = true;
  reducedMotion = false;

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getActive = () => this.active;

  get enabled(): boolean {
    return this.userEnabled && this.pageVisible && !this.reducedMotion;
  }

  set(id: string, state: Partial<SlotState>) {
    const prev = this.slots.get(id) ?? { ratio: 0, hasVideo: false };
    this.slots.set(id, { ...prev, ...state });
    this.recompute();
  }

  remove(id: string) {
    this.slots.delete(id);
    this.recompute();
  }

  recompute() {
    const next = chooseActive(this.slots, this.enabled);
    if (next !== this.active) {
      this.active = next;
      for (const fn of this.listeners) fn();
    }
  }

  /** notify even when the active slot didn't change (the on/off toggle) */
  touch() {
    this.recompute();
    for (const fn of this.listeners) fn();
  }
}

export const videoStage = new VideoStage();

let wired = false;
/** hook the stage to the page once: visibility, reduced motion, the saved toggle. */
function wireStage(stage: VideoStage) {
  if (wired) return;
  wired = true;
  stage.userEnabled = readPref();
  stage.reducedMotion = prefersReducedMotion();
  stage.pageVisible = !document.hidden;
  document.addEventListener('visibilitychange', () => {
    stage.pageVisible = !document.hidden;
    stage.recompute();
  });
  window.matchMedia?.('(prefers-reduced-motion: reduce)').addEventListener?.('change', (e) => {
    stage.reducedMotion = e.matches;
    stage.recompute();
  });
}

/** the saved on/off switch, for the toggle button. */
export function useVideoBackdropsEnabled(stage = videoStage): [boolean, (on: boolean) => void] {
  wireStage(stage);
  const on = useSyncExternalStore(stage.subscribe, () => stage.userEnabled);
  const set = (next: boolean) => {
    stage.userEnabled = next;
    try {
      window.localStorage.setItem(PREF_KEY, next ? 'on' : 'off');
    } catch {
      /* a private window keeps it for this page only */
    }
    stage.touch();
  };
  return [on, set];
}

const THRESHOLDS = [0, 0.25, 0.5, 0.6, 0.75, 0.9, 1];

export interface VideoSlot {
  /** attach to the banner's element; works whenever the element appears */
  ref: (el: HTMLElement | null) => void;
  /** it has been on screen at least once: fetch its video now, not before */
  seen: boolean;
  /** it holds the stage: mount the player */
  playing: boolean;
  /** tell the stage the pointer is on this banner, or has left it */
  setHover: (hover: boolean) => void;
}

/** register a banner with the stage. */
export function useVideoSlot(id: string, hasVideo: boolean, stage = videoStage): VideoSlot {
  wireStage(stage);
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [seen, setSeen] = useState(false);
  const active = useSyncExternalStore(stage.subscribe, stage.getActive);
  const ref = useCallback((node: HTMLElement | null) => setEl(node), []);

  useEffect(() => {
    stage.set(id, { hasVideo });
  }, [id, hasVideo, stage]);

  useEffect(() => {
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      (entries) => {
        const ratio = entries[entries.length - 1]?.intersectionRatio ?? 0;
        if (ratio > 0) setSeen(true);
        stage.set(id, { ratio });
      },
      { threshold: THRESHOLDS },
    );
    observer.observe(el);
    return () => {
      observer.disconnect();
      stage.set(id, { ratio: 0 });
    };
  }, [id, el, stage]);

  useEffect(() => () => stage.remove(id), [id, stage]);

  const setHover = useCallback((hover: boolean) => stage.set(id, { hover }), [id, stage]);

  return { ref, seen, playing: active === id, setHover };
}
