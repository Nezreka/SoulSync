/**
 * what the player does at an intro, a recap or the credits, by the
 * profile's settings (Settings > You > Playback):
 *
 *   ask     a Skip button while the span plays
 *   auto    skipped on its own after a short countdown on the button, with a
 *           "Watch it" undo for a few seconds after
 *   never   nothing
 *
 * an intro is never skipped on its own in the first episode of a sitting:
 * you hear the theme once (the button is there instead). an intro someone
 * chose to watch (the undo, or going back into it) isn't skipped again.
 *
 * with a next episode queued, the credits are where "up next" counts down
 * (Play next episode at credits), unless a scene plays after them: then
 * the card says so and waits.
 */

import type { Marker, Skippable } from './timeline';

import { creditsStart } from './timeline';

export type SkipMode = 'ask' | 'auto' | 'never';

export interface SkipPrefs {
  intro: SkipMode;
  credits: SkipMode;
  recap: SkipMode;
  nextAtCredits: boolean;
}

export const DEFAULT_SKIP: SkipPrefs = {
  intro: 'ask',
  credits: 'ask',
  recap: 'ask',
  nextAtCredits: true,
};

/** seconds the auto-skip counts down on the button */
export const AUTO_COUNTDOWN = 5;
/** seconds the "Watch it" undo stays */
export const UNDO_SECONDS = 6;
/** up next at the credits: seconds before it plays (auto credits: quicker) */
export const CREDITS_COUNTDOWN = 10;
export const CREDITS_COUNTDOWN_AUTO = 4;
/** a sitting ends after this long with nothing played */
const SITTING_GAP_MS = 2 * 60 * 60 * 1000;

const MODES: readonly SkipMode[] = ['ask', 'auto', 'never'];

function mode(v: unknown, fallback: SkipMode): SkipMode {
  return MODES.includes(v as SkipMode) ? (v as SkipMode) : fallback;
}

export function skipPrefs(s?: {
  skip_intro?: string;
  skip_credits?: string;
  skip_recap?: string;
  next_at_credits?: boolean;
}): SkipPrefs {
  return {
    intro: mode(s?.skip_intro, DEFAULT_SKIP.intro),
    credits: mode(s?.skip_credits, DEFAULT_SKIP.credits),
    recap: mode(s?.skip_recap, DEFAULT_SKIP.recap),
    nextAtCredits: s?.next_at_credits ?? DEFAULT_SKIP.nextAtCredits,
  };
}

export type SkipPlan = 'none' | 'button' | 'auto';

/** what to show for a span playing now */
export function planFor(
  skip: Skippable | null,
  prefs: SkipPrefs,
  opts: { firstOfSitting: boolean; choseToWatch: boolean },
): SkipPlan {
  if (!skip) return 'none';
  const m = skip.kind === 'preview' ? prefs.recap : prefs[skip.kind];
  if (m === 'never') return 'none';
  if (m === 'ask') return 'button';
  if (opts.choseToWatch) return 'button';
  if (skip.kind === 'intro' && opts.firstOfSitting) return 'button';
  return 'auto';
}

/** how far through the auto countdown, 0..1, and the seconds left */
export function countdown(
  startedAt: number,
  position: number,
  seconds = AUTO_COUNTDOWN,
): { progress: number; left: number } {
  const used = Math.max(0, position - startedAt);
  return { progress: Math.min(1, used / seconds), left: Math.max(0, seconds - used) };
}

export interface CreditsUpNext {
  /** where the credits start */
  start: number;
  /** a scene plays after the credits: the card waits, never plays on */
  scene: { start: number; end: number } | null;
  /** seconds the countdown runs */
  seconds: number;
}

/**
 * up next at the credits: while the credits of an episode play with a next
 * one queued and the setting on. none once a scene after the credits starts
 * (the card comes back near the end as usual).
 */
export function creditsUpNext(args: {
  markers: readonly Marker[] | undefined;
  position: number;
  isEpisode: boolean;
  hasNext: boolean;
  prefs: SkipPrefs;
}): CreditsUpNext | null {
  const { markers, position, isEpisode, hasNext, prefs } = args;
  if (!isEpisode || !hasNext || !prefs.nextAtCredits || prefs.credits === 'never') return null;
  const start = creditsStart(markers);
  if (start === null || position < start - 0.5) return null;
  const credits = (markers ?? []).find((m) => m.kind === 'credits');
  const scene = credits?.post_credits ?? null;
  if (scene && position >= scene.start - 0.5) return null;
  return {
    start,
    scene,
    seconds: prefs.credits === 'auto' ? CREDITS_COUNTDOWN_AUTO : CREDITS_COUNTDOWN,
  };
}

/* ── a sitting: the run of videos since the player last sat idle ──────── */

let sitting: { first: string | null; last: number } = { first: null, last: 0 };

/** a video started (call once per item); true when it's the sitting's first */
export function noteStarted(key: string, now = Date.now()): boolean {
  if (!sitting.first || now - sitting.last > SITTING_GAP_MS) sitting = { first: key, last: now };
  sitting.last = now;
  return sitting.first === key;
}

export function isFirstOfSitting(key: string): boolean {
  return sitting.first === key;
}

/** tests */
export function resetSitting() {
  sitting = { first: null, last: 0 };
}
