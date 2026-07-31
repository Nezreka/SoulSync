/**
 * The Adventurousness dial — a draggable orb riding an animated wave.
 *
 * Transcribed from `_advWave` (30), `_advState` (32), `_advColor` (40),
 * `_advWaveY` (50), `_advDraw` (59), `_advApply` (87), `_advCommitNow` (112),
 * `_advCommitLive` (133), `_advInitDrag` (140) and `loadAdventurousnessDial`
 * (168) — read end to end.
 *
 * Shares `discover.adventurousness` with the Settings slider, so the two stay
 * in sync; this is the same stored value, not a second setting.
 */

/** `{ value: 0.3, ... }` (30) — the dial starts at Balanced, not at 0. */
export const ADV_DEFAULT_VALUE = 0.3;

/** Orb + aura inset from the track edges, px (48). */
export const ADV_ORB_PAD = 18;

/** `now - _advLastLive < 450` (136) — live commits while dragging. */
export const ADV_LIVE_THROTTLE_MS = 450;

/** `for (let i = 0; i <= N; i++)` with N = 90 (67) — 91 points, not 90. */
export const ADV_WAVE_POINTS = 90;

/** The SVG viewBox: 1000 wide, 80 tall, wave centred on y=40. */
export const ADV_VIEW_WIDTH = 1000;
export const ADV_VIEW_HEIGHT = 80;
export const ADV_WAVE_CENTRE = 40;

export const ADV_ENDPOINT = '/api/discover/adventurousness';

/**
 * The four labels (32-37).
 *
 * Bands are strictly ascending and each bound is EXCLUSIVE, so 0.40 is
 * "Adventurous" and not "Balanced".
 */
export function advState(v: number): string {
  if (v < 0.12) return 'Playing it safe';
  if (v < 0.4) return 'Balanced';
  if (v < 0.7) return 'Adventurous';
  return 'Deep cuts only';
}

/**
 * Green (120°) → blue (220°) through cyan (40-44).
 *
 * Blue rather than red at the far end on purpose: red reads "danger / bad",
 * and being adventurous is not an error state. The hue is ROUNDED to a whole
 * degree, so the string is stable frame to frame.
 *
 * `light || 55` — passing 0 yields 55, not black. That is the vanilla's
 * behaviour and no caller passes 0, but it is transcribed rather than
 * "corrected" to `??`.
 */
export function advColor(v: number, light?: number, alpha?: number | null): string {
  const hue = 120 + 100 * Math.max(0, Math.min(1, v));
  const l = light || 55;
  return alpha != null
    ? `hsla(${hue.toFixed(0)}, 85%, ${l}%, ${alpha})`
    : `hsl(${hue.toFixed(0)}, 85%, ${l}%)`;
}

/**
 * Wave height at position u (0..1) for adventurousness v (50-58).
 *
 * Amplitude and frequency both climb with v, and above 0 a DETUNED second
 * harmonic (2.2×, not 2×) adds wobble — an exact octave would just look like a
 * taller sine. `phase` is the animation clock and is passed in rather than read
 * from module state, so this is testable.
 */
export function advWaveY(u: number, v: number, phase: number): number {
  const amp = 4.5 + v * 10;
  const freq = 1.25 + v * 1.9;
  let y = ADV_WAVE_CENTRE + amp * Math.sin(freq * u * Math.PI * 2 + phase);
  if (v > 0) {
    y += v * amp * 0.38 * Math.sin(freq * 2.2 * u * Math.PI * 2 + phase * 1.6 + 1.3);
  }
  return y;
}

/** `phase += 0.022 + v * 0.045` (65) — waves faster the more adventurous. */
export function advNextPhase(phase: number, v: number): number {
  return phase + 0.022 + v * 0.045;
}

/**
 * The wave path (66-72), and the filled area that closes it to the baseline.
 *
 * x is rounded to 0.1 and y likewise — the vanilla builds the string with
 * `toFixed(1)`, which both shortens the attribute and stops sub-pixel churn.
 */
export function advWavePath(v: number, phase: number, points = ADV_WAVE_POINTS): string {
  let line = '';
  for (let i = 0; i <= points; i++) {
    const u = i / points;
    line +=
      (i === 0 ? 'M ' : ' L ') +
      (u * ADV_VIEW_WIDTH).toFixed(1) +
      ' ' +
      advWaveY(u, v, phase).toFixed(1);
  }
  return line;
}

export function advAreaPath(linePath: string): string {
  return `${linePath} L ${ADV_VIEW_WIDTH} ${ADV_VIEW_HEIGHT} L 0 ${ADV_VIEW_HEIGHT} Z`;
}

