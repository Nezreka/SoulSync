/**
 * Album reassign — the client half.
 *
 * TheHomeGuy: "Is there any way to re assign an album to a different artist?
 * ... i have had this happen when a featured artist is taken as the album
 * artist."
 *
 * The flow is artist → album → preview → apply, and the order is the safety
 * property: you pick a real artist and then one of THAT artist's releases, so
 * the identity handed to the import pipeline is one the source can answer
 * for. A typed-in name would produce an artist with no ids, no images and
 * nothing to resolve against.
 *
 * Nothing here writes tags or moves files. Apply stages a copy of each track
 * with a hint naming the chosen release; the import pipeline re-files them,
 * exactly as a single-track re-identify does (#889).
 *
 * Library v2: the album the request names is a lib2 row, and it says so —
 * `lib2:<id>`. The service refuses a bare id on purpose, because the hint this
 * flow writes is consumed against `lib2_track_files` and a legacy id would
 * quietly resolve to a different track.
 */

export interface ReassignArtist {
  id: string;
  name: string;
  image_url?: string | null;
}

export interface ReassignAlbum {
  id: string;
  name: string;
  year?: string | number | null;
  album_type?: string | null;
  total_tracks?: number | null;
  image_url?: string | null;
}

export interface ReassignPairing {
  local_id: unknown;
  local_title: string;
  local_track_number: number | null;
  target_title: string;
  target_track_number: number | null;
  /** 'track_number' | 'title' | null — shown so the user can see WHY. */
  matched_by: string | null;
  mapped: boolean;
}

export interface ReassignPreview {
  success: boolean;
  error?: string;
  pairings?: ReassignPairing[];
  mapped_count?: number;
  unmapped_count?: number;
}

export interface ReassignResult {
  success: boolean;
  error?: string;
  /** Present when the mapping is incomplete and the user must confirm. */
  needs_confirmation?: boolean;
  mapped_count?: number;
  unmapped_count?: number;
  staged?: { title: string; target_title: string }[];
  failed?: { title: string; error: string }[];
  skipped?: { title: string; reason: string }[];
}

export interface ReassignSource {
  id: string;
  label: string;
  active: boolean;
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  return (await response.json()) as T;
}

/** The Library-v2 subject for an album row. */
export function reassignSubject(albumId: number | string): string {
  return `lib2:${albumId}`;
}

/** Metadata sources with a live client, the active one flagged so the modal
 *  can pre-select it. Shared with the re-identify flow — same endpoint, same
 *  clients, and a source that cannot answer for an artist is no use here
 *  either. */
export async function fetchReassignSources(): Promise<ReassignSource[]> {
  try {
    const data = await getJson<{
      success?: boolean;
      sources?: { source?: string; label?: string; active?: boolean }[];
    }>('/api/reidentify/sources');
    return (data.sources ?? [])
      .filter((row) => row.source)
      .map((row) => ({
        id: String(row.source),
        label: String(row.label ?? row.source),
        active: Boolean(row.active),
      }));
  } catch {
    return [];
  }
}

export async function searchReassignArtists(
  source: string,
  query: string,
): Promise<ReassignArtist[]> {
  const url = `/api/reassign/artists?source=${encodeURIComponent(source)}&q=${encodeURIComponent(query)}`;
  const data = await getJson<{ success: boolean; artists?: ReassignArtist[] }>(url);
  return data.success ? (data.artists ?? []) : [];
}

export async function fetchReassignAlbums(
  source: string,
  artistId: string,
): Promise<ReassignAlbum[]> {
  const url = `/api/reassign/albums?source=${encodeURIComponent(source)}&artist_id=${encodeURIComponent(artistId)}`;
  const data = await getJson<{ success: boolean; albums?: ReassignAlbum[] }>(url);
  return data.success ? (data.albums ?? []) : [];
}

export async function previewReassign(body: {
  source: string;
  local_album_id: unknown;
  album_id: string;
}): Promise<ReassignPreview> {
  const response = await fetch('/api/reassign/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return (await response.json()) as ReassignPreview;
}

export async function applyReassign(body: {
  source: string;
  local_album_id: unknown;
  album_id: string;
  album_name: string;
  artist_id: string | null;
  artist_name: string;
  album_type?: string | null;
  replace: boolean;
  allow_partial: boolean;
}): Promise<ReassignResult> {
  const response = await fetch('/api/reassign/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return (await response.json()) as ReassignResult;
}

/** "12 of 12 tracks line up" / "9 of 12 tracks line up, 3 will stay put". */
export function describeMapping(preview: ReassignPreview): string {
  const mapped = preview.mapped_count ?? 0;
  const unmapped = preview.unmapped_count ?? 0;
  const total = mapped + unmapped;
  if (!total) return 'Nothing to line up';
  if (!unmapped) return `All ${total} tracks line up`;
  return `${mapped} of ${total} tracks line up — ${unmapped} would stay with the current artist`;
}

/** Why a pairing was proposed, in words. Blank when it was not matched. */
export function describeMatch(pairing: ReassignPairing): string {
  if (!pairing.mapped) return 'no match';
  if (pairing.matched_by === 'track_number') return 'by track number';
  if (pairing.matched_by === 'title') return 'by title';
  return 'matched';
}

/** The album row's subtitle: "2019 · album · 12 tracks", skipping blanks. */
export function albumBits(album: ReassignAlbum): string {
  return [
    album.year ? String(album.year).slice(0, 4) : '',
    album.album_type || '',
    album.total_tracks ? `${album.total_tracks} tracks` : '',
  ]
    .filter(Boolean)
    .join(' · ');
}
