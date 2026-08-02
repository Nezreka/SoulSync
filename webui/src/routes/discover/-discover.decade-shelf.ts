/**
 * Time Machine — the decade shelf and its three live actions.
 *
 * Transcribed from `loadDecadeBrowserTabs` (2646), `startDecadeSync` (2718),
 * `startDecadeSyncPolling` (2785) and `openDownloadModalForDecade` (2870), read
 * end to end.
 *
 * ── These are LIVE inside a mostly-dead region ──────────────────────────────
 *
 * The tabbed decade browser is unreachable (see discover_dead_code_audit.md),
 * but these four are NOT part of it. `loadDecadeBrowserTabs` is in the loader
 * list (243) and renders a mix-card shelf; the mix card's own actions call
 * `openDownloadModalForDecade` and `startDecadeSync` (2672). They must survive
 * the deletion of the tab path around them.
 */

import { MIX_ACTION_DOWNLOAD, MIX_ACTION_SYNC, type DiscoverMix } from './-discover.mixes';

export const DECADES_AVAILABLE_URL = '/api/discover/decades/available';

export function decadeTracksUrl(year: number | string): string {
  return `/api/discover/decade/${year}`;
}

export const DECADE_NO_TRACKS = 'No tracks available for this decade';

/** `${year}s Classics` — the playlist name AND the mix subtitle (2667, 2758). */
export function decadeClassicsName(year: number | string): string {
  return `${year}s Classics`;
}

export function decadeSyncCompleteToast(year: number | string): string {
  return `${decadeClassicsName(year)} sync complete!`;
}

/**
 * THREE different ids exist for one decade, and they are not interchangeable.
 *
 *   mix key         `decade_${year}`            (2665) — the shelf registry
 *   sync playlist   `discover_decade_${year}`   (2753) — what startPlaylistSync gets
 *   download        `decade_${year}`            (2901) — what the modal gets
 *   poller          `decade_${year}`            (2786) — key in discoverSyncPollers
 *   status base     `decade-${year}`            (2669) — HYPHEN, for DOM ids
 *
 * So the sync path and the download path address the SAME decade under
 * different ids, and the download id collides by value with the mix key. Any
 * "tidy up" that unifies them changes which cache the sync writes into.
 */
export function decadeMixKey(year: number | string): string {
  return `decade_${year}`;
}

export function decadeSyncPlaylistId(year: number | string): string {
  return `discover_decade_${year}`;
}

export function decadeDownloadPlaylistId(year: number | string): string {
  return `decade_${year}`;
}

export function decadePollerId(year: number | string): string {
  return `decade_${year}`;
}

/** DOM ids hyphenate; the playlist ids do not (2669, 2767). */
export function decadeStatusBase(year: number | string): string {
  return `decade-${year}`;
}

export const decadeStatusId = (y: number | string) => `${decadeStatusBase(y)}-sync-status`;
export const decadeButtonId = (y: number | string) => `${decadeStatusBase(y)}-sync-btn`;
export const decadeCompletedId = (y: number | string) => `${decadeStatusBase(y)}-sync-completed`;
export const decadePendingId = (y: number | string) => `${decadeStatusBase(y)}-sync-pending`;
export const decadeFailedId = (y: number | string) => `${decadeStatusBase(y)}-sync-failed`;
export const decadePercentageId = (y: number | string) => `${decadeStatusBase(y)}-sync-percentage`;

export interface AvailableDecade {
  year: number;
  track_count?: number;
}

/**
 * The shelf hides its whole section when there are no decades (2657-2660).
 *
 * It also force-hides the legacy `#decade-tabs` strip on every load (2652),
 * which is belt-and-braces: index.html already ships it `display:none`.
 */
export function decadeShelfHasContent(
  data: { success?: boolean; decades?: AvailableDecade[] } | null | undefined,
): boolean {
  return Boolean(data?.success && Array.isArray(data.decades) && data.decades.length > 0);
}

