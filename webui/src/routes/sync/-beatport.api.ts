/**
 * The Beatport tab's wire layer — all 19 endpoints beatport-ui.js calls.
 *
 * ABORT IS NOT UNIFORM IN THE VANILLA, and the split is deliberate rather than
 * accidental. Exactly these SEVEN pass core.js's getBeatportContentSignal, so
 * leaving the page cancels them mid-flight:
 *
 *     hero-tracks, new-releases, hype-picks, featured-charts, dj-charts,
 *     homepage/top-10-lists, homepage/top-10-releases-cards
 *
 * The other twelve — the two Top 100s, chart extraction, release metadata, both
 * enrichment calls, and all five genre endpoints — pass nothing. That is not an
 * oversight to correct while porting: those twelve are the ones that lead to a
 * DOWNLOAD, and cancelling a scrape halfway because the user changed tab is a
 * behaviour change, not a fix.
 *
 * Every function here takes an optional signal so React can still tear down
 * cleanly, but the UI layer should supply one only for the seven above unless a
 * divergence is being made on purpose. `VANILLA_ABORTED_ENDPOINTS` below is the
 * checkable form of that list.
 */

import type { BeatportScrapedTrack } from './-beatport.core';

/* ── Shapes ───────────────────────────────────────────────────────────────── */

export interface BeatportEnvelope {
  success?: boolean;
  error?: string;
}

export interface BeatportHeroTrack {
  title?: string;
  artist?: string;
  label?: string;
  url?: string;
  image_url?: string;
}

export interface BeatportRelease {
  title?: string;
  artist?: string;
  artists_string?: string;
  label?: string;
  url?: string;
  image_url?: string;
  rank?: number | string;
  type?: string;
  source?: string;
  badges?: string[];
}

export interface BeatportChart {
  name?: string;
  creator?: string;
  url?: string;
  image?: string;
}

export interface BeatportTop10Track {
  title?: string;
  artist?: string;
  label?: string;
  url?: string;
  rank?: number | string;
  artwork_url?: string;
}

export interface BeatportGenre {
  name: string;
  slug: string;
  id: string | number;
  url?: string;
  /** Filled in progressively by the image loader and cached on the object. */
  imageUrl?: string;
}

export interface BeatportReleaseMetadata extends BeatportEnvelope {
  tracks?: { artists: (string | { name: string })[] }[];
  album?: { name: string; images?: { url: string }[] };
  artist?: { id?: string; name?: string };
}

/**
 * The seven the vanilla cancels on page-leave, as data rather than prose, so
 * the UI slice can be checked against it instead of remembering.
 */
export const VANILLA_ABORTED_ENDPOINTS: readonly string[] = [
  '/api/beatport/hero-tracks',
  '/api/beatport/new-releases',
  '/api/beatport/hype-picks',
  '/api/beatport/featured-charts',
  '/api/beatport/dj-charts',
  '/api/beatport/homepage/top-10-lists',
  '/api/beatport/homepage/top-10-releases-cards',
];

/* ── The seven abortable homepage loads ───────────────────────────────────── */

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, signal ? { signal } : undefined);
  return (await response.json()) as T;
}

/** 47. */
export function fetchBeatportHeroTracks(
  signal?: AbortSignal,
): Promise<BeatportEnvelope & { tracks?: BeatportHeroTrack[] }> {
  return getJson('/api/beatport/hero-tracks', signal);
}

/** 384. */
export function fetchBeatportNewReleases(
  signal?: AbortSignal,
): Promise<BeatportEnvelope & { releases?: BeatportRelease[] }> {
  return getJson('/api/beatport/new-releases', signal);
}

/** 727. */
export function fetchBeatportHypePicks(
  signal?: AbortSignal,
): Promise<BeatportEnvelope & { releases?: BeatportRelease[] }> {
  return getJson('/api/beatport/hype-picks', signal);
}

/** 1070. */
export function fetchBeatportFeaturedCharts(
  signal?: AbortSignal,
): Promise<BeatportEnvelope & { charts?: BeatportChart[] }> {
  return getJson('/api/beatport/featured-charts', signal);
}

/** 1369. */
export function fetchBeatportDJCharts(
  signal?: AbortSignal,
): Promise<BeatportEnvelope & { charts?: BeatportChart[] }> {
  return getJson('/api/beatport/dj-charts', signal);
}

/** 1612 — ONE endpoint feeds both top-10 track lists. */
export function fetchBeatportTop10Lists(
  signal?: AbortSignal,
): Promise<
  BeatportEnvelope & { beatport_top10?: BeatportTop10Track[]; hype_top10?: BeatportTop10Track[] }
> {
  return getJson('/api/beatport/homepage/top-10-lists', signal);
}

