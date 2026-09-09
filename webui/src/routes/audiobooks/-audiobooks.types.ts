/**
 * Shapes returned by /api/audiobooks/*.
 *
 * These mirror AudiobookItem.to_dict() in core/audiobook_client.py exactly. The
 * blueprint does no reshaping, so there is one definition of an audiobook
 * payload on the server and one here.
 */

/** Which level of the metadata hierarchy a result came from. */
export type AudiobookSource = 'audible' | 'apple';

export interface AudiobookPerson {
  name: string;
  /** Authors carry their own ASIN; narrators are indexed by name only. */
  asin?: string | null;
}

export interface AudiobookSeriesRef {
  asin?: string | null;
  title: string;
  /** As printed: "1", "2.5", "1-3". Null for companions with no position. */
  sequence?: string | null;
  /** Numeric form for sorting. Null when the printed sequence is not a number. */
  sequence_value?: number | null;
}

export interface AudiobookRating {
  average?: number | null;
  count: number;
  /** Counts per star level, keyed "5".."1" — enough to draw a real histogram. */
  distribution: Record<string, number>;
}

export interface AudiobookItem {
  asin: string;
  title: string;
  subtitle: string;
  authors: AudiobookPerson[];
  narrators: AudiobookPerson[];
  author_names: string[];
  narrator_names: string[];
  series: AudiobookSeriesRef[];
  publisher: string;
  summary: string;
  short_summary: string;
  release_date?: string | null;
  runtime_minutes?: number | null;
  runtime_formatted: string;
  cover_url?: string | null;
  cover_url_large?: string | null;
  sample_url?: string | null;
  rating?: AudiobookRating | null;
  genres: string[];
  language: string;
  format_type: string;
  is_adult: boolean;
  /** True when this book is already in the audiobook library on disk. Set by
   *  the API on every book payload, refreshed by the daily library scan. */
  owned?: boolean;
  /** "audible" carries narrators and series; "apple" is the thin fallback. */
  source: AudiobookSource;
}

export interface AudiobookCategory {
  id: string;
  name: string;
  children: { id: string; name: string }[];
}

export interface AudiobookShelf {
  key: string;
  title: string;
  /**
   * Genre NAME, not an id. Audible's category ids are per-storefront — of the
   * genres the US and UK stores share, none use the same id — so a name is the
   * only key that survives the server falling back to another store.
   */
  category: string;
  sort: string;
  results: AudiobookItem[];
}

export interface AudiobookHome {
  hero: AudiobookItem | null;
  shelves: AudiobookShelf[];
}

/** Search modes. Narrator is the one nothing else can do. */
export type AudiobookSearchType = 'keywords' | 'title' | 'author' | 'narrator';

export interface AudiobookSearchResult {
  results: AudiobookItem[];
  /** Which level of the hierarchy answered, so the UI can stop promising narrators. */
  source: AudiobookSource;
}

/** What the sample player bar is currently holding. */
export interface AudiobookPlayback {
  asin: string;
  title: string;
  author: string;
  narrator: string;
  coverUrl?: string | null;
  sampleUrl: string;
  isPlaying: boolean;
}

/** Which credit a person page is showing. */
export type AudiobookRole = 'author' | 'narrator';

export interface AudiobookSeriesGroup {
  title: string;
  asin?: string | null;
  books: AudiobookItem[];
}

export interface AudiobookCollaborator {
  name: string;
  count: number;
}

/**
 * A person page: everything one author wrote or one narrator performed,
 * grouped rather than listed.
 *
 * Built server-side from several pages of the catalogue, because a prolific
 * author runs well past the 50-result page cap and a bibliography that stops at
 * 50 silently hides half a career.
 */
export interface AudiobookPersonProfile {
  name: string;
  role: AudiobookRole;
  total_books: number;
  total_runtime_minutes: number;
  runtime_formatted: string;
  genres: string[];
  /** Narrators for an author page, authors for a narrator page. */
  collaborators: AudiobookCollaborator[];
  series: AudiobookSeriesGroup[];
  standalone: AudiobookItem[];
  /** Best-rated titles, for the header. */
  highlights: AudiobookItem[];
  /** Only ever true for authors — a narrator has no release of their own. */
  watching?: boolean;
}

