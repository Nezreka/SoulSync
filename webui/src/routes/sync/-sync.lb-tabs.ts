/**
 * The three small tabs' data layer — ListenBrainz (sync-listenbrainz.js,
 * 364 lines), Last.fm Radio (sync-lastfm.js, 131) and SoulSync Discovery
 * (sync-soulsync-discovery.js, 286), the Phase-1c Discover-to-Sync trio.
 * All pure/wire logic lives here; the tab components render it.
 */

import type { MirrorPayload } from './-sync.import';

/* ── JSPF unwrap (sync-listenbrainz.js 89-105 / sync-lastfm.js 65-73) ─────── */

export interface LbCardData {
  mbid: string;
  title: string;
  creator: string;
  count: number;
}

/**
 * The Discover-page endpoints wrap each entry in JSPF shape:
 * {playlist: {identifier: 'https://.../<mbid>', title, creator,
 * annotation: {track_count}, track: [...]}}. Defaults differ per tab
 * ('ListenBrainz Playlist'/'ListenBrainz' vs 'Last.fm Radio'/'Last.fm').
 */
export function unwrapJspfPlaylist(
  entry: unknown,
  defaults: { title: string; creator: string },
  options: { nameFallback?: boolean } = {},
): LbCardData {
  // The LB variant falls back to inner.name (sync-listenbrainz.js 96); the
  // lastfm variant does NOT (sync-lastfm.js 68) — parameterized.
  const nameFallback = options.nameFallback ?? true;
  const p = entry as Record<string, unknown>;
  const inner = (p.playlist ?? p) as {
    identifier?: string;
    id?: string;
    title?: string;
    name?: string;
    creator?: string;
    track_count?: number;
    annotation?: { track_count?: number };
    track?: unknown[];
  };
  const mbid = (inner.identifier || '').split('/').pop() || inner.id || '';
  const title = inner.title || (nameFallback ? inner.name : undefined) || defaults.title;
  const creator = inner.creator || defaults.creator;
  let count = 0;
  if (inner.track_count) count = inner.track_count;
  else if (inner.annotation && inner.annotation.track_count) count = inner.annotation.track_count;
  else if (Array.isArray(inner.track) && inner.track.length > 0) count = inner.track.length;
  return { mbid, title, creator, count };
}

export type LbSubTabType = 'created_for_user' | 'user_created' | 'collaborative';

export const LB_SUB_TABS: readonly { type: LbSubTabType; label: string }[] = [
  { type: 'created_for_user', label: 'For You' },
  { type: 'user_created', label: 'My Playlists' },
  { type: 'collaborative', label: 'Collaborative' },
];

/** The per-sub-tab empty copy (sync-listenbrainz.js 80-84). */
export const LB_EMPTY_MESSAGES: Readonly<Record<LbSubTabType, string>> = {
  created_for_user:
    'No "For You" playlists yet. ListenBrainz publishes Weekly Exploration / Top Discoveries on its own schedule.',
  user_created: "You haven't created any ListenBrainz playlists yet.",
  collaborative: 'No collaborative playlists.',
};

export interface LbCategories {
  playlists: Record<LbSubTabType, unknown[]>;
  /** True when created-for answered the not-authenticated error (41-47). */
  unauthenticated: boolean;
}

/** The three parallel category fetches (33-53). */
export async function fetchLbCategories(): Promise<LbCategories> {
  const [createdFor, userPl, collab] = await Promise.all([
    fetch('/api/discover/listenbrainz/created-for').then((r) => r.json()),
    fetch('/api/discover/listenbrainz/user-playlists').then((r) => r.json()),
    fetch('/api/discover/listenbrainz/collaborative').then((r) => r.json()),
  ]);
  const unauthenticated = Boolean(
    !createdFor.success &&
    String(createdFor.error || '')
      .toLowerCase()
      .includes('not authenticated'),
  );
  return {
    unauthenticated,
    playlists: {
      created_for_user: createdFor.playlists || [],
      user_created: userPl.playlists || [],
      collaborative: collab.playlists || [],
    },
  };
}

/** GET /api/discover/listenbrainz/lastfm-radio (sync-lastfm.js 34-40). */
export async function fetchLastfmRadios(): Promise<{ playlists: unknown[]; error?: string }> {
  const data = await (await fetch('/api/discover/listenbrainz/lastfm-radio')).json();
  if (!data.success && data.error) return { playlists: [], error: String(data.error) };
  return { playlists: data.playlists || [] };
}

