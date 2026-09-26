/**
 * Removing an artist from the library.
 *
 * Database only — no file on disk is touched. The confirm copy says that out
 * loud, because "delete" beside a music library reads as "erase my files" and
 * a yes/no box is the only place to correct that before it happens.
 */

export interface DeleteArtistResult {
  artist_name?: string;
  albums_deleted?: number;
  tracks_deleted?: number;
  /** A media-server artist comes back on the next scan; the toast says so. */
  returns_on_rescan?: boolean;
}

/** "45 albums and 234 tracks", with the singulars right. */
export function deleteArtistScope(albums: number, tracks: number): string {
  const album = `${albums} album${albums === 1 ? '' : 's'}`;
  const track = `${tracks} track${tracks === 1 ? '' : 's'}`;
  return `${album} and ${track}`;
}

/**
 * What the confirm box says.
 *
 * No counts: the page payload carries a track count but no album count, and a
 * confirm that says "and 0 albums" is worse than one that does not count at
 * all. The toast reports the real numbers once the server has them.
 *
 * The reassurance is its own paragraph rather than a trailing clause, because
 * "delete" next to a music library reads as "erase my files" and this box is
 * the only place to correct that before it happens.
 */
export function deleteArtistMessage(name: string): string {
  return (
    `Remove ${name} from your library, along with all of their albums and tracks?\n\n` +
    `This clears the database entries only. Not one file on disk is deleted — ` +
    `your music stays exactly where it is, and a rescan will find it again.`
  );
}

/** The toast after it lands. */
export function deleteArtistToast(name: string, result: DeleteArtistResult): string {
  const scope = deleteArtistScope(result.albums_deleted ?? 0, result.tracks_deleted ?? 0);
  const base = `Removed ${name} from the library (${scope}). No files were deleted.`;
  return result.returns_on_rescan
    ? `${base} It came from your media server, so a scan will add it back.`
    : base;
}

export async function deleteArtistRequest(artistId: unknown): Promise<DeleteArtistResult> {
  const response = await fetch(`/api/library/artist/${encodeURIComponent(String(artistId))}`, {
    method: 'DELETE',
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.success) {
    throw new Error(data.error || `Delete failed (${response.status})`);
  }
  return data as DeleteArtistResult;
}
