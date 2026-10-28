/**
 * Every chart-type beatport list (the top 10s, the top 100s, a genre's lists,
 * featured and dj charts) imports the way every other playlist source does:
 * into SoulSync, landing on it under Mirrored.
 *
 * the backend fetches the list by its ref and saves the live mirror under it,
 * so it refreshes with the chart, then identifies each track (isrc first) in
 * the background like any import. sync / download use the identified tracks
 * with their real albums. "Save snapshot" (a dated copy that never refreshes)
 * is on the imported playlist's menu.
 *
 * the oldest flow handed each list straight to the download modal as a fake
 * "Various Artists" compilation named after the chart; the one after it
 * identified the list in a beatport-only window. releases are not lists: a
 * release card still opens its album download.
 */

/** what a card asks for. a ref when the card knows its list, a url for charts. */
export interface BeatportListRequest {
  ref?: string;
  url?: string;
  /** what the overlay and toasts call it while it loads */
  label: string;
  genreName?: string;
}

export interface BeatportImportResult {
  success?: boolean;
  error?: string;
  playlist_id?: number;
  /** the canonical ref the mirror is filed under */
  source_playlist_id?: string;
  name?: string;
  track_count?: number;
}

/** POST /api/beatport/import: save the live list as a mirror. Answers its id. */
export async function importBeatportList(request: BeatportListRequest): Promise<number> {
  const response = await fetch('/api/beatport/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ref: request.ref,
      url: request.ref ? undefined : request.url,
      genre_name: request.genreName,
    }),
  });
  let data: BeatportImportResult;
  try {
    data = (await response.json()) as BeatportImportResult;
  } catch {
    throw new Error(`Couldn't import ${request.label} (${response.status})`);
  }
  if (!data.success || typeof data.playlist_id !== 'number') {
    throw new Error(data.error || `Couldn't import ${request.label}`);
  }
  return data.playlist_id;
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

export function isLiveBeatportRef(ref: string | number | null | undefined): boolean {
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