/** POST /api/discover/listenbrainz/refresh — best-effort (348-354). */
export async function postLbCacheRefresh(): Promise<void> {
  try {
    await fetch('/api/discover/listenbrainz/refresh', { method: 'POST' });
  } catch {
    // Non-fatal; the caller re-loads from the cache endpoints regardless.
  }
}

export interface LbTrack {
  track_name: string;
  artist_name: string;
  album_name: string;
  duration_ms: number;
  mbid: string;
  release_mbid: string;
  album_cover_url: string;
}

/** The on-demand track fetch + mapping (sync-listenbrainz.js 180-194). */
export async function fetchLbPlaylistTracks(mbid: string): Promise<LbTrack[]> {
  const response = await fetch(`/api/discover/listenbrainz/playlist/${encodeURIComponent(mbid)}`);
  if (!response.ok) throw new Error(`Failed to load playlist tracks (${response.status})`);
  const data = await response.json();
  const rows = (data.tracks || []) as {
    track_name?: string;
    artist_name?: string;
    album_name?: string;
    duration_ms?: number;
    recording_mbid?: string;
    mbid?: string;
    release_mbid?: string;
    album_cover_url?: string;
  }[];
  return rows.map((t) => ({
    track_name: t.track_name || '',
    artist_name: t.artist_name || '',
    album_name: t.album_name || '',
    duration_ms: t.duration_ms || 0,
    mbid: t.recording_mbid || t.mbid || '',
    release_mbid: t.release_mbid || '',
    album_cover_url: t.album_cover_url || '',
  }));
}

/* ── The LB card progress line (_refreshOneLbSyncCard 253-283) ────────────── */

export interface LbProgressInput {
  phase: string;
  lastSyncProgress?: {
    matched_tracks?: number;
    spotify_matches?: number;
    total_tracks?: number;
    spotify_total?: number;
    failed_tracks?: number;
  };
  spotifyTotal: number;
  spotifyMatches: number;
  discoveryProgress: number;
}

/**
 * "♪ T / ✓ M / ✗ F / P%": syncing/sync_complete read lastSyncProgress (failed
 * falls back to total-matched, percent is matched/total); other non-fresh
 * phases read the discovery counters (percent falls back to the discovery
 * progress when total is 0). Fresh → null (the line hides).
 */
export function lbCardProgressLine(input: LbProgressInput): string | null {
  if (input.phase === 'fresh') return null;
  if ((input.phase === 'syncing' || input.phase === 'sync_complete') && input.lastSyncProgress) {
    const sp = input.lastSyncProgress;
    const matched = sp.matched_tracks || sp.spotify_matches || 0;
    const total = sp.total_tracks || sp.spotify_total || 0;
    const failed = sp.failed_tracks !== undefined ? sp.failed_tracks : Math.max(0, total - matched);
    const pct = total > 0 ? Math.round((matched / total) * 100) : 0;
    return `♪ ${total} / ✓ ${matched} / ✗ ${failed} / ${pct}%`;
  }
  const total = input.spotifyTotal || 0;
  const matched = input.spotifyMatches || 0;
  const failed = Math.max(0, total - matched);
  const pct = total > 0 ? Math.round((matched / total) * 100) : input.discoveryProgress || 0;
  return `♪ ${total} / ✓ ${matched} / ✗ ${failed} / ${pct}%`;
}

/* ── SoulSync Discovery (sync-soulsync-discovery.js) ──────────────────────── */

export interface SsdRecord {
  kind: string;
  variant?: string;
  name?: string;
  track_count?: number;
  is_stale?: boolean;
  _never_generated?: boolean;
}

/** ssd_<kind>[_<variant>] — UPSERT-stable (124-129). */
export function soulsyncSyntheticId(kind: string, variant?: string): string {
  return `ssd_${kind}${variant ? `_${variant}` : ''}`;
}

/**
 * Existing generated rows + synthetic never-generated SINGLETON kinds (33-57).
 * Kinds are best-effort — a kinds failure must never sink the tab.
 */
