import type { ArtistDetailResponse, DiscographyRelease } from './-artist-detail.types';

/**
 * Opening a release card, ported from the click handler inside
 * createReleaseCard (library.js:1803-1878).
 *
 * The download modal itself (openDownloadMissingModalForArtistAlbum) stays
 * vanilla. it is invoked, not reimplemented.
 *
 * #1297 a card used to open the add-to-wishlist modal: every track, no picking.
 * it opens the download modal now, same as an album in search, with track
 * checkboxes and its own add-to-wishlist button.
 */

export interface OpenReleaseArtist {
  id: string | number | undefined;
  name: string;
  image_url: string;
  source: string | null;
}

/**
 * The artist payload the download modal expects.
 *
 * Built from the CURRENT page state rather than the response, because the
 * library-upgrade branch in loadArtistDetailData can rewrite the id after the
 * fetch. Returns null when there is no artist name — the vanilla treated that
 * as a hard error rather than opening a modal with no owner.
 */
export function openReleaseArtist(
  payload: ArtistDetailResponse,
  currentArtistId: string | number | undefined,
  artistImageUrl: string,
): OpenReleaseArtist | null {
  const name = payload.artist?.name;
  if (!name) return null;
  return {
    id: currentArtistId,
    name: String(name),
    image_url: artistImageUrl || '',
    source: payload.discography?.source || payload.artist?.source || null,
  };
}

/**
 * The album shape the download modal expects.
 *
 * `total_tracks` prefers the object form of track_completion, falls back to
 * track_count, and finally to 1 — never 0, because the modal treats a
 * zero-track album as empty and refuses to open.
 */
export function releaseToAlbumData(release: DiscographyRelease) {
  const completion = release.track_completion;
  const totalTracks =
    completion && typeof completion === 'object'
      ? (completion as { total_tracks?: number }).total_tracks
      : (release.track_count as number | undefined) || 1;

  return {
    id: release.id,
    name: release.title,
    image_url: release.image_url,
    // The modal wants a full date; the year is all we have on a release card.
    release_date: release.year ? `${release.year}-01-01` : '',
    album_type:
      release.album_type || (typeof release.type === 'string' ? release.type : '') || 'album',
    total_tracks: totalTracks,
  };
}

/**
 * Query string for the album-tracks lookup.
 *
 * `source` comes from the ARTIST, except for a gap-fill card (#1067) which
 * belongs to a different source entirely and must be fetched from that one —
 * otherwise its tracks come back empty.
 */
export function albumTracksParams(
  release: DiscographyRelease,
  artist: OpenReleaseArtist,
): Record<string, string> {
  const params: Record<string, string> = {
    name: String(release.title ?? ''),
    artist: artist.name || '',
  };
  if (artist.source) params.source = artist.source;
  if (release._gap_source) params.source = String(release._gap_source);
  return params;
}

/**
 * Releases can be opened immediately even while background ownership checking
 * is still in progress. The modal and playback pathways backfill track ownership
 * dynamically on demand without locking the user out.
 */
export function isReleaseClickable(_release: DiscographyRelease): boolean {
  return true;
}

export function stillCheckingMessage(release: DiscographyRelease): string {
  return `Still checking ownership for ${release.title ?? ''}...`;
}

/**
 * the download modal's key for a release. the same key the wishlist modal's
 * Download Now built, so an album already downloading reopens its modal
 * instead of starting a second one.
 */
export function releaseVirtualPlaylistId(
  artist: OpenReleaseArtist,
  album: { id: DiscographyRelease['id'] },
): string {
  return `artist_album_${artist.id}_${album.id}`;
}

export function releasePlaylistName(
  artist: OpenReleaseArtist,
  album: { name: DiscographyRelease['title'] },
): string {
  return `[${artist.name}] ${album.name}`;
}
