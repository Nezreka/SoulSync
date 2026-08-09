/**
 * Fix Track Match — pure core + api, ported React-native from the
 * wishlist-tools flow (openDiscoveryFixModal 22-154, searchDiscoveryFix
 * 179-266, lookupDiscoveryFixByMbid 275-331, renderDiscoveryFixResults sort
 * 336-348, selectDiscoveryFixTrack 371-445, unmatchDiscoveryTrack 646-709;
 * parseMusicBrainzMbid from shared-helpers.js 4656-4675). The vanilla flow
 * reads script-scoped registries a React page cannot populate — that is WHY
 * this port exists (P4a review finding #1).
 *
 * One structural simplification the config model buys: the vanilla's
 * backend-identifier ladder (tidal url_hash → tidal_playlist_id etc.,
 * 385-405/649-653) dissolves — React states are keyed by the source's OWN id,
 * which IS the backend identifier for every source.
 *
 * Knowing fix (bug #5 again): the search cascade puts the LIVE metadata
 * source first; the vanilla reordered on the never-updated
 * currentMusicSourceName, so the active-first reorder never actually fired.
 */

import type { SourceVerticalConfig, SyncSourceId } from './-sync.sources';

/** A track as the search_tracks endpoints return it. */
export interface FixTrack {
  id?: string;
  name?: string;
  artists?: (string | { name?: string })[] | string;
  album?: string | Record<string, unknown>;
  duration_ms?: number;
  image_url?: string | null;
}

/* ── The search cascade (179-236) ─────────────────────────────────────────── */

export interface FixSearchSource {
  key: string;
  endpoint: string;
  label: string;
}

/**
 * MusicBrainz sits last (1 req/sec rate limit) unless it is the active
 * primary; Discogs is absent — no track-level search API (223-224).
 */
export const FIX_SEARCH_SOURCES: readonly FixSearchSource[] = [
  { key: 'spotify', endpoint: '/api/spotify/search_tracks', label: 'Spotify' },
  { key: 'deezer', endpoint: '/api/deezer/search_tracks', label: 'Deezer' },
  { key: 'itunes', endpoint: '/api/itunes/search_tracks', label: 'iTunes' },
  { key: 'musicbrainz', endpoint: '/api/musicbrainz/search_tracks', label: 'MusicBrainz' },
];

/** Active source first, everything else in list order as fallbacks (232-236). */
export function orderedFixSearchSources(activeLabel: string): FixSearchSource[] {
  const active = activeLabel.toLowerCase();
  const activeIdx = FIX_SEARCH_SOURCES.findIndex((s) => active.includes(s.key));
  return activeIdx > 0
    ? [FIX_SEARCH_SOURCES[activeIdx], ...FIX_SEARCH_SOURCES.filter((_, i) => i !== activeIdx)]
    : [...FIX_SEARCH_SOURCES];
}

/* ── Result ordering (340-348) ────────────────────────────────────────────── */

const VARIANT_PATTERN =
  /\b(live|remix|remaster|refix|cover|acoustic|demo|instrumental|radio edit|single version|deluxe|edition|soundtrack|from .* film|from .* movie|bonus track)\b|\b\w+ mix\b/i;
const ALBUM_VARIANT_PATTERN =
  /\b(live|greatest hits|best of|collection|compilation|soundtrack|from .* film|from .* movie|remaster|deluxe|redux|expanded|anniversary)\b/i;

/** Standard album versions first; live/remix/cover/soundtrack variants last. */
export function sortFixResults(tracks: FixTrack[]): FixTrack[] {
  return [...tracks].sort((a, b) => {
    const aVariant =
      VARIANT_PATTERN.test(a.name || '') ||
      ALBUM_VARIANT_PATTERN.test(typeof a.album === 'string' ? a.album : '');
    const bVariant =
      VARIANT_PATTERN.test(b.name || '') ||
      ALBUM_VARIANT_PATTERN.test(typeof b.album === 'string' ? b.album : '');
    if (aVariant !== bVariant) return aVariant ? 1 : -1;
    return 0;
  });
}

/* ── MBID parsing (shared-helpers.js 4656-4675, verbatim) ─────────────────── */

