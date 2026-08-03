/**
 * Re-identify (#889 — library.js:7517-7703): re-file an imported track under a
 * different release via staging + a single-use hint the auto-import worker
 * picks up. Requests + the pure ranking; the modal UI lives in
 * -ui/reidentify-modal.tsx.
 */

export interface ReidentifySource {
  source: string;
  label?: string;
  active?: boolean;
}

export interface ReidentifyResult {
  source?: string;
  track_id?: unknown;
  track_title?: string;
  album_name?: string;
  artist_name?: string;
  album_type?: string;
  year?: number | string;
  total_tracks?: number;
  image_url?: string;
  isrc?: string;
}

export async function fetchReidentifySources(): Promise<ReidentifySource[]> {
  try {
    const response = await fetch('/api/reidentify/sources');
    const data = await response.json();
    return (data && data.sources) || [];
  } catch {
    return [];
  }
}

export async function reidentifySearchRequest(
  source: string,
  query: string,
): Promise<ReidentifyResult[]> {
  const url = `/api/reidentify/search?source=${encodeURIComponent(source)}&q=${encodeURIComponent(query)}`;
  const response = await fetch(url);
  const data = await response.json();
  return (data && data.results) || [];
}

/**
 * ISRC-bearing rows first — an ISRC match is provably the same recording
 * (7618-7621). The sort is stable, so each group keeps the source's order.
 */
export function rankReidentifyResults(rows: ReidentifyResult[]): ReidentifyResult[] {
  return rows
    .map((r, i) => ({ r, i }))
    .sort((a, b) => (b.r.isrc ? 1 : 0) - (a.r.isrc ? 1 : 0) || a.i - b.i)
    .map(({ r }) => r);
}

/** The "1992 · 13 tracks" detail line (7626-7628). */
export function reidentifyResultBits(result: ReidentifyResult): string {
  const bits: string[] = [];
  if (result.year) bits.push(String(result.year));
  if (result.total_tracks) {
    bits.push(`${result.total_tracks} track${result.total_tracks === 1 ? '' : 's'}`);
  }
  return bits.join(' · ');
}

/** Stage the re-file; resolves to the vanilla's success toast line (7696). */
export async function applyReidentifyRequest(
  libraryTrackId: unknown,
  selected: ReidentifyResult,
  replace: boolean,
): Promise<string> {
  const response = await fetch('/api/reidentify/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      library_track_id: libraryTrackId,
      source: selected.source,
      track_id: selected.track_id,
      replace,
    }),
  });
  const data = await response.json();
  if (!response.ok || !data.success) throw new Error(data.error || 'Re-identify failed');
  return `Re-filing under “${data.album_name || 'the chosen release'}” — it'll update after the next import pass.`;
}