export async function fetchSsdRecords(): Promise<{ records: SsdRecord[]; error?: string }> {
  const data = await (await fetch('/api/personalized/playlists')).json();
  if (!data.success) return { records: [], error: String(data.error || 'Failed to load') };
  const existing = (data.playlists || []) as SsdRecord[];
  const seen = new Set(existing.map((p) => `${p.kind}|${p.variant || ''}`));
  let kinds: { kind: string; requires_variant?: boolean; name_template?: string }[] = [];
  try {
    const kindsData = await (await fetch('/api/personalized/kinds')).json();
    if (kindsData.success && Array.isArray(kindsData.kinds)) kinds = kindsData.kinds;
  } catch {
    // kinds endpoint optional — fall back to existing rows only
  }
  const synthetic: SsdRecord[] = kinds
    .filter((k) => !k.requires_variant && !seen.has(`${k.kind}|`))
    .map((k) => ({
      kind: k.kind,
      variant: '',
      name: k.name_template || k.kind,
      track_count: 0,
      is_stale: true,
      _never_generated: true,
    }));
  return { records: [...existing, ...synthetic] };
}

/** Staleness line + color (86-90). */
export function ssdStaleness(record: SsdRecord): { text: string; color: string } {
  const stale = Boolean(record.is_stale);
  return {
    text: record._never_generated
      ? 'Tap “Refresh & Mirror” to generate'
      : stale
        ? 'Stale — refresh to regenerate'
        : 'Ready',
    color: stale ? '#facc15' : '#14b8a6',
  };
}

export interface SsdTrack {
  track_name?: string;
  artist_name?: string;
  album_name?: string;
  duration_ms?: number;
  album_cover_url?: string;
  spotify_track_id?: string;
  itunes_track_id?: string;
  deezer_track_id?: string;
  source?: string;
}

/**
 * Project generator tracks into the mirror contract (170-202): tracks already
 * carry provider ids, so extra_data holds the full matched_data block as a
 * JSON STRING (the wire format the backend stores).
 */
export function buildSsdMirrorTracks(tracks: SsdTrack[]): MirrorPayload['tracks'] {
  return tracks.map((t) => {
    const trackId = t.spotify_track_id || t.itunes_track_id || t.deezer_track_id || '';
    const provider = t.spotify_track_id
      ? 'spotify'
      : t.itunes_track_id
        ? 'itunes'
        : t.deezer_track_id
          ? 'deezer'
          : t.source || 'unknown';
    const albumObject: Record<string, unknown> = { name: t.album_name || '' };
    if (t.album_cover_url) {
      albumObject.images = [{ url: t.album_cover_url, height: 600, width: 600 }];
    }
    const extra = trackId
      ? JSON.stringify({
          discovered: true,
          provider,
          confidence: 1.0,
          matched_data: {
            id: trackId,
            name: t.track_name || '',
            artists: [{ name: t.artist_name || '' }],
            album: albumObject,
            duration_ms: t.duration_ms || 0,
            image_url: t.album_cover_url || null,
            source: provider,
          },
        })
      : null;
    return {
      track_name: t.track_name || '',
      artist_name: t.artist_name || '',
      album_name: t.album_name || '',
      duration_ms: t.duration_ms || 0,
      image_url: t.album_cover_url || null,
      source_track_id: trackId,
      extra_data: extra,
    };
  });
}

/** The full mirror body for a refreshed kind (217-228). */
export function buildSsdMirrorPayload(
  kind: string,
  variant: string,
  name: string,
  tracks: SsdTrack[],
): MirrorPayload {
  return {
    source: 'soulsync_discovery',
    source_playlist_id: soulsyncSyntheticId(kind, variant || undefined),
    name,
    tracks: buildSsdMirrorTracks(tracks),
    description: `Personalized ${kind}${variant ? ' · ' + variant : ''} — regenerates on Auto-Sync refresh.`,
    owner: 'SoulSync',
    image_url: '',
  };
}

/** POST the kind's generator (144-152). */
export async function postSsdRefresh(
  kind: string,
  variant: string,
): Promise<{ success?: boolean; error?: string; playlist?: SsdRecord; tracks?: SsdTrack[] }> {
  const url = variant
    ? `/api/personalized/playlist/${encodeURIComponent(kind)}/${encodeURIComponent(variant)}/refresh`
    : `/api/personalized/playlist/${encodeURIComponent(kind)}/refresh`;
  return (await (await fetch(url, { method: 'POST' })).json()) as {
    success?: boolean;
    error?: string;
    playlist?: SsdRecord;
    tracks?: SsdTrack[];
  };
}