/** 1764. */
export function fetchBeatportTop10Releases(
  signal?: AbortSignal,
): Promise<BeatportEnvelope & { releases?: BeatportRelease[] }> {
  return getJson('/api/beatport/homepage/top-10-releases-cards', signal);
}

/* ── Charts and downloads (the vanilla aborts NONE of these) ──────────────── */

/** 2172 / 2208. The `enrich=false` is not optional — enrichment is a second,
 *  progress-reporting step, and asking for it here would block the response. */
export function fetchBeatportTop100(
  variant: 'beatport' | 'hype',
  signal?: AbortSignal,
): Promise<BeatportEnvelope & { tracks?: BeatportScrapedTrack[] }> {
  const path = variant === 'hype' ? 'hype-top-100' : 'top-100';
  return getJson(`/api/beatport/${path}?enrich=false`, signal);
}

/** 2082. `chart_name` is prefixed by the caller ('Featured Chart: …' / 'DJ Chart: …'). */
export async function extractBeatportChart(
  chartUrl: string,
  chartName: string,
  signal?: AbortSignal,
): Promise<BeatportEnvelope & { tracks?: BeatportScrapedTrack[] }> {
  const response = await fetch('/api/beatport/chart/extract', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chart_url: chartUrl, chart_name: chartName, limit: 100, enrich: false }),
    signal,
  });
  return (await response.json()) as BeatportEnvelope & { tracks?: BeatportScrapedTrack[] };
}

/** 1876. */
export async function fetchBeatportReleaseMetadata(
  releaseUrl: string,
  signal?: AbortSignal,
): Promise<BeatportReleaseMetadata> {
  const response = await fetch('/api/beatport/release-metadata', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ release_url: releaseUrl }),
    signal,
  });
  return (await response.json()) as BeatportReleaseMetadata;
}

/* ── Enrichment (1938-1988) ───────────────────────────────────────────────── */

export interface EnrichStartResponse extends BeatportEnvelope {
  tracks?: BeatportScrapedTrack[];
  async?: boolean;
}

export interface EnrichProgress extends BeatportEnvelope {
  completed?: number;
  total?: number;
  current_track?: string;
  done?: boolean;
  tracks?: BeatportScrapedTrack[];
}

/** 1942. */
export async function startBeatportEnrichment(
  tracks: readonly BeatportScrapedTrack[],
  enrichmentId: string,
  signal?: AbortSignal,
): Promise<EnrichStartResponse> {
  const response = await fetch('/api/beatport/enrich-tracks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tracks, enrichment_id: enrichmentId }),
    signal,
  });
  return (await response.json()) as EnrichStartResponse;
}

/** 1959. The cache-buster is the vanilla's, kept so proxies behave the same. */
export async function fetchBeatportEnrichProgress(
  enrichmentId: string,
  cacheBuster: number,
  signal?: AbortSignal,
): Promise<EnrichProgress> {
  return getJson(`/api/beatport/enrich-progress/${enrichmentId}?_=${cacheBuster}`, signal);
}

export interface EnrichPollOptions {
  /** 1957: the vanilla's fixed 800ms gap between polls. */
  intervalMs?: number;
  /**
   * DELIBERATE DIVERGENCE, see the note on pollBeatportEnrichment. The vanilla
   * has neither of these.
   */
  maxConsecutiveErrors?: number;
  maxAttempts?: number;
  onProgress?: (progress: EnrichProgress) => void;
  sleep?: (ms: number) => Promise<void>;
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Poll an async enrichment to completion, returning the enriched tracks — or
 * the ORIGINALS if anything goes wrong, which is the vanilla's contract: a
 * failed enrichment must still let the download proceed with what was scraped.
 *
 * DELIBERATE DIVERGENCE from beatport-ui.js 1955-1980, flagged for Boulder and
 * approved. The vanilla polls inside `while (true)` with:
 *   - no attempt cap,
 *   - no abort — it ignores getBeatportContentSignal, alone among the fetches
 *     in that file, so leaving the page does not stop it, and
 *   - a `catch` INSIDE the loop that swallows a throwing poll and continues.
 * Those three together mean a persistently failing progress endpoint spins
 * every 800ms for the life of the tab.
 *
 * Here: an AbortSignal stops it, consecutive failures are capped, and there is
 * an overall attempt ceiling. All three exits return the original tracks, which
 * is what the vanilla does on every non-hanging failure — so the only behaviour
 * that changes is the hang itself.
 */
export async function pollBeatportEnrichment(
  enrichmentId: string,
  originalTracks: readonly BeatportScrapedTrack[],
  now: () => number,
  options: EnrichPollOptions = {},
  signal?: AbortSignal,
): Promise<BeatportScrapedTrack[]> {
  const {
    intervalMs = 800,
    maxConsecutiveErrors = 5,
    // 800ms x 2250 is ~30 minutes: far longer than a 100-track enrichment, and
    // still an end.
    maxAttempts = 2250,
    onProgress,
    sleep = defaultSleep,
  } = options;

  let consecutiveErrors = 0;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    if (signal?.aborted) return [...originalTracks];
    await sleep(intervalMs);
    if (signal?.aborted) return [...originalTracks];

    try {
      const progress = await fetchBeatportEnrichProgress(enrichmentId, now(), signal);
      // 1961: a well-formed 'not successful' answer ends the poll — it is the
      // backend saying the job is gone, not a transport hiccup.
      if (!progress.success) return [...originalTracks];

      consecutiveErrors = 0;
      onProgress?.(progress);

      if (progress.done) {
        return progress.tracks ? [...progress.tracks] : [...originalTracks];
      }
    } catch {
      consecutiveErrors++;
      if (consecutiveErrors >= maxConsecutiveErrors) return [...originalTracks];
    }
  }

