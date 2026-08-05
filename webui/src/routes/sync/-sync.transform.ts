/**
 * The unified discovery-result transform.
 *
 * The vanilla maps backend discovery results into the modal's row shape in
 * FOURTEEN places (a socket + a poll transform per vertical), and they
 * drifted: only some map wing-it (live bug #4), only beatport/LB have an
 * error arm, LB shows only the first Spotify artist. This module is the ONE
 * mapper, per-source only where the backend payloads genuinely differ (the
 * source-track field), with three documented knowing unifications:
 *
 * 1. Wing-it rows map to '🎯 Wing It' on EVERY source and transport (the
 *    vanilla's socket-only handling was the transport-dependent rendering
 *    bug).
 * 2. The 'error' status arm (vanilla: beatport + LB only) applies everywhere —
 *    an errored row renders '❌ Error' and gets the Fix action instead of
 *    masquerading as Not Found.
 * 3. Spotify artists render as the full joined list (the tidal socket mapper,
 *    sync-services.js 641-650); LB's first-artist-only variant (11049) is
 *    dropped.
 *
 * Row field names keep the vanilla's yt_* spelling — they are the shared
 * discovery modal's wire format (generateTableRowsFromState reads them), and
 * renaming them would force a translation layer at every seam.
 */

import type { SyncSourceId } from './-sync.sources';

/** A row in the shape the shared discovery modal renders. */
export interface DiscoveryRow {
  index: number;
  yt_track: string;
  yt_artist: string;
  status: string;
  status_class: string;
  spotify_track: string;
  spotify_artist: string;
  spotify_album: string;
  spotify_data?: unknown;
  spotify_id?: string;
  manual_match?: unknown;
  wing_it_fallback?: boolean;
  /** LB rows carry a preformatted duration (11052); absent elsewhere. */
  duration?: string;
}

/** A raw backend result, duck-typed across all nine payload dialects. */
export interface RawDiscoveryResult {
  index?: number;
  status?: string;
  status_class?: string;
  wing_it_fallback?: boolean;
  manual_match?: unknown;
  spotify_data?: {
    name?: string;
    artists?: unknown;
    album?: unknown;
  } | null;
  spotify_id?: string;
  spotify_track?: string;
  spotify_artist?: string;
  spotify_album?: string;
  // Source-track dialects:
  tidal_track?: { name?: string; artists?: string[] };
  qobuz_track?: { name?: string; artists?: string[] };
  deezer_track?: { name?: string; artists?: string[] };
  spotify_public_track?: { name?: string; artists?: string[] };
  itunes_link_track?: { name?: string; artists?: string[] };
  beatport_track?: { title?: string; artist?: string };
  lb_track?: string;
  lb_artist?: string;
  track_name?: string;
  artist_name?: string;
  yt_track?: string;
  yt_artist?: string;
  duration?: string;
}

/**
 * Which field carries the source track, per payload dialect. youtube and
 * mirrored results arrive already carrying yt_track/yt_artist (the backend
 * speaks the modal's format natively for those two).
 */
const OBJECT_TRACK_FIELDS: Partial<Record<SyncSourceId, keyof RawDiscoveryResult>> = {
  tidal: 'tidal_track',
  qobuz: 'qobuz_track',
  deezer: 'deezer_track',
  spotify_public: 'spotify_public_track',
  itunes_link: 'itunes_link_track',
};

/** Source track name/artist extraction per dialect. */
export function sourceTrackFields(
  source: SyncSourceId,
  r: RawDiscoveryResult,
): { track: string; artist: string } {
  const objectField = OBJECT_TRACK_FIELDS[source];
  if (objectField) {
    const t = r[objectField] as { name?: string; artists?: string[] } | undefined;
    return {
      track: t ? (t.name ?? 'Unknown') : 'Unknown',
      artist: t ? (t.artists ? t.artists.join(', ') : 'Unknown') : 'Unknown',
    };
  }
  if (source === 'beatport') {
    return {
      track: r.beatport_track ? (r.beatport_track.title ?? 'Unknown') : 'Unknown',
      artist: r.beatport_track ? (r.beatport_track.artist ?? 'Unknown') : 'Unknown',
    };
  }
  if (source === 'listenbrainz') {
    return {
      track: r.lb_track || r.track_name || 'Unknown',
      artist: r.lb_artist || r.artist_name || 'Unknown',
    };
  }
  // youtube + mirrored: the backend already speaks the modal format.
  return { track: r.yt_track || 'Unknown', artist: r.yt_artist || 'Unknown' };
}

