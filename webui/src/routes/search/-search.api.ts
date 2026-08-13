import { apiClient, readJson, shellClient } from '@/app/api-client';

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
  // `source` is omitted rather than sent empty when there is none to send, and
  // 'auto' means the same thing — that is enhancedSearchFetch's contract
  // (shared-helpers.js:22-23), and it is what makes the server fan out across
  // sources instead of searching one called ''.
  const json: { query: string; source?: string } = { query };
  if (source && source !== 'auto') json.source = source;
  return readJson<EnhancedSearchResponse>(
    apiClient.post('enhanced-search', { json, timeout: false, signal }),
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

/**
 * One artist's image, fetched lazily per card.
 *
 * `source` is omitted when it is spotify, which is the vanilla's rule
 * (search.js:741). Worth being explicit that this is a port, not a judgement:
 * the endpoint reads a missing `source` as "use the configured primary source"
 * (web_server.py:10092), so viewing Spotify results while Deezer is primary
 * resolves Spotify ids against Deezer. That asymmetry is left exactly as it is —
 * changing it would alter which images resolve, for every user, on a hunch.
 *
 * `name` matters for sources that store no artist art at all: MusicBrainz has
 * MBIDs only, so the resolver searches iTunes/Deezer by name instead.
 */
export async function fetchArtistImage(
  artistId: string | number,
  source: string,
  name: string,
): Promise<string> {
  const searchParams: Record<string, string> = {};
  if (source && source !== 'spotify') searchParams.source = source;
  if (name) searchParams.name = name;
  try {
    const data = await readJson<{ success?: boolean; image_url?: string }>(
      apiClient.get(`artist/${encodeURIComponent(String(artistId))}/image`, { searchParams }),
    );
    // The endpoint answers 200 with success:false on failure, so the flag is the
    // only signal — a url next to success:false is not one to trust.
    return data?.success && data.image_url ? data.image_url : '';
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

/** What /status says about the configured primary source. */
export interface MetadataStatus {
  /** The user's primary metadata source, or '' when /status did not say. */
  source: string;
  enabledExperimental: Set<string>;
}

/**
 * Read the primary source (and the experimental flags) from /status.
 *
 * `/status` rather than `/api/settings`, because settings is admin-only and
 * answers 403 for a member profile — which used to make every non-admin fall
 * back to Spotify no matter what the admin had configured
 * (shared-helpers.js:344-350).
 *
 * `metadata_source` is a DICT (`{source, connected, …}`). Reading it as a string
 * was a real bug: `SOURCE_LABELS[<dict>]` is always undefined, so the picker
 * silently stayed on Spotify. The legacy string shape is still accepted.
 */
export async function fetchMetadataStatus(): Promise<MetadataStatus> {
  try {
    // shellClient, not apiClient: /status is a root path, not under /api.
    const data = await readJson<{ metadata_source?: unknown }>(shellClient.get('status'));
    const raw = data?.metadata_source;
    const source =
      raw && typeof raw === 'object'
        ? String((raw as { source?: unknown }).source ?? '')
        : String(raw ?? '');
    return { source, enabledExperimental: parseEnabledExperimental(data) };
  } catch {
    return { source: '', enabledExperimental: new Set() };
  }
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
