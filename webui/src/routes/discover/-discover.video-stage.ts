import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';

/**
 * music videos behind banners, only where you're looking, within a budget.
 *
 * every banner that could play a video registers a slot with how much of it
 * is on screen. the stage picks which ones are live: the one you turned the
 * sound on for, then one you're pointing at, then the most visible ones (at
 * least half showing), up to a budget the device can carry. only live slots
 * mount a player, so scrolling a banner away tears its player down. nothing
 * plays while the tab is hidden, with reduced motion on, or when you've
 * switched video backgrounds off. only one video ever has its sound on.
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

/** a live banner keeps its place over one that's only a little more visible */
const STICKY = 0.15;

export interface DeviceHints {
  hardwareConcurrency?: number;
  deviceMemory?: number;
  connection?: { saveData?: boolean };
}

/**
 * how many videos may play at once. one on a phone or with data saver on,
 * two on a small machine, up to six on a strong one. muted players this
 * small get youtube's low-bitrate streams, so six is light work for a desktop.
 */
export function liveBudget(nav: DeviceHints, coarsePointer: boolean): number {
  if (nav.connection?.saveData || coarsePointer) return 1;
  if ((nav.deviceMemory ?? 8) < 4) return 2;
  const cores = nav.hardwareConcurrency ?? 4;
  if (cores >= 8) return 6;
  return cores >= 4 ? 4 : 2;
}

/**
 * the slots that play, in priority order. the sound owner first (while any
 * of it shows), then hovered slots, then the most visible with at least half
 * showing. slots already live get a small edge so a card on the edge of the
 * screen doesn't flicker between players.
 */
export function chooseLive(
  slots: Map<string, SlotState>,
  enabled: boolean,
  budget: number,
  sound: string | null = null,
  current: ReadonlySet<string> = new Set(),
): string[] {
  if (!enabled || budget < 1) return [];
  const out: string[] = [];
  const take = (id: string) => {
    if (out.length < budget && !out.includes(id)) out.push(id);
  };
  const soundSlot = sound ? slots.get(sound) : undefined;
  if (sound && soundSlot?.hasVideo && soundSlot.ratio > 0) take(sound);
  for (const [id, s] of slots) if (s.hover && s.hasVideo && s.ratio > 0) take(id);
  const score = (id: string, s: SlotState) => s.ratio + (current.has(id) ? STICKY : 0);
  const rest = [...slots]
    .filter(([, s]) => s.hasVideo && s.ratio >= MIN_VISIBLE)
    .sort((a, b) => score(b[0], b[1]) - score(a[0], a[1]));
  for (const [id] of rest) take(id);
  return out;
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
  private live: ReadonlySet<string> = new Set();
  private sound: string | null = null;
  userEnabled = true;
  pageVisible = true;
  reducedMotion = false;
  budget = 1;

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  isLive = (id: string) => this.live.has(id);

  getLive = () => this.live;

  getSound = () => this.sound;

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

  /** give one banner the sound (null mutes it). every other video stays muted */
  setSound(id: string | null) {
    if (this.sound === id) return;
    this.sound = id;
    this.recompute();
    this.notify();
  }

  recompute() {
    const next = chooseLive(this.slots, this.enabled, this.budget, this.sound, this.live);
    let changed = next.length !== this.live.size || next.some((id) => !this.live.has(id));
    if (changed) this.live = new Set(next);
    // the sound goes with its video
    if (this.sound && !this.live.has(this.sound)) {
      this.sound = null;
      changed = true;
    }
    if (changed) this.notify();
  }

  /** notify even when the live set didn't change (the on/off toggle) */
  touch() {
    this.recompute();
    this.notify();
  }

  private notify() {
    for (const fn of this.listeners) fn();
  }
}

export const videoStage = new VideoStage();

const wired = new WeakSet<VideoStage>();
/** hook the stage to the page once: visibility, reduced motion, the saved toggle. */
function wireStage(stage: VideoStage) {
  if (wired.has(stage)) return;
  wired.add(stage);
  stage.userEnabled = readPref();
  stage.reducedMotion = prefersReducedMotion();
  stage.pageVisible = !document.hidden;
  stage.budget = liveBudget(
    navigator as DeviceHints,
    Boolean(window.matchMedia?.('(pointer: coarse)').matches),
  );
  // soulsync's own player started: the video gives the sound back. media
  // events don't bubble, so listen in the capture phase
  document.addEventListener(
    'play',
    (e) => {
      if ((e.target as Element | null)?.id === 'audio-player') stage.setSound(null);
    },
    true,
  );
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
  /** it's live: mount the player */
  playing: boolean;
  /** at least MIN_VISIBLE of it is on screen */
  visible: boolean;
  /** tell the stage the pointer is on this banner, or has left it */
  setHover: (hover: boolean) => void;
  /** this banner's video is the one with sound */
  soundOn: boolean;
  /** take the sound (muting any other), or give it back */
  setSound: (on: boolean) => void;
}

/** register a banner with the stage. */
export function useVideoSlot(id: string, hasVideo: boolean, stage = videoStage): VideoSlot {
  wireStage(stage);
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [seen, setSeen] = useState(false);
  const [visible, setVisible] = useState(false);
  const playing = useSyncExternalStore(stage.subscribe, () => stage.isLive(id));
  const soundOn = useSyncExternalStore(stage.subscribe, () => stage.getSound() === id);
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
        setVisible(ratio >= MIN_VISIBLE);
        stage.set(id, { ratio });
      },
      { threshold: THRESHOLDS },
    );
    observer.observe(el);
    return () => {
      observer.disconnect();
      setVisible(false);
      stage.set(id, { ratio: 0 });
    };
  }, [id, el, stage]);

  useEffect(() => () => stage.remove(id), [id, stage]);

  const setHover = useCallback((hover: boolean) => stage.set(id, { hover }), [id, stage]);
  const setSound = useCallback(
    (on: boolean) => {
      if (on) stage.setSound(id);
      else if (stage.getSound() === id) stage.setSound(null);
    },
    [id, stage],
  );

  return { ref, seen, visible, playing, setHover, soundOn, setSound };
}