  return [...originalTracks];
}

/**
 * 1938-1988 end to end: start, then either take the synchronous answer or poll.
 * Never throws — a failed enrichment yields the original tracks.
 */
export async function enrichBeatportTracks(
  tracks: readonly BeatportScrapedTrack[],
  enrichmentId: string,
  now: () => number,
  options: EnrichPollOptions = {},
  signal?: AbortSignal,
): Promise<BeatportScrapedTrack[]> {
  try {
    const started = await startBeatportEnrichment(tracks, enrichmentId, signal);
    // 1950: everything was cached, so the answer came back inline.
    if (started.success && started.tracks) return [...started.tracks];
    if (started.success && started.async) {
      return pollBeatportEnrichment(enrichmentId, tracks, now, options, signal);
    }
    return [...tracks];
  } catch {
    return [...tracks];
  }
}

/* ── Genres ───────────────────────────────────────────────────────────────── */

/** 2361. Note this one checks response.ok, unlike its neighbours. */
export async function fetchBeatportGenres(
  signal?: AbortSignal,
): Promise<{ genres?: BeatportGenre[] }> {
  const response = await fetch('/api/beatport/genres', signal ? { signal } : undefined);
  if (!response.ok) {
    throw new Error(`API returned ${response.status}: ${response.statusText}`);
  }
  return (await response.json()) as { genres?: BeatportGenre[] };
}

/** 2550 — one request per genre, run by the two-worker image loader. */
export async function fetchBeatportGenreImage(
  slug: string,
  id: string | number,
  signal?: AbortSignal,
): Promise<{ success?: boolean; image_url?: string } | null> {
  const response = await fetch(
    `/api/beatport/genre-image/${slug}/${id}`,
    signal ? { signal } : undefined,
  );
  // 2552: a non-ok response is simply no image — the card keeps its emoji.
  if (!response.ok) return null;
  return (await response.json()) as { success?: boolean; image_url?: string };
}

/**
 * 2827. Note the extra `message`: this endpoint reports its failure text there
 * rather than in `error` (2835), alone among the twenty. Typed explicitly so a
 * caller cannot silently read the field that is never populated.
 */
export async function fetchBeatportGenreHero(
  slug: string,
  id: string | number,
  signal?: AbortSignal,
): Promise<BeatportEnvelope & { releases?: BeatportRelease[]; message?: string }> {
  // 2828-2830: response.ok is checked HERE too — one of only two Beatport
  // endpoints that does, and it reports the status line to the user.
  const response = await fetch(
    `/api/beatport/genre/${slug}/${id}/hero`,
    signal ? { signal } : undefined,
  );
  if (!response.ok) {
    throw new Error(`API returned ${response.status}: ${response.statusText}`);
  }
  return (await response.json()) as BeatportEnvelope & {
    releases?: BeatportRelease[];
    message?: string;
  };
}

/** 3133. */
export function fetchBeatportGenreTop10Lists(
  slug: string,
  id: string | number,
  signal?: AbortSignal,
): Promise<
  BeatportEnvelope & { beatport_top10?: BeatportTop10Track[]; hype_top10?: BeatportTop10Track[] }
> {
  return getJson(`/api/beatport/genre/${slug}/${id}/top-10-lists`, signal);
}

/** 3454. */
export function fetchBeatportGenreTop10Releases(
  slug: string,
  id: string | number,
  signal?: AbortSignal,
): Promise<BeatportEnvelope & { releases?: BeatportRelease[] }> {
  return getJson(`/api/beatport/genre/${slug}/${id}/top-10-releases`, signal);
}

/** 3419 — the genre's own Top 100, unenriched for the same reason as 2172. */
export function fetchBeatportGenreTracks(
  slug: string,
  id: string | number,
  signal?: AbortSignal,
): Promise<BeatportEnvelope & { tracks?: BeatportScrapedTrack[] }> {
  return getJson(`/api/beatport/genre/${slug}/${id}/tracks?enrich=false`, signal);
}