/**
 * One decade → one mix card (2662-2681).
 *
 * `trackCount` comes from the available-decades payload so the card can show a
 * count before its tracks are lazily fetched. `fetchTracks` populates
 * `decadeTracksCache` as a SIDE EFFECT, which is what makes the Sync button
 * work — it reads that cache, not the mix.
 */
export function decadeMix(d: AvailableDecade): DiscoverMix {
  const year = d.year;
  return {
    key: decadeMixKey(year),
    title: `${year}s`,
    subtitle: decadeClassicsName(year),
    trackCount: d.track_count,
    statusBase: decadeStatusBase(year),
    actions: [
      {
        label: MIX_ACTION_DOWNLOAD,
        closeFirst: true,
        onclick: `openDownloadModalForDecade(${year})`,
      },
      { label: MIX_ACTION_SYNC, primary: true, isSync: true, onclick: `startDecadeSync(${year})` },
    ],
  };
}

// ── Track conversion ────────────────────────────────────────────────────────

export interface DecadeSpotifyTrack {
  id?: string;
  name?: string;
  artists?: unknown;
  album?: { name?: string; images?: { url: string }[] };
  duration_ms?: number;
}

/**
 * The decade conversion is NOT `toSyncTracks` (2726-2751).
 *
 * The playlist sync uses `track_data_json` WHOLE when present. This one merges
 * field by field, preferring the json's value and falling back to the flat
 * column for each — so a partial `track_data_json` still gets a name and an
 * album from the row. Two conversions, deliberately different, and sharing one
 * would silently change what either path sends.
 *
 * `flattenArtists` is the second difference: `startDecadeSync` flattens artists
 * to strings (2746), `openDownloadModalForDecade` does NOT (2897). Transcribed
 * rather than unified — see `DECADE_DOWNLOAD_KEEPS_ARTIST_OBJECTS`.
 */
export function decadeTrackToSpotify(
  track: Record<string, unknown>,
  flattenArtists: boolean,
): DecadeSpotifyTrack {
  const trackData = (track.track_data_json ?? track) as Record<string, unknown>;
  const album = trackData.album as { name?: string; images?: { url: string }[] } | undefined;
  const spotifyTrack: DecadeSpotifyTrack = {
    id: (trackData.id || track.spotify_track_id) as string,
    name: (trackData.name || trackData.track_name || track.track_name) as string,
    artists: trackData.artists || [{ name: trackData.artist_name || track.artist_name }],
    album: album || {
      name: (trackData.album_name || track.album_name) as string,
      // The vanilla writes `trackData.album?.images || (...)` here (2740). That
      // first operand is DEAD: this is the branch where `trackData.album` is
      // falsy, so it can only be undefined. Not a judgement call — TypeScript
      // narrows it to `never` and refuses to compile it. Dropped rather than
      // cast around, because a cast would preserve the misleading shape while
      // changing nothing.
      images: track.album_cover_url ? [{ url: track.album_cover_url as string }] : [],
    },
    duration_ms: (trackData.duration_ms || track.duration_ms || 0) as number,
  };
  if (flattenArtists && Array.isArray(spotifyTrack.artists)) {
    spotifyTrack.artists = spotifyTrack.artists.map(
      (a: unknown) => (a as { name?: string })?.name || a,
    );
  }
  return spotifyTrack;
}

/**
 * The download path leaves artists as OBJECTS (2897) while the sync path
 * flattens them (2746).
 *
 * Recorded, not corrected. The two feed different consumers and I have not
 * verified what the download modal does with an object array; changing it is a
 * behaviour change that belongs with that verification, not with a port.
 */
export const DECADE_DOWNLOAD_KEEPS_ARTIST_OBJECTS = true;

/** Both actions refuse an empty cache with the same warning (2720, 2872). */
export function decadeHasTracks(tracks: unknown[] | null | undefined): boolean {
  return Array.isArray(tracks) && tracks.length > 0;
}
