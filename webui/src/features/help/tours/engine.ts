/**
 * The tour engine: which step is up, getting to its page, finding its
 * anchor, skipping what isn't there. layer.tsx draws whatever this says.
 *
 * Rules (plan 4.9):
 *   - a step goes to its page first, on the right side, and waits for the
 *     page to really be there (a refused page bounces, and counts as missing)
 *   - an anchor gets time to appear (async pages), an optional one only a
 *     moment; a missing one is skipped and logged once, never a card
 *     floating in a corner
 *   - a step can ask the user to close something first (waitUntilGone), or
 *     move on by itself when the user does the thing (advanceOn)
 */

import { create } from 'zustand';

import { viewerIsAdmin } from '../page';
import { isMac } from '../shortcut-registry';
import { useHelpStore } from '../store';
import { arriveAt, findAnchor, findShown, onPage, waitFor } from './navigate';
import { copy, tourById, type Tour, type TourContext, type TourStep } from './tours';

/** the tour engine's own phone line, the sheet's breakpoint */
export const TOUR_PHONE_QUERY = '(max-width: 768px)';
export const PAGE_WAIT_MS = 8000;
export const ANCHOR_WAIT_MS = 4000;
export const OPTIONAL_WAIT_MS = 900;
/** how long a blocker (an open window) may hold a step before we stop asking */
export const BLOCKED_WAIT_MS = 10 * 60_000;
export const DONE_KEY = 'soulsync_tours_done';

export type Phase = 'idle' | 'finding' | 'blocked' | 'shown';

export interface TourState {
  tour: Tour | null;
  index: number;
  phase: Phase;
  target: HTMLElement | null;
  /** what a blocked step asks for */
  blockedBy: string | null;
  ctx: TourContext;
  /** steps actually shown this run: 0 at the end means nothing was there */
  shown: number;
  /** a one step spotlight from Show me: no counter, no "done" record */
  adHoc: boolean;
  /** steps known not to be there this run: the counter and the dots leave them out */
  skipped: number[];
  run: number;
}

const IDLE_CTX: TourContext = { isAdmin: false, canDownload: true, mac: false, phone: false };

export const useTourStore = create<TourState>(() => ({
  tour: null,
  index: 0,
  phase: 'idle',
  target: null,
  blockedBy: null,
  ctx: IDLE_CTX,
  shown: 0,
  adHoc: false,
  skipped: [],
  run: 0,
}));

/** "2 of 6" over the steps still in play */
export function stepPosition(
  total: number,
  index: number,
  skipped: number[],
): { position: number; count: number; earlier: boolean } {
  const gone = new Set(skipped);
  let position = 1;
  let earlier = false;
  for (let i = 0; i < index; i += 1) {
    if (!gone.has(i)) {
      position += 1;
      earlier = true;
    }
  }
  return { position, count: total - [...gone].filter((i) => i !== index).length, earlier };
}

function markSkipped(i: number, skipped: boolean): void {
  const now = useTourStore.getState().skipped;
  if (skipped === now.includes(i)) return;
  useTourStore.setState({ skipped: skipped ? [...now, i] : now.filter((x) => x !== i) });
}

/**
 * Before the first page change or user action, what isn't on screen now
 * won't be: the phone's menu on a desktop, the sidebar on a phone, a profile
 * chip on a single profile install. Leaving them out up front keeps "1 of 6"
 * honest from the first card, and nobody waits on a step that can't come.
 */
function preSkipped(tour: Tour): number[] {
  const out: number[] = [];
  for (let i = 0; i < tour.steps.length; i += 1) {
    const step = tour.steps[i];
    if (step.page || step.advanceOn || step.waitUntilGone) break;
    if (!findAnchor(step.anchor)) {
      out.push(i);
      if (!step.optional) logSkip(tour, step, 'not on screen');
    }
  }
  return out;
}

let controller: AbortController | null = null;
let returnFocus: HTMLElement | null = null;
const logged = new Set<string>();

function logSkip(tour: Tour, step: TourStep, why: string): void {
  const key = `${tour.id}:${step.anchor}`;
  if (logged.has(key)) return;
  logged.add(key);
  console.info(`[tour] ${tour.id}: skipped ${step.anchor} (${why})`);
}

/** for tests */
export function resetTourLog(): void {
  logged.clear();
}

export function tourContext(): TourContext {
  let canDownload = true;
  try {
    if (typeof window.canDownload === 'function') canDownload = window.canDownload() !== false;
  } catch {
    /* keep the default */
  }
  return {
    isAdmin: viewerIsAdmin(),
    canDownload,
    mac: isMac(),
    phone: typeof window.matchMedia === 'function' && window.matchMedia(TOUR_PHONE_QUERY).matches,
  };
}

// ── resolving one step ──────────────────────────────────────────────────

type Found = { el: HTMLElement } | { missing: string } | null;

