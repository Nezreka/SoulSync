/**
 * a hand-off from another page (the issues fix button) to the enhanced view:
 * open this artist in enhanced mode with the album expanded, its details form
 * open (album) or the track row in view (track). one pending ask at a time,
 * and it goes stale after a minute so a later visit isn't surprised by it.
 */

export interface ArtistEditFocus {
  artistId: string;
  albumId?: string;
  trackId?: string;
}

interface Pending extends ArtistEditFocus {
  at: number;
}

const STALE_MS = 60_000;
let pending: Pending | null = null;

export function requestArtistEdit(focus: ArtistEditFocus): void {
  pending = {
    artistId: String(focus.artistId),
    albumId: focus.albumId != null && focus.albumId !== '' ? String(focus.albumId) : undefined,
    trackId: focus.trackId != null && focus.trackId !== '' ? String(focus.trackId) : undefined,
    at: Date.now(),
  };
}

/** the ask for this artist, if there is a fresh one. doesn't use it up. */
export function peekArtistEdit(
  artistId: string | number | null | undefined,
): ArtistEditFocus | null {
  if (!pending || artistId == null) return null;
  if (Date.now() - pending.at > STALE_MS) {
    pending = null;
    return null;
  }
  return pending.artistId === String(artistId) ? pending : null;
}

/** the album row that answers the ask: its own id, or it holds the track */
export function albumMatchesEdit(
  focus: ArtistEditFocus | null,
  album: { id?: unknown; tracks?: readonly object[] },
): boolean {
  if (!focus) return false;
  if (focus.albumId && String(album.id) === focus.albumId) return true;
  if (focus.trackId)
    return (album.tracks ?? []).some((t) => String((t as { id?: unknown }).id) === focus.trackId);
  return false;
}

export function clearArtistEdit(): void {
  pending = null;
}
