/** The Songs tab (#1906): what it reads from the URL and from /api/library/songs. */

/** One page of songs. */
export const SONGS_PAGE_SIZE = 75;

/** The slice of the library search the Songs tab reads. */
export interface SongsSearch {
  q: string;
  /** alphabet filter, lowercase; 'all' means none */
  letter: string;
  page: number;
  /** '' (title), 'recent', 'artist', 'album' or 'year_desc' */
  sort: string;
  /** only songs the library holds more than one copy of */
  dupes: boolean;
  /** '' | 'lossless' | 'upgradable' */
  quality: string;
}

/** One row from /api/library/songs. */
export interface LibrarySong {
  id: string | number;
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

export interface SongsPagination {
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
