/**
 * Basic (download-source file) search shapes.
 *
 * Every field here is traced to the server dataclasses in
 * `core/download_plugins/types.py`, serialised by `core/search/basic.py`
 * through `__dict__.copy()`. That serialisation is the reason the two shapes
 * disagree so much: an album is NOT a track with extra fields. It carries
 * `album_title`/`total_size`/`dominant_quality` where a track carries
 * `title`/`size`/`quality`, and it has no `filename`, `bitrate` or `duration`
 * at all. Sorting code that reads only the track names silently ranks every
 * album at zero — which is exactly what the vanilla did.
 */

/** A single file result. Mirrors `TrackResult`. */
export interface BasicTrack {
  result_type: 'track';
  username: string;
  filename: string;
  size: number;
  /** kbps, or null when the source did not report one. */
  bitrate: number | null;
  /** MILLIseconds — slskd reports seconds and the client converts. */
  duration: number | null;
  /** Container/codec, lowercase-ish ('flac', 'mp3', …). */
  quality: string;
  free_upload_slots: number;
  upload_speed: number;
  queue_length: number;
  sample_rate: number | null;
  bit_depth: number | null;
  artist: string | null;
  title: string | null;
  /** The album NAME, not an object — `TrackResult.album` is a str. */
  album: string | null;
  track_number: number | null;
  /**
   * 0..1, computed from format/bitrate/slots/speed/queue.
   *
   * It is a `@property` on the server, so it only reaches us because
   * `basic.py` copies it onto the dict explicitly. Before that fix the key
   * was absent from every payload and the Quality sort ranked everything 0.
   */
  quality_score: number;
}

/** A folder result carrying its own tracks. Mirrors `AlbumResult`. */
export interface BasicAlbum {
  result_type: 'album';
  username: string;
  album_path: string;
  album_title: string;
  artist: string | null;
  track_count: number;
  total_size: number;
  tracks: BasicTrack[];
  /** The most common format across the album's tracks. */
  dominant_quality: string;
  year: string | null;
  free_upload_slots: number;
  upload_speed: number;
  queue_length: number;
  quality_score: number;
}

export type BasicResult = BasicAlbum | BasicTrack;

export function isAlbum(result: BasicResult): result is BasicAlbum {
  return result.result_type === 'album';
}

/** `{mode, sources}` from GET /api/search/sources. */
export interface BasicSource {
  name: string;
  display_name: string;
}

export interface BasicSourcesResponse {
  /** 'hybrid', or the single source's name. */
  mode: string;
  sources: BasicSource[];
}

/** POST /api/search. `error` is set instead of `results` on a 400. */
export interface BasicSearchResponse {
  results?: BasicResult[];
  error?: string;
}

// ── Filters ───────────────────────────────────────────────────────────────

export const TYPE_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'album', label: 'Albums' },
  { value: 'track', label: 'Tracks' },
] as const;

export type TypeFilter = (typeof TYPE_FILTERS)[number]['value'];

export const FORMAT_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'flac', label: 'FLAC' },
  { value: 'mp3', label: 'MP3' },
  { value: 'ogg', label: 'OGG' },
  { value: 'aac', label: 'AAC' },
  { value: 'wma', label: 'WMA' },
] as const;

export type FormatFilter = (typeof FORMAT_FILTERS)[number]['value'];

/**
 * The sort pills, in the order the vanilla rendered them.
 *
 * `availability` is deliberately absent. The vanilla comparator had a branch
 * for it and it appeared in the sort-direction table, but no pill ever emitted
 * that value — it was unreachable from the UI. Rather than port a dead branch,
 * it is dropped; a pill can be added back with the key if it is ever wanted.
 */
export const SORT_OPTIONS = [
  { value: 'relevance', label: 'Relevance' },
  { value: 'quality_score', label: 'Quality' },
  { value: 'size', label: 'Size' },
  { value: 'title', label: 'Name' },
  { value: 'username', label: 'Uploader' },
  { value: 'bitrate', label: 'Bitrate' },
  { value: 'duration', label: 'Duration' },
] as const;

export type SortKey = (typeof SORT_OPTIONS)[number]['value'];

export interface FilterState {
  type: TypeFilter;
  format: FormatFilter;
  sort: SortKey;
  /** True once the user has flipped the arrow away from the sort's natural order. */
  reversed: boolean;
}

/**
 * What a fresh search resets to.
 *
 * Quality, not relevance: `resetFilters()` set `currentSortBy = 'quality_score'`
 * and lit the Quality pill on every search, even though the static markup
 * shipped with Relevance marked active. The reset is what the user actually
 * saw, so it is what is preserved here.
 */
export const DEFAULT_FILTERS: FilterState = {
  type: 'all',
  format: 'all',
  sort: 'quality_score',
  reversed: false,
};