/**
 * Where the orb sits along the track, as a fraction (80-82).
 *
 * The orb's travel is inset by `ADV_ORB_PAD` at both ends, so the wave has to
 * be sampled at the INSET x rather than at raw `v` — otherwise the handle
 * drifts off the line. Without the inset the orb gets sliced by the card's
 * `overflow: hidden` at the extremes.
 */
export function advOrbU(v: number, trackWidth: number): number {
  const w = trackWidth || 1;
  return (ADV_ORB_PAD + v * (w - 2 * ADV_ORB_PAD)) / w;
}

/** The orb's vertical position as a percentage of the viewBox (82). */
export function advOrbTopPercent(v: number, phase: number, trackWidth: number): string {
  return ((advWaveY(advOrbU(v, trackWidth), v, phase) / ADV_VIEW_HEIGHT) * 100).toFixed(2) + '%';
}

/** The CSS `left` for orb and aura (99, 106). */
export function advOrbLeft(v: number): string {
  return `calc(${ADV_ORB_PAD}px + ${v.toFixed(4)} * (100% - ${2 * ADV_ORB_PAD}px))`;
}

export function advAuraBackground(v: number): string {
  return `radial-gradient(circle, ${advColor(v, 50, 0.16)} 0%, transparent 72%)`;
}

export interface AdvStyles {
  value: number;
  color: string;
  colorBright: string;
  state: string;
  orbLeft: string;
  auraBackground: string;
}

/**
 * Everything `_advApply` sets (87-111).
 *
 * Two lightnesses: 55 for the line, orb glow and aura, 62 for the orb fill and
 * the state label — the brighter one is what makes the label readable against
 * the card.
 */
export function advStyles(rawValue: number): AdvStyles {
  const value = Math.max(0, Math.min(1, rawValue));
  return {
    value,
    color: advColor(value, 55),
    colorBright: advColor(value, 62),
    state: advState(value),
    orbLeft: advOrbLeft(value),
    auraBackground: advAuraBackground(value),
  };
}

/** `r.width ? (clientX - r.left) / r.width : 0` (144-147) — unclamped here. */
export function advValueFromX(clientX: number, rectLeft: number, rectWidth: number): number {
  return rectWidth ? (clientX - rectLeft) / rectWidth : 0;
}

/**
 * The animation loop skips its work while the page is hidden (63).
 *
 * `track.offsetParent === null` means the Discover page is not displayed — the
 * rAF keeps ticking but computes nothing, so a background tab does not burn
 * cycles rebuilding a 91-point path 60 times a second.
 */
export function advShouldDraw(trackIsVisible: boolean): boolean {
  return trackIsVisible;
}

/** Only a NUMBER from the server overwrites the value (176). */
export function advValueFromResponse(
  data: { value?: unknown } | null | undefined,
  current: number,
): number {
  return typeof data?.value === 'number' ? data.value : current;
}

/**
 * Whether a live (mid-drag) commit fires (133-138).
 *
 * Releasing the pointer resets `_advLastLive` to 0 (162) so the FINAL value
 * always commits regardless of when the last throttled one went — otherwise a
 * drag ending inside the throttle window would save a stale value.
 */
export function advShouldCommitLive(now: number, lastLive: number): boolean {
  return now - lastLive >= ADV_LIVE_THROTTLE_MS;
}

export const ADV_RELEASE_RESETS_THROTTLE = true;

/**
 * The two rec sections re-fetch on commit via `refresh()`, NOT `load()` (119-126).
 *
 * `load()` coalesces with an in-flight request, so a rapid drag would fold the
 * newest dial value into a request that used an older one and render stale.
 * `refresh()` bypasses that. The distinction is the whole reason the dial feels
 * responsive.
 */
export const ADV_DEPENDENT_SECTIONS = ['listening-recs', 'recommended-artists'] as const;
export const ADV_REFETCH_BYPASSES_COALESCE = true;

// ── The shared loader pool ──────────────────────────────────────────────────

/**
 * `_runLoadersLimited` (190).
 *
 * Bounded concurrency over a shared cursor, and it NEVER rejects — a failing
 * loader is swallowed exactly as `Promise.allSettled` did. Firing all ~20
 * section loaders at once put ~20 heavy queries in contention on Flask+GIL and
 * the page took tens of seconds to become usable.
 */
export async function runLoadersLimited(thunks: (() => unknown)[], limit = 5): Promise<void> {
  let cursor = 0;
  async function worker(): Promise<void> {
    while (cursor < thunks.length) {
      const idx = cursor++;
      try {
        await thunks[idx]();
      } catch {
        /* allSettled semantics */
      }
    }
  }
  const pool: Promise<void>[] = [];
  for (let w = 0; w < Math.min(limit, thunks.length); w++) pool.push(worker());
  await Promise.all(pool);
}
