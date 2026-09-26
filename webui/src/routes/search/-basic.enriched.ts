/**
 * The enriched download flow's calls. one metadata provider end to end:
 * search it, load the release from it, start the download with its ids.
 *
 * the old vanilla modal mixed providers mid-flow (suggestions from one, ids
 * from another) and did the file matching in the browser. here the server
 * maps files to tracks, and an answer from a provider other than the one
 * asked is refused on both ends.
 */

import { HTTPError } from 'ky';

import { apiClient, readJson } from '@/app/api-client';

import type { BasicTrack } from './-basic.types';
import type { SearchAlbum, SearchTrack } from './-search.types';

import { fetchEnhancedSearch } from './-search.api';

export interface EnrichedProvider {
  source: string;
  label: string;
  /** the user's primary metadata source, the modal opens on it */
  active?: boolean;
}

/** which providers can be searched right now. not admin-gated, unlike config-status */
export async function fetchProviders(): Promise<EnrichedProvider[]> {
  const data = await readJson<{ sources?: EnrichedProvider[] } | EnrichedProvider[]>(
    apiClient.get('reidentify/sources'),
  );
  const list = Array.isArray(data) ? data : (data?.sources ?? []);
  return list.filter((p) => p && p.source);
}

export class ProviderUnavailable extends Error {}

/**
 * Releases (or tracks) on ONE provider. the server answers from another source
 * when the asked one can't; that is exactly the mixing the rebuild exists to
 * stop, so it's refused here instead of shown.
 */
export async function searchProvider(
  query: string,
  source: string,
  signal?: AbortSignal,
): Promise<{ albums: SearchAlbum[]; tracks: SearchTrack[] }> {
  const data = await fetchEnhancedSearch(query, source, signal);
  const served = String(data.primary_source || data.metadata_source || source).toLowerCase();
  if (data.source_available === false || served !== source.toLowerCase()) {
    throw new ProviderUnavailable(`${source} isn't available right now`);
  }
  return { albums: data.spotify_albums ?? [], tracks: data.spotify_tracks ?? [] };
}

/** a picked file as the server wants it */
export interface EnrichedFile {
  username: string;
  filename: string;
  size: number;
  title: string | null;
  artist: string | null;
  album: string | null;
  track_number: number | null;
  duration: number | null;
  bitrate: number | null;
  quality: string;
}

export function toEnrichedFile(track: BasicTrack, albumFallback = ''): EnrichedFile {
  return {
    username: track.username,
    filename: track.filename,
    size: track.size,
    title: track.title,
    artist: track.artist,
    album: track.album || albumFallback || null,
    track_number: track.track_number,
    duration: track.duration,
    bitrate: track.bitrate,
    quality: track.quality,
  };
}

/** the key the server uses for a file in assignments */
export function fileKey(file: { username: string; filename: string }): string {
  return `${file.username}::${file.filename}`;
}

export interface ReleaseTrack {
  index: number;
  name: string;
  track_number: number;
  disc_number: number;
  duration_ms: number;
}

export interface Assignment {
  file_key: string;
  /** null = not downloaded */
  track_index: number | null;
  confidence: number;
}

export interface MatchResponse {
  success: boolean;
  error?: string;
  album?: {
    id: string;
    name: string;
    release_date?: string;
    image_url?: string;
    total_tracks?: number;
    artists?: { name: string }[];
  };
  tracks?: ReleaseTrack[];
  assignments?: Assignment[];
}

/** read the server's own error sentence off a failed call */
async function orError<T extends { success?: boolean; error?: string }>(
  call: Promise<T>,
): Promise<T> {
  try {
    return await call;
  } catch (error) {
    if (error instanceof HTTPError) {
      const body = error.data as T | undefined;
      if (body && typeof body === 'object') return body;
    }
    throw error;
  }
}

export function matchRelease(payload: {
  source: string;
  album_id: string;
  album_name: string;
  artist: string;
  files: EnrichedFile[];
}): Promise<MatchResponse> {
  return orError(
    readJson<MatchResponse>(
      apiClient.post('search/enriched/match', { json: payload, timeout: false }),
    ),
  );
}

export interface StartResponse {
  success: boolean;
  error?: string;
  message?: string;
  batch_id?: string;
  blocked?: boolean;
  blocked_name?: string;
  explicit_blocked?: boolean;
}

export function startEnriched(payload: Record<string, unknown>): Promise<StartResponse> {
  return orError(
    readJson<StartResponse>(
      apiClient.post('search/enriched/start', { json: payload, timeout: false }),
    ),
  );
}

// ── tag it yourself ───────────────────────────────────────────────────────

export interface ManualAlbum {
  name: string;
  artist: string;
  date: string;
  genre: string;
  /** album | live | ep | single */
  type: string;
  /** a pasted link */
  image_url?: string;
  /** an uploaded cover, as a data: url */
  image_data?: string;
}

export interface ManualTrack {
  file_key: string;
  title: string;
  track_number: number;
  disc_number: number;
}

export function startManual(payload: {
  files: EnrichedFile[];
  album: ManualAlbum;
  tracks: ManualTrack[];
  ignore_blocklist?: boolean;
}): Promise<StartResponse> {
  return orError(
    readJson<StartResponse>(
      apiClient.post('search/manual/start', { json: payload, timeout: false }),
    ),
  );
}

/**
 * a readable title out of a filename: no folder, no extension, no leading
 * track number, no "Artist - " prefix when it's the album's own artist.
 * soulseek paths use backslashes.
 */
export function titleFromFilename(filename: string, artist = ''): string {
  let stem = filename.replace(/\\/g, '/').split('/').pop() ?? filename;
  stem = stem.replace(/\.[a-z0-9]{2,5}$/i, '');
  // a leading number is a track number only with a separator after it
  // ("01 - ", "01. "), in disc-track form ("1-03 ") or zero padded ("02 ").
  // "7 rings" is a title
  stem = stem.replace(/^\s*(?:\d{1,2}[-.]\d{1,3}\s+|\d{1,3}\s*[-._)\]]\s*|0\d\s+)/, '');
  if (artist) {
    const prefix = new RegExp(`^${artist.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*-\\s*`, 'i');
    stem = stem.replace(prefix, '');
  }
  return stem.replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
}
