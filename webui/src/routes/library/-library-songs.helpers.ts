import type { UserPlaylistTrack } from '@/features/playlists/user-playlists';

import type { LibrarySong, LibrarySongsResponse, SongsPagination } from './-library-songs.types';

interface SongsPageState {
  page: number;
  totalPages: number;
  totalCount: number;
  hasPrev: boolean;
  hasNext: boolean;
}

function readPagination(p: SongsPagination | undefined): SongsPageState {
  return {
    page: p?.page ?? 1,
    totalPages: p?.total_pages ?? 0,
    totalCount: p?.total_count ?? 0,
    hasPrev: p?.has_prev ?? false,
    hasNext: p?.has_next ?? false,
  };
}

/** Unwrap /api/library/songs. Same contract as the other two views. */
export function readSongsResponse(payload: LibrarySongsResponse | undefined): {
  songs: LibrarySong[];
  pagination: SongsPageState;
  /** the count stopped at the server's cap, so it reads "N+" */
  capped: boolean;
} {
  if (payload?.success === false) throw new Error(payload.error || 'Failed to load songs');
  return {
    songs: payload?.songs ?? [],
    pagination: readPagination(payload?.pagination),
    capped: payload?.pagination?.total_capped === true,
  };
}

/** The line after the album: year, and where it sits on the album. */
export function songMeta(song: LibrarySong): string {
  const bits: string[] = [];
  if (song.year) bits.push(String(song.year));
  if (song.track_number) {
    bits.push(
      song.disc_number && song.disc_number > 1
        ? `disc ${song.disc_number}, track ${song.track_number}`
        : `track ${song.track_number}`,
    );
  }
  return bits.join(' · ');
}

/** What the add-to-playlist picker takes for a library song. */
export function songPlaylistTrack(song: LibrarySong): UserPlaylistTrack | null {
  const title = song.title.trim();
  const artist = song.artist_name.trim();
  if (!title || !artist) return null;
  return {
    track_name: title,
    artist_name: artist,
    album_name: song.album_title || '',
    duration_ms: song.duration ?? 0,
    image_url: song.album_thumb_url || null,
  };
}

/** Play one library song from its file, the way the artist page's rows do. */
export async function playLibrarySong(song: LibrarySong): Promise<void> {
  if (!song.file_path) return;
  try {
    await window.SoulSyncWebShellBridge?.playLibraryTrack(
      {
        id: song.id,
        title: song.title,
        file_path: song.file_path,
        _stats_image: song.album_thumb_url || null,
      },
      song.album_title,
      song.artist_name,
    );
  } catch {
    window.showToast?.(`Could not play ${song.title}`, 'error');
  }
}

/** A library song as the player's queue takes it, the album card's shape. */
export function songQueueTrack(song: LibrarySong) {
  const title = song.title || 'Unknown Track';
  const artist = song.artist_name || 'Unknown Artist';
  return {
    title,
    name: title,
    artist,
    artists: [{ name: artist }],
    album: song.album_title || 'Unknown Album',
    file_path: song.file_path,
    filename: song.file_path,
    is_library: true,
    playback_status: 'ready',
    image_url: song.album_thumb_url ?? null,
    id: song.id,
    artist_id: song.artist_id,
    album_id: song.album_id,
    track_number: song.track_number,
    duration: song.duration,
    bitrate: song.bitrate,
  };
}

/** A shuffled copy (Fisher-Yates); the list itself is left alone. */
export function shuffled<T>(list: readonly T[], random: () => number = Math.random): T[] {
  const out = [...list];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/** Letters folded the way the search folds them: case and accents off. */
function fold(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase();
}

/**
 * ``text`` split around where ``query`` matches it, for highlighting a
 * search's hit in a title or artist. Matching ignores case and accents, as
 * the search does, so "sand" marks "Sánd". [] marks nothing.
 */
export function matchParts(text: string, query: string): { text: string; hit: boolean }[] {
  const q = fold(query.trim());
  if (!q || !text) return [{ text, hit: false }];
  // folding can change a string's length (one char to base + mark is undone
  // by the replace), so map positions through a per-character fold
  const chars = Array.from(text);
  const folded = chars.map((c) => fold(c));
  const flat = folded.join('');
  const parts: { text: string; hit: boolean }[] = [];
  let at = 0;
  let from = 0;
  for (;;) {
    const found = flat.indexOf(q, from);
    if (found < 0) break;
    // the char span covering folded[found, found + q.length)
    let pos = 0;
    let start = -1;
    let end = -1;
    for (let i = 0; i < chars.length; i += 1) {
      const next = pos + folded[i].length;
      if (start < 0 && next > found) start = i;
      if (next >= found + q.length) {
        end = i + 1;
        break;
      }
      pos = next;
    }
    if (start < 0 || end < 0) break;
    if (start > at) parts.push({ text: chars.slice(at, start).join(''), hit: false });
    parts.push({ text: chars.slice(start, end).join(''), hit: true });
    at = end;
    from = found + q.length;
  }
  if (at < chars.length) parts.push({ text: chars.slice(at).join(''), hit: false });
  return parts.length ? parts : [{ text, hit: false }];
}
