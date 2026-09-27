/**
 * "appears on": albums and tracks this artist is credited on that are filed
 * under someone else. collab albums and features, from the credits the
 * spotify and deezer workers keep (/api/artist/<id>/appears-on).
 */

export interface AppearsOnTrack {
  id: string | number;
  title?: string;
  track_number?: number | null;
  disc_number?: number | null;
  duration?: number | null;
  file_path?: string | null;
  album_id?: string | number;
  album_title?: string;
  album_thumb_url?: string | null;
  year?: number | null;
  artist_id?: string | number;
  artist_name?: string;
  credits?: string[];
}

export interface AppearsOnAlbum {
  id: string | number;
  title?: string;
  thumb_url?: string | null;
  year?: number | null;
  artist_id?: string | number;
  artist_name?: string;
  credits?: string[];
  tracks?: AppearsOnTrack[];
}

export interface AppearsOnData {
  albums: AppearsOnAlbum[];
  tracks: AppearsOnTrack[];
}

export const EMPTY_APPEARS_ON: AppearsOnData = { albums: [], tracks: [] };

export async function loadAppearsOn(artistId: string | number): Promise<AppearsOnData> {
  try {
    const resp = await fetch(`/api/artist/${encodeURIComponent(String(artistId))}/appears-on`);
    if (!resp.ok) return EMPTY_APPEARS_ON;
    const data = await resp.json();
    if (!data?.success) return EMPTY_APPEARS_ON;
    return {
      albums: Array.isArray(data.albums) ? data.albums : [],
      tracks: Array.isArray(data.tracks) ? data.tracks : [],
    };
  } catch {
    return EMPTY_APPEARS_ON;
  }
}

/** an album's tracks you can play, carrying the album's title, art and
 *  credits so they queue like any other appears-on track. */
export function albumTracks(album: AppearsOnAlbum): AppearsOnTrack[] {
  return (album.tracks ?? [])
    .filter((t) => t.file_path)
    .map((t) => ({
      ...t,
      album_id: album.id,
      album_title: album.title,
      album_thumb_url: album.thumb_url,
      year: album.year,
      artist_id: album.artist_id,
      artist_name: album.artist_name,
      credits: t.credits?.length ? t.credits : album.credits,
    }));
}

/** "with JAY-Z · 2011" for an album card. */
export function albumSubLine(album: AppearsOnAlbum, pageArtist: string): string {
  const who = withLine(
    { id: album.id, credits: album.credits, artist_name: album.artist_name },
    pageArtist,
  );
  return [who ? `with ${who}` : '', album.year ? String(album.year) : '']
    .filter(Boolean)
    .join(' · ');
}

/**
 * who else is on the track, from this artist's page. "Calvin Harris" on
 * rihanna's page, "Calvin Harris, Rihanna" on a third feature's. falls back to
 * the artist the track is filed under when there are no credits to read.
 */
export function withLine(track: AppearsOnTrack, pageArtist: string): string {
  const me = pageArtist.trim().toLowerCase();
  const others = (track.credits ?? []).filter((n) => n && n.trim().toLowerCase() !== me);
  if (others.length) return others.join(', ');
  return track.artist_name ?? '';
}

/** "with Calvin Harris · 18 Months · 2012", skipping whatever's missing. */
export function subLine(track: AppearsOnTrack, pageArtist: string): string {
  const who = withLine(track, pageArtist);
  return [who ? `with ${who}` : '', track.album_title ?? '', track.year ? String(track.year) : '']
    .filter(Boolean)
    .join(' · ');
}

/** the radio-row shape window.playTrackList queues. */
export function queueRow(track: AppearsOnTrack, pageArtist: string) {
  const credits = track.credits?.length ? track.credits.join(', ') : track.artist_name;
  return {
    id: track.id,
    title: track.title || 'Unknown Track',
    artist: credits || pageArtist,
    album: track.album_title || '',
    file_path: track.file_path,
    filename: track.file_path,
    is_library: true,
    image_url: track.album_thumb_url || null,
    artist_id: track.artist_id,
    album_id: track.album_id,
  };
}

/** m:ss from milliseconds, blank when unknown. */
export function formatDuration(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return '';
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}