async function resolveStep(step: TourStep, signal: AbortSignal): Promise<Found> {
  const gone = step.waitUntilGone;
  if (gone && findShown(gone.selector)) {
    useTourStore.setState({ phase: 'blocked', blockedBy: gone.say, target: null });
    const cleared = await waitFor(() => !findShown(gone.selector), BLOCKED_WAIT_MS, signal);
    if (signal.aborted) return null;
    if (!cleared) return { missing: 'still blocked' };
    useTourStore.setState({ phase: 'finding', blockedBy: null });
  }
  if (step.page && !onPage(step.page)) {
    const there = await arriveAt(step.page, PAGE_WAIT_MS, signal);
    if (signal.aborted) return null;
    if (!there) return { missing: `could not open ${step.page}` };
  }
  const el = await waitFor(
    () => findAnchor(step.anchor),
    step.optional ? OPTIONAL_WAIT_MS : ANCHOR_WAIT_MS,
    signal,
  );
  if (signal.aborted) return null;
  return el ? { el } : { missing: 'not on screen' };
}

/** from `from`, in `dir`, the first step whose anchor is there */
async function go(from: number, dir: 1 | -1): Promise<void> {
  const { tour } = useTourStore.getState();
  if (!tour) return;
  controller?.abort();
  const ctrl = new AbortController();
  controller = ctrl;
  const before = useTourStore.getState().index;
  useTourStore.setState({ phase: 'finding', target: null, blockedBy: null, index: from });

  for (let i = from; i >= 0 && i < tour.steps.length; i += dir) {
    useTourStore.setState({ index: i });
    const step = tour.steps[i];
    // known missing and still missing: no point waiting for it again
    if (useTourStore.getState().skipped.includes(i) && !findAnchor(step.anchor)) {
      continue;
    }
    const found = await resolveStep(step, ctrl.signal);
    if (ctrl.signal.aborted || !found) return;
    if ('el' in found) {
      const shown = useTourStore.getState().shown + 1;
      markSkipped(i, false);
      useTourStore.setState({ phase: 'shown', target: found.el, index: i, shown, blockedBy: null });
      return;
    }
    markSkipped(i, true);
    if (!step.optional) logSkip(tour, step, found.missing);
  }
  if (ctrl.signal.aborted) return;
  // past the end: done. before the start: back to where we were
  if (dir === 1) end(true);
  else void go(Math.max(0, before), 1);
}

// ── the api ─────────────────────────────────────────────────────────────

export function startTour(tourOrId: Tour | string, opts: { adHoc?: boolean } = {}): boolean {
  const tour = typeof tourOrId === 'string' ? tourById(tourOrId) : tourOrId;
  if (!tour || !tour.steps.length) return false;
  if (useHelpStore.getState().open) useHelpStore.getState().close();
  if (!useTourStore.getState().tour) {
    const active = document.activeElement;
    returnFocus = active instanceof HTMLElement && active !== document.body ? active : null;
  }
  useTourStore.setState((s) => ({
    tour,
    index: 0,
    phase: 'finding',
    target: null,
    blockedBy: null,
    ctx: tourContext(),
    shown: 0,
    adHoc: !!opts.adHoc,
    skipped: preSkipped(tour),
    run: s.run + 1,
  }));
  void go(0, 1);
  return true;
}

export function nextStep(): void {
  const { tour, index } = useTourStore.getState();
  if (!tour) return;
  if (index >= tour.steps.length - 1) {
    end(true);
    return;
  }
  void go(index + 1, 1);
}

export function prevStep(): void {
  const { tour, index, skipped } = useTourStore.getState();
  if (!tour) return;
  let to = index - 1;
  while (to >= 0 && skipped.includes(to)) to -= 1;
  if (to < 0) return;
  void go(to, -1);
}

/** the target went away under a shown step (the page changed): find it again */
export function relocate(): void {
  const { tour, index } = useTourStore.getState();
  if (!tour) return;
  void go(index, 1);
}

export function end(completed = false): void {
  const { tour, shown, adHoc } = useTourStore.getState();
  controller?.abort();
  controller = null;
  useTourStore.setState({ tour: null, phase: 'idle', target: null, blockedBy: null, index: 0 });
  if (!tour) return;
  if (completed && !adHoc) {
    if (shown === 0) {
      window.showToast?.('Nothing in that tour is on screen for you right now.', 'info');
    } else {
      markDone(tour.id);
    }
  }
  const back = returnFocus;
  returnFocus = null;
  if (back?.isConnected) back.focus({ preventScroll: true });
}

export function isTourRunning(): boolean {
  return !!useTourStore.getState().tour;
}

/** one step with no counter: What's new's Show me */
export function spotlight(step: Omit<TourStep, 'optional'>): boolean {
  return startTour(
    { id: 'show-me', title: '', detail: '', pages: [], steps: [step] },
    { adHoc: true },
  );
}

// ── done, per browser ───────────────────────────────────────────────────

export function doneTours(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(DONE_KEY) || '[]');
    return Array.isArray(raw) ? raw.filter((x) => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

function markDone(id: string): void {
  try {
    const done = new Set(doneTours());
    done.add(id);
    localStorage.setItem(DONE_KEY, JSON.stringify([...done]));
  } catch {
    /* storage blocked */
  }
}

export { copy };
