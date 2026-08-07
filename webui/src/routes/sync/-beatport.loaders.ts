/**
 * The five section loaders, from beatport-ui.js's five load…() functions.
 *
 * They look like five copies of one function and are not. Each answers three
 * questions differently, and the differences are the whole content of this file:
 *
 *  1. WHICH FIELD carries the payload — `tracks`, `releases` or `charts`.
 *  2. WHAT A FAILURE SAYS. The two error-block sections have TWO distinct
 *     messages each: one for "the API said no", which forwards the backend's own
 *     `data.error`, and one for "the fetch threw". The charts and DJ sections
 *     say nothing at all.
 *  3. WHETHER A FAILURE IS AN EXCEPTION. `useBeatportSection` renders
 *     `error.message` for an error-block section, so the two that show a message
 *     THROW it. The three silent ones return null instead — there is no message
 *     to carry, and throwing would only invite someone to render it later.
 *
 * Every message below is the string the vanilla puts on screen, not the one it
 * logs — for hype picks those differ ('No hype picks available' is displayed,
 * 'No hype picks found' is logged), which is exactly the sort of thing that gets
 * copied from the wrong line.
 */

import {
  type BeatportChart,
  type BeatportHeroTrack,
  type BeatportRelease,
  fetchBeatportDJCharts,
  fetchBeatportFeaturedCharts,
  fetchBeatportHeroTracks,
  fetchBeatportHypePicks,
  fetchBeatportNewReleases,
} from './-beatport.api';

/**
 * A page-leave abort must reach the hook untouched — it tests `error.name` to
 * tell "the user left" from "Beatport is down", and re-wrapping it would turn a
 * navigation into an error block.
 */
function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

/* ── Hero (47-61) ─────────────────────────────────────────────────────────── */

/**
 * Returns null rather than throwing: the hero has no error renderer. On failure
 * the vanilla calls setupBeatportSliderWithPlaceholders, which re-wires the
 * placeholder slides ALREADY IN THE PAGE MARKUP (163-168) — it draws nothing
 * new, so the port draws nothing either.
 */
export async function loadBeatportHero(signal: AbortSignal): Promise<BeatportHeroTrack[] | null> {
  const data = await fetchBeatportHeroTracks(signal);
  if (data.success && data.tracks && data.tracks.length > 0) return data.tracks;
  return null;
}

/* ── New releases (384-410) ───────────────────────────────────────────────── */

export async function loadBeatportNewReleases(signal: AbortSignal): Promise<BeatportRelease[]> {
  let data;
  try {
    data = await fetchBeatportNewReleases(signal);
  } catch (error) {
    if (isAbort(error)) throw error;
    // 408: the thrown-error arm has its OWN copy and does not surface the
    // exception's message.
    throw new Error('Failed to load releases');
  }
  if (data.success && data.releases && data.releases.length > 0) return data.releases;
  // 401: the backend's own message wins when it sent one.
  throw new Error(data.error || 'No releases available');
}

/* ── Hype picks (727-754) ─────────────────────────────────────────────────── */

export async function loadBeatportHypePicks(signal: AbortSignal): Promise<BeatportRelease[]> {
  let data;
  try {
    data = await fetchBeatportHypePicks(signal);
  } catch (error) {
    if (isAbort(error)) throw error;
    throw new Error('Failed to load hype picks');
  }
  if (data.success && data.releases && data.releases.length > 0) return data.releases;
  // 750 — 'available', not the 'found' that the console line above it uses.
  throw new Error(data.error || 'No hype picks available');
}

/* ── Featured charts (1063-1085) and DJ charts (1362-1384) ────────────────── */

/**
 * Both return null on every failure. These two have no error renderer at all —
 * loadBeatportFeaturedCharts just returns false and the `.then(success => …)`
 * skips everything — and they never mark themselves initialised either, which
 * makes them the only two sections that retry on a later visit.
 */
export async function loadBeatportFeaturedCharts(
  signal: AbortSignal,
): Promise<BeatportChart[] | null> {
  const data = await fetchBeatportFeaturedCharts(signal);
  if (data.success && data.charts && data.charts.length > 0) return data.charts;
  return null;
}

export async function loadBeatportDJCharts(signal: AbortSignal): Promise<BeatportChart[] | null> {
  const data = await fetchBeatportDJCharts(signal);
  if (data.success && data.charts && data.charts.length > 0) return data.charts;
  return null;
}

/* ── The hero's click payload (128-138) ───────────────────────────────────── */

/**
 * The hero is the only section that BUILDS a release object rather than passing
 * one through: its API returns tracks, and the click handler wants a release.
 * The four defaults are the vanilla's (134-137) and are reached whenever the
 * scrape came back thin.
 */
export function heroClickRelease(track: BeatportHeroTrack) {
  return {
    url: track.url,
    title: track.title || 'Unknown Title',
    artist: track.artist || 'Unknown Artist',
    label: track.label || 'Unknown Label',
    image_url: track.image_url || '',
  };
}

/**
 * 128: the hero attaches no handler at all unless the url is real, so an
 * artwork-only slide is not clickable and shows no pointer cursor.
 *
 * The releases and hype-picks sliders apply the same test to their cards
 * (500-501, 959). The top-10 release list is the exception — it wires every
 * card unconditionally (1834), which is the one place the handler's own
 * 'No release URL available' toast can actually be seen.
 */
export function isBeatportReleaseClickable(url: string | undefined | null): boolean {
  return Boolean(url) && url !== '#' && url !== '';
}