const MB_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function parseMusicBrainzMbid(input: string | null | undefined): string | null {
  if (!input || typeof input !== 'string') return null;
  let candidate = input.trim();
  if (!candidate) return null;
  const urlMatch = candidate.match(/musicbrainz\.org\/[a-z-]+\/([0-9a-f-]+)/i);
  if (urlMatch) candidate = urlMatch[1];
  candidate = candidate.split(/[?#]/)[0].trim().toLowerCase();
  return MB_UUID_RE.test(candidate) ? candidate : null;
}

/* ── Backend endpoints ────────────────────────────────────────────────────── */

/**
 * The update_match platform segment (408): mirrored rides youtube, the two
 * link sources hyphenate, everyone else passes through — including qobuz.
 */
export function fixApiPlatform(source: SyncSourceId): string {
  if (source === 'mirrored') return 'youtube';
  if (source === 'spotify_public') return 'spotify-public';
  if (source === 'itunes_link') return 'itunes-link';
  return source;
}

/**
 * The unmatch API base (656-662). NOTE the vanilla ladder has NO qobuz arm —
 * a qobuz unmatch falls through to /api/youtube with the qobuz identifier
 * (catalogued as live bug #7). Transcribed bug-compatible; the flip phase
 * decides whether to fix it knowingly.
 */
export function unmatchApiBase(source: SyncSourceId): string {
  switch (source) {
    case 'tidal':
      return '/api/tidal';
    case 'deezer':
      return '/api/deezer';
    case 'spotify_public':
      return '/api/spotify-public';
    case 'itunes_link':
      return '/api/itunes-link';
    case 'beatport':
      return '/api/beatport';
    case 'listenbrainz':
      return '/api/listenbrainz';
    default:
      return '/api/youtube';
  }
}

/** The update_match body (410-426); #843 sends the source track alongside. */
export function buildUpdateMatchBody(
  sourceId: string,
  trackIndex: number,
  sourceTrack: string,
  sourceArtist: string,
  track: FixTrack,
): Record<string, unknown> {
  return {
    identifier: sourceId,
    track_index: trackIndex,
    original_name: sourceTrack || '',
    original_artist: sourceArtist || '',
    spotify_track: {
      id: track.id,
      name: track.name,
      artists: track.artists,
      album: track.album,
      duration_ms: track.duration_ms,
      image_url: track.image_url || null,
    },
  };
}

/* ── Wire calls ───────────────────────────────────────────────────────────── */

export async function searchFixTracks(
  endpoint: string,
  track: string,
  artist: string,
): Promise<FixTrack[]> {
  const params = new URLSearchParams();
  if (track) params.set('track', track);
  if (artist) params.set('artist', artist);
  params.set('limit', '50');
  const response = await fetch(`${endpoint}?${params.toString()}`);
  const data = (await response.json()) as { tracks?: FixTrack[] };
  return data.tracks ?? [];
}

/** The two vanilla not-found flavours carry different messages (308 vs 318). */
export type MbLookupResult =
  | { kind: 'found'; track: FixTrack }
  | { kind: 'missing' } // 404 — "Double-check the MBID"
  | { kind: 'no-id' }; // 200 without an id — plain not-found

/** GET /api/musicbrainz/recording/<mbid> (304-321). */
export async function lookupMbRecording(mbid: string): Promise<MbLookupResult> {
  const response = await fetch(`/api/musicbrainz/recording/${encodeURIComponent(mbid)}`);
  if (response.status === 404) return { kind: 'missing' };
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const track = (await response.json()) as FixTrack | null;
  return track && track.id ? { kind: 'found', track } : { kind: 'no-id' };
}

export async function postUpdateMatch(
  config: SourceVerticalConfig,
  body: Record<string, unknown>,
): Promise<{ error?: string }> {
  const response = await fetch(`/api/${fixApiPlatform(config.id)}/discovery/update_match`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return (await response.json()) as { error?: string };
}

export async function postUnmatch(
  config: SourceVerticalConfig,
  sourceId: string,
  trackIndex: number,
): Promise<{ success?: boolean; error?: string }> {
  const response = await fetch(`${unmatchApiBase(config.id)}/discovery/unmatch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ identifier: sourceId, track_index: trackIndex }),
  });
  return (await response.json()) as { success?: boolean; error?: string };
}

/* ── Mirrored organize preference (shared-helpers.js 1339-1357, 1479-1504) ──
   `ref` is the BARE source id (the url_hash): the vanilla builds a prefixed
   ref and then normalizePlaylistOrganizeRef strips it before the wire request
   (1113-1115) — the stored source_playlist_id is the bare hash. */

export async function fetchOrganizePreference(ref: string, source: string): Promise<boolean> {
  try {
    const response = await fetch(
      `/api/mirrored-playlists/resolve?ref=${encodeURIComponent(ref)}&source=${encodeURIComponent(source)}`,
    );
    const data = (await response.json()) as {
      found?: boolean;
      playlist?: { organize_by_playlist?: boolean };
    };
    return Boolean(data.found && data.playlist?.organize_by_playlist);
  } catch {
    return false;
  }
}

/** Resolve + PATCH, false on any failure (setMirroredOrganizePreference 1479). */
export async function setOrganizePreference(
  ref: string,
  source: string,
  enabled: boolean,
): Promise<boolean> {
  try {
    const resolveResponse = await fetch(
      `/api/mirrored-playlists/resolve?ref=${encodeURIComponent(ref)}&source=${encodeURIComponent(source)}`,
    );
    const data = (await resolveResponse.json()) as { found?: boolean; playlist?: { id?: number } };
    if (!data.found || !data.playlist?.id) return false;
    const patchResponse = await fetch(`/api/mirrored-playlists/${data.playlist.id}/preferences`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ organize_by_playlist: Boolean(enabled) }),
    });
    if (!patchResponse.ok) return false;
    const patchData = (await patchResponse.json()) as { error?: string };
    return !patchData.error;
  } catch {
    return false;
  }
}