/**
 * The joined-artist extraction from the tidal socket mapper (641-650):
 * spotify_data.artists as objects or strings, blanks dropped, '-' when empty;
 * a non-array artists value passes through as-is.
 */
export function spotifyArtistText(r: RawDiscoveryResult): string {
  if (r.spotify_data && r.spotify_data.artists) {
    const artists = r.spotify_data.artists;
    if (Array.isArray(artists)) {
      return (
        artists
          .map((a) =>
            typeof a === 'object' && a !== null ? ((a as { name?: string }).name ?? '') : a,
          )
          .filter(Boolean)
          .join(', ') || '-'
      );
    }
    // Non-array artists pass through as-is, exactly like the vanilla ternary.
    return artists as string;
  }
  return r.spotify_artist || '-';
}

export function spotifyAlbumText(r: RawDiscoveryResult): string {
  if (r.spotify_data) {
    const album = r.spotify_data.album;
    return typeof album === 'object' && album !== null
      ? ((album as { name?: string }).name ?? '-')
      : ((album as string | undefined) ?? '-');
  }
  return r.spotify_album || '-';
}

/**
 * Map one raw backend result to the modal row. `foundVariant` comes from the
 * source's config (Qobuz's backend also emits the bare 'Found').
 */
export function toDiscoveryRow(
  source: SyncSourceId,
  foundVariant: 'lenient' | 'qobuz',
  r: RawDiscoveryResult,
  positionIndex: number,
): DiscoveryRow {
  const isWingIt = Boolean(r.wing_it_fallback || r.status_class === 'wing-it');
  const isError = r.status === 'error' || r.status_class === 'error' || r.status === '❌ Error';
  const isFound =
    !isWingIt &&
    !isError &&
    Boolean(
      r.status === 'found' ||
      r.status === '✅ Found' ||
      (foundVariant === 'qobuz' && r.status === 'Found') ||
      r.status_class === 'found' ||
      r.spotify_data ||
      r.spotify_track,
    );

  const { track, artist } = sourceTrackFields(source, r);

  const row: DiscoveryRow = {
    // LB results carry authoritative indexes (11043); everyone else is keyed
    // by position, same as the vanilla mappers.
    index: r.index !== undefined ? r.index : positionIndex,
    yt_track: track,
    yt_artist: artist,
    status: isWingIt ? '🎯 Wing It' : isError ? '❌ Error' : isFound ? '✅ Found' : '❌ Not Found',
    status_class: isWingIt ? 'wing-it' : isError ? 'error' : isFound ? 'found' : 'not-found',
    spotify_track: r.spotify_data ? (r.spotify_data.name ?? '-') : r.spotify_track || '-',
    spotify_artist: spotifyArtistText(r),
    spotify_album: spotifyAlbumText(r),
    spotify_data: r.spotify_data ?? undefined,
    spotify_id: r.spotify_id,
    manual_match: r.manual_match,
    wing_it_fallback: isWingIt,
  };
  if (source === 'listenbrainz') row.duration = r.duration || '0:00';
  return row;
}

/** Transform a whole results array. */
export function toDiscoveryRows(
  source: SyncSourceId,
  foundVariant: 'lenient' | 'qobuz',
  results: RawDiscoveryResult[] | null | undefined,
): DiscoveryRow[] {
  return (results || []).map((r, i) => toDiscoveryRow(source, foundVariant, r, i));
}

/** Matched-row count the way the verticals compute actualMatches (491). */
export function countMatches(rows: DiscoveryRow[]): number {
  return rows.filter((row) => row.status_class === 'found').length;
}
