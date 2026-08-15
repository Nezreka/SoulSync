/**
 * Motion for the Year in Listening story.
 *
 * A Wrapped slide that arrives fully formed reads like a report. The numbers
 * have to LAND — that is most of what separates "here are your stats" from a
 * moment. So the count-up and the stagger are part of the feature, not polish
 * on top of it.
 *
 * The maths lives here, pure and tested, because animation bugs are the kind
 * that only show up as "it looked wrong for a second" and never as a failing
 * assertion otherwise.
 */

/** Ease-out cubic: fast at first, settling at the end. */
export function easeOutCubic(t: number): number {
  const clamped = Math.min(1, Math.max(0, t));
  return 1 - (1 - clamped) ** 3;
}

/**
 * The value to show at `elapsed` ms into a count-up to `target`.
 *
 * Always lands EXACTLY on the target — an eased tween that stops at 1,246 of
 * 1,247 is worse than no animation at all, because the number on screen is
 * then simply wrong. Rounding happens here rather than at the call site so
 * there is one place that can be off by one.
 */
export function countUpValue(target: number, elapsed: number, duration: number): number {
  if (!Number.isFinite(target)) return 0;
  if (!Number.isFinite(elapsed) || elapsed <= 0) return 0;
  if (!Number.isFinite(duration) || duration <= 0) return target;
  if (elapsed >= duration) return target;
  return Math.round(target * easeOutCubic(elapsed / duration));
}

/**
 * How long a number should take to count up.
 *
 * Scaled by magnitude: 12 ticking to its value over the same 1.4s that 40,000
 * uses looks broken, because the eye reads the individual digits. Clamped at
 * both ends so nothing is instant and nothing outstays the slide.
 */
export function countUpDuration(target: number): number {
  const magnitude = Math.abs(target);
  if (!Number.isFinite(magnitude) || magnitude <= 0) return 0;
  const scaled = 400 + Math.log10(magnitude + 1) * 340;
  return Math.min(1600, Math.max(500, Math.round(scaled)));
}

/**
 * Delay before the nth item in a run appears.
 *
 * Capped so a long list (twelve months, eight discoveries) does not leave the
 * last item arriving after the reader has already moved on. Past the cap
 * everything remaining shares the final delay — still a wave, but a bounded
 * one.
 */
export function staggerDelay(index: number, step = 60, max = 520): number {
  if (!Number.isFinite(index) || index <= 0) return 0;
  return Math.min(max, Math.round(index * step));
}

/**
 * Honour the OS "reduce motion" setting.
 *
 * Not optional politeness: for some people this kind of movement is a
 * vestibular trigger, and a story that cannot be turned off is a story they
 * cannot use. Everything animated here degrades to its final state.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}
