/**
 * The Songs view (upstream #1906) on Library v2.
 *
 * Upstream's songs components (`-ui/library-songs.tsx`,
 * `-ui/library-song-copies.tsx`, `-ui/song-bits.tsx`) are kept as they are;
 * this module is what their deleted `-library.api` / `-library.helpers` /
 * `-library.types` imports provided, on the catalogue: `/api/library/v2/songs`
 * rows carry catalogue ids, the track's live `file_ids`, and a song is
 * removed through the existing journaled file delete (album scope + file ids),
 * never a second delete path.
 */

import { keepPreviousData, queryOptions } from '@tanstack/react-query';

import type { UserPlaylistTrack } from '@/features/playlists/user-playlists';

import { apiClient, readJson } from '@/app/api-client';

import type {
  ActionToast,
  DeleteTrackChoice,
} from '../artist-detail/-artist-detail.manage-actions';

import {
  deleteLibraryV2Files,
  fetchLibraryV2FileDeletePreview,
  LIBRARY_V2_QUERY_KEY,
  removeLibraryV2FileRecords,
} from './-library-v2.api';

/** One row from /api/library/v2/songs. */
export interface LibrarySong {
  /** the catalogue track id; `<track>:<file>` for a row of the copies list */
  id: string | number;
  track_id: number;
  /** the live files of this row in the caller's library */
  file_ids: number[];
  title: string;
  artist_name: string;
  artist_id: string | number;
  album_id: string | number;
  album_title: string;
  album_thumb_url?: string | null;
  year?: number | null;
  track_number?: number | null;
  disc_number?: number | null;
  /** milliseconds */
  duration?: number | null;
  bitrate?: number | null;
  /** the file's extension, upper-cased ('FLAC'); '' when unknown */
  format: string;
  file_path: string;
  play_count: number;
  file_size?: number | null;
  /** how many copies of this song (title + artist) the library holds */
  copies: number;
  /** in this profile's Liked Songs */
  liked?: boolean;
}

interface SongsPagination {
  page: number;
  limit: number;
  total_count: number;
  total_pages: number;
  has_prev: boolean;
  has_next: boolean;
  /** the count stopped at the server's cap: "1,000+" */
  total_capped?: boolean;
}

export interface LibrarySongsResponse {
  success?: boolean;
  error?: string;
  songs?: LibrarySong[];
  pagination?: SongsPagination;
}

/** What a songs list is asked for: the page's filter box, sort and toggles. */
export interface LibrarySearch {
  q: string;
  letter: string;
  page: number;
  sort: string;
  dupes: boolean;
  quality: string;
}

export const LIBRARY_PAGE_SIZE = 75;
/** Most songs Play all / Shuffle queue at once. */
export const PLAY_ALL_LIMIT = 500;

function songsParams(search: LibrarySearch, limit: number, page = search.page) {
  const params: Record<string, string | number> = { search: search.q, page, limit };
  if (search.letter && search.letter !== 'all') params.letter = search.letter;
  if (search.sort) params.sort = search.sort;
  if (search.dupes) params.dupes = '1';
  if (search.quality) params.quality = search.quality;
  return params;
}

/** The Songs view, and the strip of song matches above an artist or album
 *  search. The previous page stays on screen while the next one loads. */
export function librarySongsQueryOptions(
  profileId: number,
  search: LibrarySearch,
  limit: number = LIBRARY_PAGE_SIZE,
) {
  const params = songsParams(search, limit);
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'songs', profileId, params] as const,
    queryFn: () =>
      readJson<LibrarySongsResponse>(apiClient.get('library/v2/songs', { searchParams: params })),
    placeholderData: keepPreviousData,
  });
}

/** The first PLAY_ALL_LIMIT songs of what the view shows, in its order. */
export async function fetchSongsToPlay(search: LibrarySearch): Promise<LibrarySong[]> {
  const payload = await readJson<LibrarySongsResponse>(
    apiClient.get('library/v2/songs', { searchParams: songsParams(search, PLAY_ALL_LIMIT, 1) }),
  );
  if (payload.success === false) throw new Error(payload.error || 'Failed to load songs');
  return payload.songs ?? [];
}

/** Every copy of one song, best file first (the "2 copies" comparison). */
export async function fetchSongCopies(trackId: string | number): Promise<LibrarySong[]> {
  const payload = await readJson<{ success?: boolean; error?: string; copies?: LibrarySong[] }>(
    apiClient.get(`library/v2/songs/${Number.parseInt(String(trackId), 10)}/copies`),
  );
  if (payload.success === false) throw new Error(payload.error || "Couldn't load the copies");
  return payload.copies ?? [];
}

/** Unwrap /api/library/v2/songs. */
export function readSongsResponse(payload: LibrarySongsResponse | undefined): {
  songs: LibrarySong[];
  pagination: {
    page: number;
    totalPages: number;
    totalCount: number;
    hasPrev: boolean;
    hasNext: boolean;
  };
  /** the count stopped at the server's cap, so it reads "N+" */
  capped: boolean;
} {
  if (payload?.success === false) throw new Error(payload.error || 'Failed to load songs');
  const p = payload?.pagination;
  return {
    songs: payload?.songs ?? [],
    pagination: {
      page: p?.page ?? 1,
      totalPages: p?.total_pages ?? 0,
      totalCount: p?.total_count ?? 0,
      hasPrev: p?.has_prev ?? false,
      hasNext: p?.has_next ?? false,
    },
    capped: p?.total_capped === true,
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

/** A library song as the player's queue takes it. Library v2 rows address the
 *  catalogue by typed id; `id` stays null because the player sends it to the
 *  media server (see -library-v2.play.ts). The file path plays this copy. */
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
    id: null,
    lib2_track_id: song.track_id,
    lib2_album_id: Number(song.album_id),
    lib2_artist_id: Number(song.artist_id) || null,
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

/** Letters folded the way a search reads them: case and accents off. */
function fold(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

/**
 * ``text`` split around where ``query`` matches it, for highlighting a
 * search's hit in a title or artist. Matching ignores case and accents, so
 * "sand" marks "Sánd". [] marks nothing.
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

/**
 * Remove a song's files from the library (``db_only``: the records, files
 * stay) or from disk too, through the journaled album-scoped file delete the
 * Library page's own dialogs use. A copies row names one file, a list row
 * every live file of its track.
 */
export async function deleteLibraryTrackRequest(
  song: LibrarySong,
  choice: DeleteTrackChoice,
): Promise<ActionToast> {
  const albumId = Number(song.album_id);
  if (!song.file_ids.length) throw new Error('No file to remove');
  const operation =
    choice === 'delete_file'
      ? await fetchLibraryV2FileDeletePreview('albums', albumId, song.file_ids).then((preview) => {
          if (preview.unsafe_count > 0)
            throw new Error('The file is outside your library folders or not reachable');
          return deleteLibraryV2Files('albums', albumId, preview.preview_token, song.file_ids);
        })
      : await removeLibraryV2FileRecords('albums', albumId, song.file_ids);
  const failed = operation.items.find((item) => item.status === 'failed');
  if (failed) {
    return {
      message: 'Track removed from library but file could not be deleted',
      tone: 'warning',
      extra: failed.error || undefined,
    };
  }
  return {
    message:
      choice === 'delete_file'
        ? 'Track deleted from library and disk'
        : 'Track removed from library',
    tone: 'success',
  };
}
