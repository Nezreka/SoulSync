/**
 * The Beatport tab's browse wire layer. (chart-type lists load through
 * /api/beatport/list instead, see -beatport.lists.ts.)
 *
 * ABORT IS NOT UNIFORM, and the split is deliberate. Exactly these SEVEN pass
 * core.js's getBeatportContentSignal, so leaving the page cancels them
 * mid-flight:
 *
 *     hero-tracks, new-releases, hype-picks, featured-charts, dj-charts,
 *     homepage/top-10-lists, homepage/top-10-releases-cards
 *
 * The rest (release metadata and the genre endpoints) pass nothing. Release
 * metadata leads to a DOWNLOAD, and cancelling it halfway because the user
 * changed tab is a behaviour change, not a fix.
 *
 * Every function here takes an optional signal so React can still tear down
 * cleanly, but the UI layer should supply one only for the seven above unless a
 * divergence is being made on purpose. `VANILLA_ABORTED_ENDPOINTS` below is the
 * checkable form of that list.
 */

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

/* ── Release download (not aborted: it leads to a download) ──────────────── */

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

/**
 * 3133. Carries `has_hype_section`, which the homepage twin does not: the genre
 * page hides the hype column outright when it is false (3219).
 */
export function fetchBeatportGenreTop10Lists(
  slug: string,
  id: string | number,
  signal?: AbortSignal,
): Promise<
  BeatportEnvelope & {
    beatport_top10?: BeatportTop10Track[];
    hype_top10?: BeatportTop10Track[];
    has_hype_section?: boolean;
  }
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
