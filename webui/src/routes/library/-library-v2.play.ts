/**
 * Build a media-player queue from Library v2 rows.
 *
 * The player is the Legacy one, reached through the shell bridge — Library v2
 * does not own a second player, the same way TrackPlayButton reuses it for a
 * single row. What this module owns is the SHAPE of a queue row, which the
 * player reads strictly:
 *
 *   - a row without `file_path` is "download this first", and with
 *     auto-download off that fails outright — even for an album sitting
 *     complete on disk (upstream #1213). So a row with no live file never
 *     enters the queue.
 *   - `is_library: true` is what skips the download flow entirely.
 *   - `id` must be an id the MEDIA SERVER understands (server or legacy). A v2
 *     id means nothing to it, so the typed ids ride alongside instead — the
 *     same contract `playLibraryTrack` follows in the shell bundle.
 *
 * Upstream's equivalent (-artist-detail.owned-tracks.ts) exists because its
 * rows come from a provider tracklist with no path on them, so it has to ask
 * /api/library/check-tracks what the library owns. Here the path is already on
 * the row that was just rendered; asking again would be a second source of
 * truth that can disagree with the one on screen.
 */

import type { LibraryV2ArtistPlaybackFile } from './-library-v2.api';
import type { LibraryV2AlbumDetail, LibraryV2Track } from './-library-v2.types';

export interface PlayQueueRow {
  /** What the media server understands: a server or legacy id, else null. */
  id: string | number | null;
  lib2_track_id: number | null;
  legacy_track_id: string | number | null;
  server_track_id: string | number | null;
  lib2_artist_id: number | null;
  title: string;
  /** The player reads `name` on some paths and `title` on others. */
  name: string;
  artist: string;
  album: string;
  file_path: string;
  is_library: true;
  image_url: string | null;
  format: string | null;
  bitrate: number | null;
  duration: number | null;
  track_number: number | null;
  disc_number: number | null;
}

/** A file the catalogue still counts as present on disk. */
function isPlayable(file: { path?: string | null; file_state?: string | null } | null): boolean {
  if (!file?.path || !file.path.trim()) return false;
  return (file.file_state ?? 'active') === 'active';
}

function byPosition(
  a: { disc_number: number | null; track_number: number | null },
  b: { disc_number: number | null; track_number: number | null },
): number {
  // A payload arrives in whatever order the query produced. Unnumbered tracks
  // sort last rather than jumping to the front of the queue.
  const disc = (a.disc_number ?? Number.MAX_SAFE_INTEGER) - (b.disc_number ?? Number.MAX_SAFE_INTEGER);
  if (disc !== 0) return disc;
  return (
    (a.track_number ?? Number.MAX_SAFE_INTEGER) - (b.track_number ?? Number.MAX_SAFE_INTEGER)
  );
}

function primaryArtist(track: LibraryV2Track): { id: number; name: string } | null {
  const credits = track.artists ?? [];
  const primary = credits.find((a) => a.role === 'primary') ?? credits[0];
  return primary ? { id: primary.id, name: primary.name } : null;
}

/** Queue rows for one album, in disc/track order, owned tracks only. */
export function albumQueueRows(
  album: Pick<LibraryV2AlbumDetail, 'title' | 'image_url' | 'tracks'>,
  fallbackArtistName: string,
): PlayQueueRow[] {
  return (album.tracks ?? [])
    .filter((track) => isPlayable(track.file))
    .slice()
    .sort(byPosition)
    .map((track) => {
      const credit = primaryArtist(track);
      const title = track.title || 'Unknown Track';
      return {
        id: track.server_track_id ?? track.legacy_track_id ?? null,
        lib2_track_id: track.id,
        legacy_track_id: track.legacy_track_id ?? null,
        server_track_id: track.server_track_id ?? null,
        lib2_artist_id: credit?.id ?? null,
        title,
        name: title,
        artist: credit?.name || fallbackArtistName,
        album: album.title || 'Unknown Album',
        file_path: track.file!.path,
        is_library: true as const,
        image_url: album.image_url ?? null,
        format: track.file!.format ?? null,
        bitrate: track.file!.bitrate ?? null,
        duration: track.duration ?? null,
        track_number: track.track_number ?? null,
        disc_number: track.disc_number ?? null,
      };
    });
}

/** Queue rows for everything an artist owns, album by album.
 *
 *  Fed by the credit-scoped play-queue endpoint, so a release the artist only
 *  guests on is here too — and each row carries the artist actually credited
 *  on that track, which on a compilation is not the artist whose page this is. */
export function artistQueueRows(
  files: LibraryV2ArtistPlaybackFile[],
  artistName: string,
): PlayQueueRow[] {
  // The Files tab lists every physical copy, so a lossless master and its
  // intentional lossy companion are two rows for ONE recording — queueing both
  // plays the same song twice. Keep one file per track, preferring the primary
  // and otherwise the first that turns up, so a track whose only copy is not
  // flagged primary is still played rather than silently dropped.
  const perTrack = new Map<number, LibraryV2ArtistPlaybackFile>();
  for (const file of files) {
    if (!isPlayable(file)) continue;
    const held = perTrack.get(file.track_id);
    if (!held || (!held.is_primary && file.is_primary)) perTrack.set(file.track_id, file);
  }

  return [...perTrack.values()]
    .sort((a, b) => (a.album_id - b.album_id) || byPosition(a, b))
    .map((file) => {
      const title = file.track_title || 'Unknown Track';
      return {
        // The flat file list carries no server/legacy id — a v2 id must never
        // be sent as one, so this stays null and the typed id below addresses
        // the row. Streaming from the media server falls back to the path.
        id: null,
        lib2_track_id: file.track_id,
        legacy_track_id: null,
        server_track_id: null,
        lib2_artist_id: file.artist_id ?? null,
        title,
        name: title,
        artist: file.artist_name || artistName,
        album: file.album_title || 'Unknown Album',
        file_path: file.path,
        is_library: true as const,
        image_url: file.album_image_url ?? null,
        format: file.format ?? null,
        bitrate: file.bitrate ?? null,
        duration: file.duration ?? null,
        track_number: file.track_number ?? null,
        disc_number: file.disc_number ?? null,
      };
    });
}
