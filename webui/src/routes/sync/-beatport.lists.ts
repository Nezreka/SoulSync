/**
 * Every chart-type beatport list (the top 10s, the top 100s, a genre's lists,
 * featured and dj charts) opens the way every other playlist source opens:
 * as a beatport discovery, in the same React modal.
 *
 * the old flow handed each list straight to the download modal as a fake
 * "Various Artists" compilation named after the chart. the chart name landed
 * in album tags and folders, track ids were the chart position (so a second
 * chart inherited the first one's sync matches), and nothing was ever matched
 * to a metadata source. now the backend fetches the list by its ref, discovery
 * identifies each track (isrc first), the list is mirrored by that ref so it
 * can refresh, and sync / download use the identified tracks with their real
 * albums.
 *
 * releases are not lists: a release card still opens its album download.
 */

/** what a card asks for. a ref when the card knows its list, a url for charts. */
export interface BeatportListRequest {
  ref?: string;
  url?: string;
  /** what the overlay and toasts call it while it loads */
  label: string;
  genreName?: string;
}

export interface BeatportChartTrack {
  id: string;
  name: string;
  artists: string[];
  isrc?: string;
  duration_ms?: number;
  album?: string;
  image_url?: string;
  url?: string;
}

/** the chart_data discovery/start takes, as /api/beatport/list returns it */
export interface BeatportChartData {
  hash: string;
  ref: string;
  name: string;
  image?: string;
  url?: string;
  creator?: string;
  refreshable?: boolean;
  track_count: number;
  tracks: BeatportChartTrack[];
}

export function beatportListUrl(request: BeatportListRequest): string {
  const params = new URLSearchParams();
  if (request.ref) params.set('ref', request.ref);
  else if (request.url) params.set('url', request.url);
  if (request.genreName) params.set('genre_name', request.genreName);
  return `/api/beatport/list?${params.toString()}`;
}

export async function fetchBeatportList(request: BeatportListRequest): Promise<BeatportChartData> {
  const response = await fetch(beatportListUrl(request));
  let data: { success?: boolean; error?: string } & Partial<BeatportChartData>;
  try {
    data = (await response.json()) as typeof data;
  } catch {
    throw new Error(`Beatport list failed (${response.status})`);
  }
  if (!data.success || !data.ref || !Array.isArray(data.tracks)) {
    throw new Error(data.error || `Beatport list failed (${response.status})`);
  }
  if (data.tracks.length === 0) throw new Error(`No tracks found in ${data.name || request.label}`);
  return data as BeatportChartData;
}

export interface BeatportListEnv {
  showToast: (message: string, type?: string) => void;
  showLoadingOverlay: (message: string) => void;
  hideLoadingOverlay: () => void;
  /** the beatport vertical's state for this list, if it has one */
  phaseOf: (id: string) => string | undefined;
  seed: (id: string, playlist: Record<string, unknown>) => void;
  open: (id: string) => void;
  startDiscovery: (id: string, chart: BeatportChartData) => void | Promise<void>;
}

/** phases a list can be re-identified from without stepping on running work */
const RESTARTABLE = new Set(['fresh']);

let opening = false;

export function resetBeatportListLatch(): void {
  opening = false;
}

/**
 * fetch, seed, open, identify. one click at a time: a second click while a
 * list is loading is dropped rather than racing the first.
 *
 * a list already being identified, synced or downloaded just reopens its
 * modal; Rediscover in the modal starts it over with this week's tracks.
 */
export async function openBeatportList(
  request: BeatportListRequest,
  env: BeatportListEnv,
): Promise<void> {
  if (opening) return;
  if (!request.ref && !request.url) {
    env.showToast(`No link for ${request.label}`, 'error');
    return;
  }
  opening = true;
  try {
    env.showLoadingOverlay(`Loading ${request.label}...`);
    const chart = await fetchBeatportList(request);
    env.hideLoadingOverlay();

    const phase = env.phaseOf(chart.ref);
    env.seed(chart.ref, {
      name: chart.name,
      tracks: chart.tracks,
      description: `${chart.tracks.length} tracks from ${chart.name}`,
      source: 'beatport',
      image_url: chart.image ?? '',
      chart_data: chart,
    });
    env.open(chart.ref);
    if (phase === undefined || RESTARTABLE.has(phase)) {
      await env.startDiscovery(chart.ref, chart);
    }
  } catch (error) {
    env.hideLoadingOverlay();
    env.showToast(
      `Error loading ${request.label}: ${error instanceof Error ? error.message : String(error)}`,
      'error',
    );
  } finally {
    opening = false;
  }
}

/** the refs for the lists the tab and the genre page show */
export const beatportRefs = {
  home: (list: 'top-10' | 'hype-10' | 'top-100' | 'hype-100') => list,
  genre: (
    genre: { slug: string; id: string | number },
    list: 'top-10' | 'hype-10' | 'top-100' | 'hype-100',
  ) => `genre:${genre.slug}:${genre.id}:${list}`,
};

/**
 * a ref beatport keeps changing (the top 10s and 100s, a genre's lists, a
 * chart): the lists worth snapshotting. a release never changes, and an old
 * random chart hash isn't a ref at all.
 */
const LIVE_REF =
  /^(?:(?:top|hype)-(?:10|100)|genre:[a-z0-9][a-z0-9-]*:\d+:(?:top|hype)-(?:10|100)|chart:[a-z0-9][a-z0-9-]*:\d+)$/;

export function isLiveBeatportRef(ref: string | null | undefined): boolean {
  return LIVE_REF.test(String(ref ?? '').toLowerCase());
}

export interface BeatportSnapshotResult {
  success?: boolean;
  error?: string;
  playlist_id?: number;
  name?: string;
  track_count?: number;
}

/** POST /api/beatport/snapshot: freeze the list as it is today. */
export async function saveBeatportSnapshot(ref: string): Promise<BeatportSnapshotResult> {
  const response = await fetch('/api/beatport/snapshot', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref }),
  });
  try {
    return (await response.json()) as BeatportSnapshotResult;
  } catch {
    return { success: false, error: `Snapshot failed (${response.status})` };
  }
}
