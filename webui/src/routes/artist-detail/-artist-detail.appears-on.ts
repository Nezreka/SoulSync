/**
 * "appears on": tracks this artist is credited on that are filed under
 * someone else. features and collabs, from the credits the spotify and deezer
 * workers keep (/api/artist/<id>/appears-on).
 */

export interface AppearsOnTrack {
  id: string | number;
  title?: string;
  track_number?: number | null;
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

export async function loadAppearsOn(artistId: string | number): Promise<AppearsOnTrack[]> {
  try {
    const resp = await fetch(`/api/artist/${encodeURIComponent(String(artistId))}/appears-on`);
    if (!resp.ok) return [];
    const data = await resp.json();
    return data?.success && Array.isArray(data.tracks) ? data.tracks : [];
  } catch {
    return [];
  }
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
