/**
 * Merge library ownership onto a release's metadata tracks, so the player can
 * play what you already have.
 *
 * `/api/album/<id>/tracks` is documented as "formatted for download missing
 * tracks modal": it describes what a release SHOULD contain, from Spotify or
 * iTunes, and never carries a `file_path`. That is fine for the wishlist modal,
 * which backfills ownership separately via lazyLoadTrackOwnership.
 *
 * The play button handed those same rows straight to the player, and the player
 * reads a missing `file_path` as "this track has to be downloaded first".
 * Auto-download for queue tracks defaults to OFF, so npEnsureQueueTrackReady
 * threw "Auto-download is disabled for missing queue tracks" on every row -
 * including a release sitting complete on disk. Boulder: "every track fails to
 * play even if i have the whole album".
 *
 * `/api/library/check-tracks` is the same endpoint the modal already uses. It
 * matches on artist + album + track name and hands back the real file path, so
 * owned rows play straight from the library and only genuine misses fall
 * through to the download path.
 */

export interface OwnedTrackInfo {
  owned?: boolean;
  file_path?: string | null;
  format?: string | null;
  bitrate?: number | null;
}

export type OwnershipMap = Record<string, OwnedTrackInfo | undefined>;

/** The request body /api/library/check-tracks expects. */
export function checkTracksBody(
  artistName: string,
  albumName: string,
  tracks: Array<Record<string, unknown>>,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    artist_name: artistName,
    // the endpoint keys its reply on `name`, so send the same field the reply
    // will be looked up by rather than whichever of name/title happens to exist
    tracks: tracks.map((t) => ({
      name: t.name ?? t.title ?? '',
      track_number: t.track_number ?? null,
    })),
  };
  if (albumName) body.album_name = albumName;
  return body;
}

/**
 * Copy `file_path` (and the format/bitrate the player shows) onto every track
 * the library reports as owned. Tracks that are not owned come back untouched,
 * so they keep whatever the download path needs.
 */
export function mergeOwnership<T extends Record<string, unknown>>(
  tracks: T[],
  ownership: OwnershipMap | null | undefined,
): T[] {
  if (!ownership) return tracks;
  return tracks.map((track) => {
    const key = String(track.name ?? track.title ?? '');
    const owned = ownership[key];
    if (!owned?.owned || !owned.file_path) return track;
    return {
      ...track,
      file_path: owned.file_path,
      // the player branches on is_library to skip the download flow entirely
      is_library: true,
      format: track.format ?? owned.format ?? null,
      bitrate: track.bitrate ?? owned.bitrate ?? null,
    };
  });
}

/** How many of these tracks the library can actually play right now. */
export function ownedCount(tracks: Array<Record<string, unknown>>): number {
  return tracks.filter((t) => Boolean(t.file_path)).length;
}
