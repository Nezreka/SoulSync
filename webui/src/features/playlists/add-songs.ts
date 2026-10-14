/**
 * the pieces behind "add songs" inside one of your playlists: what a search
 * hit or a recent play looks like as a row, and whether the playlist already
 * has it.
 */

import type { UserPlaylistTrack } from './user-playlists';

export interface SongCandidate extends UserPlaylistTrack {
  key: string;
}

/** a cheap same-song key for the rows' "in playlist" state. the server's
 *  track_key is looser and is the one that decides; this only paints. */
export function songKey(title: string, artist: string): string {
  const plain = (text: string) =>
    text
      .toLowerCase()
      .replace(/\s*[([][^)\]]*[)\]]/g, '')
      .replace(/[^\p{L}\p{N}\s]/gu, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  const main = artist.split(/\s*(?:,|&|;|\/|\bfeat\.?|\bft\.?)\s*/i)[0] ?? artist;
  return `${plain(main)}\u0000${plain(title)}`;
}

interface SearchTrackLike {
  id?: string | number;
  source?: string;
  name?: string;
  artist?: string;
  artists?: (string | { name?: string })[];
  album?: string | { name?: string };
  duration_ms?: number;
  image_url?: string;
}

function artistOf(track: SearchTrackLike): string {
  const first = track.artists?.[0];
  const listed = typeof first === 'string' ? first : first?.name;
  return String(track.artist || listed || '').trim();
}

/** search hits as rows, the duplicates the sources hand back folded away. */
export function candidatesFromSearch(
  tracks: readonly SearchTrackLike[] | undefined,
): SongCandidate[] {
  const seen = new Set<string>();
  const out: SongCandidate[] = [];
  for (const track of tracks ?? []) {
    const title = String(track.name ?? '').trim();
    const artist = artistOf(track);
    if (!title || !artist) continue;
    const key = songKey(title, artist);
    if (seen.has(key)) continue;
    seen.add(key);
    const album = typeof track.album === 'string' ? track.album : (track.album?.name ?? '');
    out.push({
      key,
      track_name: title,
      artist_name: artist,
      album_name: album,
      duration_ms: track.duration_ms ?? 0,
      image_url: track.image_url || null,
      // so identify fetches this exact track rather than searching for it
      ...(track.id && track.source ? { source: track.source, source_id: String(track.id) } : {}),
    });
  }
  return out;
}

interface RecentRowLike {
  title?: string | null;
  artist?: string | null;
  album?: string | null;
  image_url?: string | null;
}

/** what you've been listening to, one row per song, newest first. */
export function candidatesFromRecent(
  rows: readonly RecentRowLike[] | undefined,
  limit = 12,
): SongCandidate[] {
  const seen = new Set<string>();
  const out: SongCandidate[] = [];
  for (const row of rows ?? []) {
    const title = String(row.title ?? '').trim();
    const artist = String(row.artist ?? '').trim();
    if (!title || !artist) continue;
    const key = songKey(title, artist);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      key,
      track_name: title,
      artist_name: artist,
      album_name: String(row.album ?? '').trim(),
      image_url: row.image_url || null,
    });
    if (out.length >= limit) break;
  }
  return out;
}

/** "3:49", or '' when the length is unknown. */
export function songDuration(ms: number | undefined): string {
  if (!ms || ms <= 0) return '';
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}