/**
 * How strictly a download must match the narrator that was wished for.
 * Never a combination — a book is always exactly one narrator.
 */
export type AudiobookNarratorMode = 'exact' | 'any';

/** Where a wishlisted book has got to. */
export type AudiobookWishlistStatus = 'wanted' | 'searching' | 'grabbed' | 'done' | 'failed';

export interface AudiobookWishlistEntry {
  id: number;
  asin: string;
  title: string;
  subtitle: string;
  authors: string[];
  narrators: string[];
  series_title: string;
  series_sequence: string;
  cover_url: string;
  runtime_minutes: number;
  release_date: string;
  language: string;
  status: AudiobookWishlistStatus;
  narrator_mode: AudiobookNarratorMode;
  /** Drives the retry backoff, so it doubles as "how hard have we looked". */
  attempt_count: number;
  last_attempt_at: number;
  last_error: string;
  added_at: number;
}

export interface AudiobookWishlistCounts {
  wanted: number;
  searching: number;
  grabbed: number;
  done: number;
  failed: number;
  total: number;
}

/** Which download client a release needs. */
export type AudiobookProtocol = 'torrent' | 'usenet' | 'soulseek';

/** What it takes to fetch a folder back off a peer. Only soulseek carries it. */
export interface AudiobookSoulseekFolder {
  username: string;
  album_path: string;
  file_count: number;
  queue_length: number;
}

/** One downloadable candidate found on an indexer. */
export interface AudiobookReleaseCandidate {
  source: string;
  protocol: AudiobookProtocol;
  title: string;
  indexer: string;
  size_bytes: number;
  guid: string;
  download_url?: string | null;
  magnet_uri?: string | null;
  seeders?: number | null;
  publish_date?: string | null;
  audio_format: string;
  bitrate_kbps?: number | null;
  abridged: boolean;
  relevance: number;
  score: number;
  /** Why it scored what it did, so an odd ordering can be shown rather than trusted. */
  reasons: string[];
  /** Present only on a Soulseek release: which peer, which folder, how many files. */
  soulseek?: AudiobookSoulseekFolder | null;
  /** Average kbps this size implies over the book's runtime — the one number
   *  that explains why the same book turns up at 100MB and at 2GB. Null when
   *  the runtime is unknown. */
  implied_kbps?: number | null;
  /** "thin" | "standard" | "good" | "generous" | "oversized". */
  quality_band?: string;
  quality_note?: string;
  /** Set only when the release NAMES a bitrate and the arithmetic says it
   *  cannot hold the whole book. The one partial-release check that works
   *  before spending a download. */
  short_warning?: string;
}

/**
 * What the wishlist pass can honestly report about itself.
 *
 * No interval and no "running" flag: the schedule belongs to the
 * `audiobook_process_wishlist` system automation, and duplicating it here would
 * let this page show a cadence that had drifted from the one actually running.
 * The page names the automation and sends you to the Automations page instead.
 */
export interface AudiobookWishlistSummary {
  retry_after_seconds: number;
  batch_size: number;
  action_type: string;
  automation_name: string;
}

export interface AudiobookDownload {
  download_id: string;
  asin: string;
  title: string;
  author: string;
  source: string;
  release_title: string;
  indexer: string;
  status: string;
  progress: number;
  bytes_done: number;
  bytes_total: number;
  save_path: string;
  error: string;
  created_at: number;
  completed_at: number;
  /** Why a book is staged rather than imported, in words. Without this a held
   *  book is indistinguishable from a hung one. */
  completeness?: string;
}

/** An author being followed for new releases. */
export interface AudiobookFollowedAuthor {
  name: string;
  role: string;
  cover_url: string;
  /** Only books published after this get wishlisted. */
  since_date: string;
  last_scanned_at: number;
  found_total: number;
  last_error: string;
  added_at: number;
}
