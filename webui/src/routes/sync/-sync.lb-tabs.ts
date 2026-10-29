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
  { type: 'user_created', label: 'Playlists' },
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

/* ── SoulSync Discovery (sync-soulsync-discovery.js) ──────────────────────── */

export interface SsdRecord {
  kind: string;
  variant?: string;
  name?: string;
  track_count?: number;
  is_stale?: boolean;
  _never_generated?: boolean;
  /** The tab's section ('Daily Mix', 'Discovery playlists', 'Time Machine', ...). */
  group?: string;
  /** What the kind is, in a sentence, from the registry. */
  description?: string;
  variant_label?: string;
}

/** ssd_<kind>[_<variant>] — UPSERT-stable (124-129). */
export function soulsyncSyntheticId(kind: string, variant?: string): string {
  return `ssd_${kind}${variant ? `_${variant}` : ''}`;
}

interface SsdKind {
  kind: string;
  requires_variant?: boolean;
  name_template?: string;
  description?: string;
  variants?: (string | number)[];
  variant_labels?: Record<string, string>;
}

/** How the tab orders the kinds: the daily ones first, the big variant families last. */
export const SSD_KIND_ORDER = [
  'daily_mix',
  'listening_mix',
  'fresh_tape',
  'archives',
  'hidden_gems',
  'discovery_shuffle',
  'popular_picks',
  'time_machine',
  'genre_playlist',
  'seasonal_mix',
];

/** 'Time Machine — {variant}' -> 'Time Machine'. */
function ssdFamilyLabel(k: SsdKind): string {
  return (
    String(k.name_template || k.kind)
      .split('{variant}')[0]
      .replace(/[\s—–:>·.-]+$/, '')
      .trim() || k.kind
  );
}

/**
 * Every discovery playlist SoulSync can make, in tab order, grouped: the rows
 * already built merged with one never-built record per kind AND variant. It
 * used to synthesize singletons only, so a decade, genre, season or daily mix
 * that had never been built wasn't in the tab at all. Kinds are best-effort:
 * without them the tab falls back to the built rows.
 */
export async function fetchSsdRecords(): Promise<{ records: SsdRecord[]; error?: string }> {
  const data = await (await fetch('/api/personalized/playlists')).json();
  if (!data.success) return { records: [], error: String(data.error || 'Failed to load') };
  const existing = (data.playlists || []) as SsdRecord[];
  let kinds: SsdKind[] = [];
  try {
    const kindsData = await (await fetch('/api/personalized/kinds')).json();
    if (kindsData.success && Array.isArray(kindsData.kinds)) kinds = kindsData.kinds;
  } catch {
    // kinds endpoint optional — fall back to existing rows only
  }
  kinds = kinds.filter((k) => k && k.kind);
  if (!kinds.length) return { records: existing };

  const built = new Map(existing.map((p) => [`${p.kind}|${p.variant || ''}`, p]));
  const rank = (kind: string) => {
    const i = SSD_KIND_ORDER.indexOf(kind);
    return i < 0 ? SSD_KIND_ORDER.length : i;
  };
  const ordered = [...kinds].sort((a, b) => rank(a.kind) - rank(b.kind));
  const records: SsdRecord[] = [];
  const placed = new Set<string>();
  for (const k of ordered) {
    const group = k.requires_variant ? ssdFamilyLabel(k) : 'Discovery playlists';
    const variants = k.requires_variant ? (k.variants || []).map(String) : [''];
    // a built variant the resolver no longer lists (yesterday's sixth daily
    // mix) stays in its family
    for (const p of existing) {
      if (p.kind === k.kind && p.variant && !variants.includes(p.variant)) variants.push(p.variant);
    }
    for (const variant of variants) {
      const key = `${k.kind}|${variant}`;
      placed.add(key);
      const label = (k.variant_labels && k.variant_labels[variant]) || variant;
      const extra = { group, description: k.description || '', variant_label: label };
      const row = built.get(key);
      records.push(
        row
          ? { ...row, ...extra }
          : {
              kind: k.kind,
              variant,
              name: k.requires_variant
                ? String(k.name_template || `${k.kind} {variant}`).replace('{variant}', label)
                : k.name_template || k.kind,
              track_count: 0,
              is_stale: true,
              _never_generated: true,
              ...extra,
            },
      );
    }
  }
  // built rows of a kind the server no longer registers
  for (const p of existing) {
    if (!placed.has(`${p.kind}|${p.variant || ''}`)) records.push(p);
  }
  return { records };
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

/* ── Where an LB / Last.fm import is filed (the old post-discovery mirror's target, 10975-11004) ─ */

/** What detectLbSeries returns, as the vanilla reads it (10990-10993). */
export interface LbSeriesMatch {
  matched?: boolean;
  source?: string;
  series_id?: string;
  canonical_name?: string;
}

export interface LbMirrorTarget {
  source: string;
  sourcePlaylistId: string;
  name: string;
  owner: string;
}

/**
 * Where an LB mirror lands (10975-11004, 11011).
 *
 * Two rules, both load-bearing:
 * - A 'Last.fm Radio:' title prefix routes the mirror to source 'lastfm' so the
 *   Auto-Sync board groups it under Last.fm Radio and the cascade-delete hook
 *   targets the right source. This is also why those rows are unschedulable —
 *   autoSyncCanSchedulePlaylist excludes 'lastfm' (auto-sync.js 205-216).
 * - A matched rotating series collapses onto a synthetic id + canonical name so
 *   the next week/year UPSERTs onto ONE row instead of accumulating.
 *
 * The owner fallback is chosen from the RESOLVED source, so a Last.fm radio
 * whose series lookup rewrote the source follows it (11004).
 */
export function resolveLbMirrorTarget(
  playlistMbid: string,
  rawTitle: string,
  creator: string | undefined,
  series?: LbSeriesMatch | null,
): LbMirrorTarget {
  const title = rawTitle || 'ListenBrainz Playlist';
  let source = title.startsWith('Last.fm Radio:') ? 'lastfm' : 'listenbrainz';
  let sourcePlaylistId = playlistMbid;
  let name = title;
  if (series && series.matched) {
    source = series.source || source;
    sourcePlaylistId = series.series_id || sourcePlaylistId;
    name = series.canonical_name || name;
  }
  const ownerFallback = source === 'lastfm' ? 'Last.fm' : 'ListenBrainz';
  return { source, sourcePlaylistId, name, owner: creator || ownerFallback };
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
