import { apiClient, readJson } from '@/app/api-client';

import type {
  EnhancedSearchResponse,
  LibraryCheckResponse,
  SearchAlbum,
  SearchLabel,
  SearchTrack,
  SearchVideo,
} from './-search.types';

import { visibleSources } from './-search.helpers';
import { ALWAYS_CONFIGURED_SOURCES } from './-search.types';

/**
 * The main metadata search.
 *
 * No timeout: several providers are slow-and-rate-limited, and the vanilla
 * fetch had none — ky's default 10s would turn a working search into an error.
 * The caller owns cancellation through `signal`.
 */
export function fetchEnhancedSearch(
  query: string,
  source: string,
  signal?: AbortSignal,
): Promise<EnhancedSearchResponse> {
  return readJson<EnhancedSearchResponse>(
    apiClient.post('enhanced-search', {
      json: { query, source },
      timeout: false,
      signal,
    }),
  );
}

/**
 * YouTube music videos, which arrive as NDJSON rather than one JSON body.
 *
 * The server emits newline-delimited `{type:'videos', data:[...]}` chunks so the
 * grid can fill in progressively; `onChunk` is called per chunk with the
 * cumulative list. A partial trailing line is held back until its newline
 * arrives — splitting mid-object and JSON.parsing the fragment is the obvious
 * way to break this.
 */
export async function streamVideoSearch(
  query: string,
  onChunk: (videos: SearchVideo[]) => void,
  signal?: AbortSignal,
): Promise<SearchVideo[]> {
  const response = await fetch('/api/enhanced-search/source/youtube_videos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
    signal,
  });
  if (!response.ok || !response.body) return [];

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const videos: SearchVideo[] = [];

  for (;;) {
    const { done, value } = await reader.read();
    if (done || signal?.aborted) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    // The last element is either '' (clean break) or a partial object.
    buffer = lines.pop() ?? '';

    let touched = false;
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const parsed = JSON.parse(line) as { type?: string; data?: SearchVideo[] };
        if (parsed.type === 'videos' && Array.isArray(parsed.data)) {
          videos.push(...parsed.data);
          touched = true;
        }
      } catch {
        // A malformed line is skipped rather than killing the whole stream.
      }
    }
    if (touched && !signal?.aborted) onChunk([...videos]);
  }
  return videos;
}

/** Link/ID resolver — a pasted MusicBrainz id or provider URL. */
export interface IdLookupResponse extends EnhancedSearchResponse {
  available?: boolean;
  source?: string;
  message?: string;
  artists?: EnhancedSearchResponse['spotify_artists'];
  albums?: EnhancedSearchResponse['spotify_albums'];
  tracks?: EnhancedSearchResponse['spotify_tracks'];
}

export function lookupById(raw: string, signal?: AbortSignal): Promise<IdLookupResponse> {
  return readJson<IdLookupResponse>(
    apiClient.post('enhanced-search/by-id', {
      json: { query: raw },
      timeout: false,
      signal,
    }),
  );
}

/**
 * Which of these albums/tracks are already in the library or on the wishlist.
 *
 * The response is POSITIONAL — one entry per row sent, in request order. The
 * caller must key the answers back by identity, not by where a card ends up on
 * screen; see albumOwnershipByIdentity for why that distinction matters.
 *
 * Best-effort: a failure leaves everything unbadged rather than claiming
 * nothing is owned.
 */
export async function fetchLibraryCheck(
  albums: SearchAlbum[],
  tracks: SearchTrack[],
  signal?: AbortSignal,
): Promise<LibraryCheckResponse> {
  if (!albums.length && !tracks.length) return {};
  try {
    return await readJson<LibraryCheckResponse>(
      apiClient.post('enhanced-search/library-check', {
        json: {
          albums: albums.map((a) => ({ name: a.name, artist: a.artist })),
          tracks: tracks.map((t) => ({ name: t.name, artist: t.artist })),
        },
        signal,
      }),
    );
  } catch {
    return {};
  }
}

/** Labels for the query. Best-effort — the section simply stays empty. */
export async function fetchLabels(query: string, signal?: AbortSignal): Promise<SearchLabel[]> {
  try {
    const data = await readJson<{ labels?: SearchLabel[] }>(
      apiClient.post('labels/search', { json: { query }, signal }),
    );
    return data?.labels ?? [];
  } catch {
    return [];
  }
}

/** One artist's image, fetched lazily per card. */
export async function fetchArtistImage(
  artistId: string | number,
  source: string,
  name: string,
): Promise<string> {
  try {
    const data = await readJson<{ success?: boolean; image_url?: string }>(
      apiClient.get(`artist/${encodeURIComponent(String(artistId))}/image`, {
        searchParams: { source, name },
      }),
    );
    return data?.image_url ?? '';
  } catch {
    return '';
  }
}

/** `_experimental` payload → the set of enabled experimental source names. */
export function parseEnabledExperimental(data: unknown): Set<string> {
  const enabled = new Set<string>();
  const flags = (data as { _experimental?: Record<string, unknown> } | null)?._experimental;
  if (flags && typeof flags === 'object') {
    for (const [key, value] of Object.entries(flags)) {
      if (value && key.endsWith('_enabled')) enabled.add(key.slice(0, -'_enabled'.length));
    }
  }
  return enabled;
}

export interface ConfigStatus {
  configured: Record<string, boolean>;
  enabledExperimental: Set<string>;
}

/**
 * Which sources are usable — drives the dimmed icons.
 *
 * The rule is per-source, not one expression:
 *   - ALWAYS_CONFIGURED_SOURCES need no credentials, so they are always true.
 *   - **spotify** is `configured || metadata_available`, because Spotify Free
 *     works with no credentials when that opt-in source is on.
 *   - everything else is `configured` alone. An explicit `configured: false`
 *     means false even if `metadata_available` is true.
 *
 * I first wrote this as a single `configured ?? metadata_available` for every
 * source, which is wrong in both directions — it would light up an
 * unconfigured Deezer and mis-handle a missing flag. The `_experimental` flags
 * ride the same payload, so they are parsed from it here rather than fetched
 * twice.
 */
export async function fetchConfigStatus(): Promise<ConfigStatus> {
  try {
    const data = await readJson<
      Record<string, { configured?: boolean; metadata_available?: boolean }>
    >(apiClient.get('settings/config-status'));
    const enabledExperimental = parseEnabledExperimental(data);
    const configured: Record<string, boolean> = {};
    for (const source of visibleSources(enabledExperimental)) {
      if (ALWAYS_CONFIGURED_SOURCES.has(source)) {
        configured[source] = true;
      } else if (source === 'spotify') {
        const entry = data?.[source];
        configured[source] = Boolean(entry && (entry.configured || entry.metadata_available));
      } else {
        configured[source] = Boolean(data?.[source]?.configured);
      }
    }
    return { configured, enabledExperimental };
  } catch {
    // Optimistic: an unreachable config endpoint must not dim every icon and
    // make a working picker look broken.
    return { configured: {}, enabledExperimental: new Set() };
  }
}
