/**
 * The shapes /api/labels/:id/catalog returns, as the page actually reads them.
 *
 * Everything is optional on purpose: the catalog is assembled from MusicBrainz
 * release-groups, and the vanilla treated every field as missing-by-default —
 * `rel.year ? ' · ' + year : ''`, `rel.artist_id ? <button> : ''`. Typing them
 * as required here would only move that reality into a cast.
 */

export interface LabelRelease {
  album?: string;
  artist?: string;
  artist_id?: string;
  year?: string;
  primary_type?: string;
  /** Cover Art Archive exact lookup, when the catalog knows the release. */
  release_id?: string;
  /** What the download modal resolves against when nothing better is found. */
  release_group_id?: string;
}

export interface LabelCatalogResponse {
  label?: { id?: string; name?: string };
  is_watching?: boolean;
  backlog?: boolean;
  total?: number;
  artist_count?: number;
  page?: number;
  page_size?: number;
  has_more?: boolean;
  releases?: LabelRelease[];
}

/** All / Owned / Missing, as the toolbar's three pills. */
export type LabelFilter = 'all' | 'owned' | 'missing';

/** Newest / Oldest / By artist, as the sort select. */
export type LabelSort = 'newest' | 'oldest' | 'artist';

export interface LabelWatchState {
  watching: boolean;
  backlog: boolean;
}
