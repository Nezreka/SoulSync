import type { Automation } from '@/routes/automations/-automations.types';

import { apiClient, readJson } from '@/app/api-client';
import { getShellProfileContext } from '@/platform/shell/bridge';
import { runAutomation } from '@/routes/automations/-automations.api';

/**
 * Every audiobook request, tagged with whose it is.
 *
 * Wishlists, followed authors and the blocklist are per profile — two people
 * on one install do not share a reading list — and the server reads
 * X-Profile-Id to tell them apart. Without this header every profile wrote
 * into profile 1's lists.
 *
 * A CLIENT OF ITS OWN rather than a hook on the shared apiClient: that client
 * is used by music and video too, and this must not change a single request
 * they make.
 */
const audiobookClient = apiClient.extend({
  hooks: {
    beforeRequest: [
      ({ request }) => {
        const profileId = getShellProfileContext()?.profileId ?? 1;
        request.headers.set('X-Profile-Id', String(profileId || 1));
      },
    ],
  },
});

import type {
  AudiobookBlockedRelease,
  AudiobookCategory,
  AudiobookDownload,
  AudiobookFollowedAuthor,
  AudiobookNarratorMode,
  AudiobookHome,
  AudiobookItem,
  AudiobookLibraryEntry,
  AudiobookLibrary,
  AudiobookLibraryScan,
  AudiobookSearchResult,
  AudiobookPersonProfile,
  AudiobookRole,
  AudiobookRecycledBook,
  AudiobookReleaseCandidate,
  AudiobookSearchType,
  AudiobookSource,
  AudiobookWishlistCounts,
  AudiobookWishlistEntry,
  AudiobookWishlistSummary,
} from './-audiobooks.types';

/**
 * Every call here fails soft and returns an empty result on error.
 *
 * The browse page is built from six independent shelves plus a hero; one of
 * them failing must leave the rest of the page standing, exactly as the server
 * side does it.
 */

interface ListResponse {
  success?: boolean;
  results?: AudiobookItem[];
  source?: AudiobookSource;
  error?: string;
}

interface DetailResponse {
  success?: boolean;
  book?: AudiobookItem;
  error?: string;
}

interface HomeResponse {
  success?: boolean;
  hero?: AudiobookItem | null;
  shelves?: AudiobookHome['shelves'];
  error?: string;
}

interface CategoriesResponse {
  success?: boolean;
  categories?: AudiobookCategory[];
  error?: string;
}

function listOf(data: ListResponse | null | undefined): AudiobookItem[] {
  return data?.success && Array.isArray(data.results) ? data.results : [];
}

export async function searchAudiobooks(
  query: string,
  searchType: AudiobookSearchType = 'keywords',
  limit = 24,
): Promise<AudiobookSearchResult> {
  const trimmed = query.trim();
  if (!trimmed) return { results: [], source: 'audible' };

  try {
    const data = await readJson<ListResponse>(
      audiobookClient.get('audiobooks/search', {
        searchParams: { q: trimmed, type: searchType, limit },
      }),
    );
    return { results: listOf(data), source: data?.source || 'audible' };
  } catch (err) {
    console.error('Failed to search audiobooks:', err);
    return { results: [], source: 'audible' };
  }
}

export async function fetchAudiobookHome(limit = 20): Promise<AudiobookHome> {
  try {
    const data = await readJson<HomeResponse>(
      audiobookClient.get('audiobooks/home', { searchParams: { limit } }),
    );
    if (!data?.success) return { hero: null, shelves: [] };
    return { hero: data.hero ?? null, shelves: Array.isArray(data.shelves) ? data.shelves : [] };
  } catch (err) {
    console.error('Failed to load the audiobooks home shelves:', err);
    return { hero: null, shelves: [] };
  }
}

export async function fetchAudiobook(asin: string): Promise<AudiobookItem | null> {
  if (!asin) return null;
  try {
    const data = await readJson<DetailResponse>(
      audiobookClient.get(`audiobooks/book/${encodeURIComponent(asin)}`),
    );
    return data?.success && data.book ? data.book : null;
  } catch (err) {
    console.error(`Failed to load audiobook ${asin}:`, err);
    return null;
  }
}

export async function fetchSimilarAudiobooks(asin: string, limit = 12): Promise<AudiobookItem[]> {
  if (!asin) return [];
  try {
    return listOf(
      await readJson<ListResponse>(
        audiobookClient.get(`audiobooks/similar/${encodeURIComponent(asin)}`, {
          searchParams: { limit },
        }),
      ),
    );
  } catch (err) {
    console.error('Failed to load similar audiobooks:', err);
    return [];
  }
}

export async function fetchSeries(
  name: string,
  seriesAsin?: string | null,
  limit = 50,
): Promise<AudiobookItem[]> {
  if (!name) return [];
  try {
    const searchParams: Record<string, string | number> = { name, limit };
    if (seriesAsin) searchParams.asin = seriesAsin;
    return listOf(
      await readJson<ListResponse>(audiobookClient.get('audiobooks/series', { searchParams })),
    );
  } catch (err) {
    console.error('Failed to load the series:', err);
    return [];
  }
}

export async function fetchByAuthor(name: string, limit = 20): Promise<AudiobookItem[]> {
  if (!name) return [];
  try {
    return listOf(
      await readJson<ListResponse>(
        audiobookClient.get('audiobooks/author', { searchParams: { name, limit } }),
      ),
    );
  } catch (err) {
    console.error('Failed to load the author bibliography:', err);
    return [];
  }
}

export async function fetchByNarrator(name: string, limit = 20): Promise<AudiobookItem[]> {
  if (!name) return [];
  try {
    return listOf(
      await readJson<ListResponse>(
        audiobookClient.get('audiobooks/narrator', { searchParams: { name, limit } }),
      ),
    );
  } catch (err) {
    console.error('Failed to load the narrator performances:', err);
    return [];
  }
}

export async function fetchBrowse(
  categoryName: string,
  sort: 'bestsellers' | 'newest' = 'bestsellers',
  limit = 20,
): Promise<AudiobookItem[]> {
  try {
    const searchParams: Record<string, string | number> = { sort, limit };
    if (categoryName) searchParams.category = categoryName;
    return listOf(
      await readJson<ListResponse>(audiobookClient.get('audiobooks/browse', { searchParams })),
    );
  } catch (err) {
    console.error('Failed to browse audiobooks:', err);
    return [];
  }
}

interface PersonResponse {
  success?: boolean;
  profile?: AudiobookPersonProfile;
  error?: string;
}

/**
 * The grouped bibliography behind an author or narrator page.
 *
 * Keyed on the name because Audible's own author ASIN is not usable as a
 * filter — it is accepted and then ignored, returning the whole storefront.
 */
export async function fetchPersonProfile(
  name: string,
  role: AudiobookRole,
): Promise<AudiobookPersonProfile | null> {
  if (!name) return null;
  try {
    const data = await readJson<PersonResponse>(
      audiobookClient.get('audiobooks/person', { searchParams: { name, role } }),
    );
    return data?.success && data.profile ? data.profile : null;
  } catch (err) {
    console.error(`Failed to load the ${role} profile for ${name}:`, err);
    return null;
  }
}

export async function fetchCategories(): Promise<AudiobookCategory[]> {
  try {
    const data = await readJson<CategoriesResponse>(audiobookClient.get('audiobooks/categories'));
    return data?.success && Array.isArray(data.categories) ? data.categories : [];
  } catch (err) {
    console.error('Failed to load the audiobook genre tree:', err);
    return [];
  }
}

/**
 * Route a sample through the server so it plays without a CORS failure.
 *
 * The CDNs that host previews do not send CORS headers, so an <audio> element
 * pointed straight at one can refuse to play. The proxy also forwards Range,
 * which is what makes seeking inside a preview work.
 */
export function sampleStreamUrl(sampleUrl: string): string {
  return `/api/audiobooks/sample-proxy?url=${encodeURIComponent(sampleUrl)}`;
}

// ---------------------------------------------------------------------------
// Wishlist
// ---------------------------------------------------------------------------

interface WishlistResponse {
  success?: boolean;
  items?: AudiobookWishlistEntry[];
  counts?: AudiobookWishlistCounts;
  worker?: AudiobookWishlistSummary;
  error?: string;
}

interface MutationResponse {
  success?: boolean;
  wishlisted?: boolean;
  error?: string;
}

interface ReleasesResponse {
  success?: boolean;
  releases?: AudiobookReleaseCandidate[];
  error?: string;
}

export interface AudiobookWishlistView {
  items: AudiobookWishlistEntry[];
  counts: AudiobookWishlistCounts;
  worker: AudiobookWishlistSummary | null;
}

const EMPTY_COUNTS: AudiobookWishlistCounts = {
  wanted: 0,
  searching: 0,
  grabbed: 0,
  done: 0,
  failed: 0,
  total: 0,
};

export async function fetchWishlist(): Promise<AudiobookWishlistView> {
  try {
    const data = await readJson<WishlistResponse>(audiobookClient.get('audiobooks/wishlist'));
    if (!data?.success) return { items: [], counts: EMPTY_COUNTS, worker: null };
    return {
      items: Array.isArray(data.items) ? data.items : [],
      counts: data.counts ?? EMPTY_COUNTS,
      worker: data.worker ?? null,
    };
  } catch (err) {
    console.error('Failed to load the audiobook wishlist:', err);
    return { items: [], counts: EMPTY_COUNTS, worker: null };
  }
}

/**
 * Want a book, in one narrator's reading or any.
 *
 * `exact` holds the download to the reading this ASIN actually is — on Audible
 * the narrator is baked into the ASIN, so choosing the book already chose a
 * performance. `any` accepts another narrator's edition. Never both: a book is
 * always exactly one narrator.
 */
export async function addToWishlist(
  asin: string,
  narratorMode: AudiobookNarratorMode = 'exact',
): Promise<boolean> {
  if (!asin) return false;
  try {
    const data = await readJson<MutationResponse>(
      audiobookClient.post('audiobooks/wishlist', { json: { asin, narrator_mode: narratorMode } }),
    );
    return Boolean(data?.success);
  } catch (err) {
    console.error(`Failed to wishlist ${asin}:`, err);
    return false;
  }
}

export async function removeFromWishlist(asin: string): Promise<boolean> {
  if (!asin) return false;
  try {
    const data = await readJson<MutationResponse>(
      audiobookClient.delete(`audiobooks/wishlist/${encodeURIComponent(asin)}`),
    );
    return Boolean(data?.success);
  } catch (err) {
    console.error(`Failed to un-wishlist ${asin}:`, err);
    return false;
  }
}

/**
 * Change how strictly a wanted book must match its narrator.
 *
 * Its own call rather than a re-add: adding is idempotent, so re-adding must
 * not rewrite a choice already made, and changing the choice must not reset the
 * book's retry backoff.
 */
export async function setNarratorMode(
  asin: string,
  narratorMode: AudiobookNarratorMode,
): Promise<boolean> {
  if (!asin) return false;
  try {
    const data = await readJson<MutationResponse>(
      audiobookClient.patch(`audiobooks/wishlist/${encodeURIComponent(asin)}`, {
        json: { narrator_mode: narratorMode },
      }),
    );
    return Boolean(data?.success);
  } catch (err) {
    console.error(`Failed to change the narrator mode for ${asin}:`, err);
    return false;
  }
}

/** Run a wishlist pass now instead of waiting for the timer. */
export async function runWishlistPass(): Promise<Record<string, number> | null> {
  try {
    const data = await readJson<{ success?: boolean; summary?: Record<string, number> }>(
      audiobookClient.post('audiobooks/wishlist/search', { json: {} }),
    );
    return data?.success ? (data.summary ?? null) : null;
  } catch (err) {
    console.error('Failed to run a wishlist pass:', err);
    return null;
  }
}

// ---------------------------------------------------------------------------
// Releases
// ---------------------------------------------------------------------------

/**
 * Ask the indexers what is actually downloadable for a book.
 *
 * Slow by nature — it is a real search fanning out to every configured
 * indexer — so the UI must show it working rather than assume it is instant.
 */
export async function fetchReleases(asin: string): Promise<AudiobookReleaseCandidate[]> {
  if (!asin) return [];
  try {
    const data = await readJson<ReleasesResponse>(
      audiobookClient.get(`audiobooks/releases/${encodeURIComponent(asin)}`, { timeout: 120000 }),
    );
    return data?.success && Array.isArray(data.releases) ? data.releases : [];
  } catch (err) {
    console.error(`Failed to find releases for ${asin}:`, err);
    return [];
  }
}

/** Begin a search. Returns the job id to poll, or null if it could not start. */
export async function startReleaseSearch(
  asin: string,
): Promise<{ id: string; pollMs: number } | null> {
  if (!asin) return null;
  try {
    const data = await readJson<{ success?: boolean; id?: string; poll_ms?: number }>(
      audiobookClient.post(`audiobooks/releases/${encodeURIComponent(asin)}/start`),
    );
    if (!data?.success || !data.id) return null;
    return { id: data.id, pollMs: data.poll_ms || 1200 };
  } catch (err) {
    console.error(`Failed to start a release search for ${asin}:`, err);
    return null;
  }
}

/**
 * The ranked pool so far. Always the WHOLE list, never a delta — ranking is
 * global, so a peer with the right narrator has to be able to sort above a
 * torrent found two queries earlier.
 *
 * `expired` means the job is gone (server restart, or it aged out). The caller
 * stops polling and keeps whatever it already rendered rather than blanking.
 */
export async function pollReleaseSearch(id: string): Promise<{
  releases: AudiobookReleaseCandidate[];
  stage: string;
  complete: boolean;
  error: string;
  expired: boolean;
} | null> {
  if (!id) return null;
  try {
    const data = await readJson<{
      success?: boolean;
      releases?: AudiobookReleaseCandidate[];
      stage?: string;
      complete?: boolean;
      error?: string;
      expired?: boolean;
    }>(audiobookClient.get(`audiobooks/releases/poll?id=${encodeURIComponent(id)}`));
    if (!data?.success)
      return { releases: [], stage: '', complete: true, error: '', expired: true };
    return {
      releases: Array.isArray(data.releases) ? data.releases : [],
      stage: data.stage || '',
      complete: Boolean(data.complete),
      error: data.error || '',
      expired: false,
    };
  } catch {
    // A dropped poll is not fatal: the next tick tries again, and a job that
    // really is gone reports expired above.
    return null;
  }
}

/** Tell the server to forget a search the user walked away from. */
export async function cancelReleaseSearch(id: string): Promise<void> {
  if (!id) return;
  try {
    await audiobookClient.delete(`audiobooks/releases/poll?id=${encodeURIComponent(id)}`);
  } catch {
    // Best effort — the job expires on its own.
  }
}

export interface AudiobookReleaseContents {
  files: { name: string; size: number }[];
  summary: {
    total: number;
    audio_count: number;
    extra_count: number;
    audio_bytes: number;
    total_bytes: number;
    formats: string[];
  };
  note: string;
}

/**
 * What is actually inside a release. A name and a size cannot tell one m4b
 * apart from 87 mp3s plus somebody's discography.
 *
 * Reads only — it decodes the .torrent or NZB the indexer already offers.
 * Soulseek needs no request at all, because the search result already carries
 * the peer's file list.
 */
export async function fetchReleaseContents(
  release: AudiobookReleaseCandidate,
): Promise<AudiobookReleaseContents | null> {
  try {
    const data = await readJson<{ success?: boolean } & AudiobookReleaseContents>(
      audiobookClient.post('audiobooks/releases/contents', { json: { release }, timeout: 40000 }),
    );
    if (!data?.success) return null;
    return {
      files: Array.isArray(data.files) ? data.files : [],
      summary: data.summary,
      note: data.note || '',
    };
  } catch (err) {
    console.error('Failed to read the release contents:', err);
    return null;
  }
}

// ---------------------------------------------------------------------------
// Library — what is actually on disk
// ---------------------------------------------------------------------------

export async function fetchLibrary(): Promise<AudiobookLibrary> {
  const data = await readJson<{
    success?: boolean;
    books?: AudiobookLibraryEntry[];
    total_bytes?: number;
    root?: string;
    scan?: AudiobookLibraryScan;
    error?: string;
  }>(audiobookClient.get('audiobooks/library'));
  if (!data.success || !Array.isArray(data.books)) {
    throw new Error(data.error || 'Could not read your audiobook library.');
  }
  return {
    books: data.books,
    totalBytes: data.total_bytes || 0,
    root: data.root || '',
    scan: data.scan || { status: 'never' },
  };
}

/** Use the engine's Run Now so the scan has normal progress and run history. */
export async function scanLibrary(): Promise<void> {
  const automations = await readJson<Automation[] | { error?: string }>(
    apiClient.get('automations'),
  );
  if (!Array.isArray(automations))
    throw new Error(automations.error || 'Could not load automations.');
  const candidates = automations.filter((a) => a.action_type === 'audiobook_scan_library');
  const automation = candidates.find((a) => a.is_system) || candidates[0];
  if (!automation)
    throw new Error('Add Scan Audiobook Library on the Automations page, then try again.');
  await runAutomation(automation.id);
}

/**
 * Remove one book from disk and from the record.
 *
 * Goes to the recycle bin rather than being unlinked, so a mistake is
 * recoverable for as long as the keep window allows.
 */
export async function deleteLibraryBook(
  asin: string,
): Promise<{ ok: boolean; recycled: boolean; error: string }> {
  try {
    const data = await readJson<{ success?: boolean; recycled?: boolean; error?: string }>(
      audiobookClient.delete(`audiobooks/library/${encodeURIComponent(asin)}`),
    );
    return {
      ok: Boolean(data?.success),
      recycled: Boolean(data?.recycled),
      error: data?.error || '',
    };
  } catch (err) {
    console.error('Failed to delete the book:', err);
    return {
      ok: false,
      recycled: false,
      error: err instanceof Error ? err.message : 'Request failed',
    };
  }
}

// ---------------------------------------------------------------------------
// Recycle bin — deleted books, still recoverable
// ---------------------------------------------------------------------------

export async function fetchRecycleBin(): Promise<{
  entries: AudiobookRecycledBook[];
  keepDays: number;
}> {
  try {
    const data = await readJson<{
      success?: boolean;
      entries?: AudiobookRecycledBook[];
      keep_days?: number;
    }>(audiobookClient.get('audiobooks/library/recycle'));
    return {
      entries: data?.success && Array.isArray(data.entries) ? data.entries : [],
      keepDays: data?.keep_days ?? 7,
    };
  } catch (err) {
    console.error('Failed to read the recycle bin:', err);
    return { entries: [], keepDays: 7 };
  }
}

/** Put one book back exactly where it came from. */
export async function restoreRecycledBook(name: string): Promise<{ ok: boolean; error: string }> {
  try {
    const data = await readJson<{ success?: boolean; error?: string }>(
      audiobookClient.post(`audiobooks/library/recycle/${encodeURIComponent(name)}`),
    );
    return { ok: Boolean(data?.success), error: data?.error || '' };
  } catch (err) {
    console.error('Failed to restore the book:', err);
    return { ok: false, error: 'Request failed' };
  }
}

/** Erase one now, without waiting for the keep window. */
export async function purgeRecycledBook(name: string): Promise<boolean> {
  try {
    const data = await readJson<{ success?: boolean }>(
      audiobookClient.delete(`audiobooks/library/recycle/${encodeURIComponent(name)}`),
    );
    return Boolean(data?.success);
  } catch (err) {
    console.error('Failed to purge the book:', err);
    return false;
  }
}

export async function emptyRecycleBin(): Promise<boolean> {
  try {
    const data = await readJson<{ success?: boolean }>(
      audiobookClient.delete('audiobooks/library/recycle'),
    );
    return Boolean(data?.success);
  } catch (err) {
    console.error('Failed to empty the recycle bin:', err);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Blocklist — releases never to offer or grab again
// ---------------------------------------------------------------------------

export async function fetchBlocklist(): Promise<AudiobookBlockedRelease[]> {
  try {
    const data = await readJson<{ success?: boolean; blocked?: AudiobookBlockedRelease[] }>(
      audiobookClient.get('audiobooks/blocklist'),
    );
    return data?.success && Array.isArray(data.blocked) ? data.blocked : [];
  } catch (err) {
    console.error('Failed to load the audiobook blocklist:', err);
    return [];
  }
}

/** Block one release. The RELEASE, never the book — the book stays wanted. */
export async function blockRelease(
  release: AudiobookReleaseCandidate,
  asin = '',
  bookTitle = '',
  reason = 'Blocked by hand',
): Promise<boolean> {
  try {
    const data = await readJson<{ success?: boolean }>(
      audiobookClient.post('audiobooks/blocklist', {
        json: { release, asin, book_title: bookTitle, reason },
      }),
    );
    return Boolean(data?.success);
  } catch (err) {
    console.error('Failed to block the release:', err);
    return false;
  }
}

export async function unblockRelease(key: string): Promise<boolean> {
  try {
    const data = await readJson<{ success?: boolean }>(
      audiobookClient.delete(`audiobooks/blocklist/${encodeURIComponent(key)}`),
    );
    return Boolean(data?.success);
  } catch (err) {
    console.error('Failed to unblock the release:', err);
    return false;
  }
}

export async function clearBlocklist(): Promise<boolean> {
  try {
    const data = await readJson<{ success?: boolean }>(
      audiobookClient.delete('audiobooks/blocklist'),
    );
    return Boolean(data?.success);
  } catch (err) {
    console.error('Failed to clear the blocklist:', err);
    return false;
  }
}

export async function grabRelease(
  asin: string,
  release: AudiobookReleaseCandidate,
): Promise<{ ok: boolean; error: string; ref: string }> {
  try {
    const data = await readJson<{ success?: boolean; error?: string; ref?: string }>(
      audiobookClient.post('audiobooks/grab', { json: { asin, release } }),
    );
    // The ref is how the row that was clicked follows its own download.
    return { ok: Boolean(data?.success), error: data?.error || '', ref: data?.ref || '' };
  } catch (err) {
    console.error('Failed to grab the release:', err);
    return { ok: false, error: 'Request failed', ref: '' };
  }
}

/**
 * What has been grabbed and where it has got to.
 *
 * Separate from the music Downloads page on purpose: an audiobook is one
 * release that becomes a folder of chapters, which a per-track view has nowhere
 * sensible to put.
 */
export async function fetchDownloads(activeOnly = false): Promise<AudiobookDownload[]> {
  try {
    const data = await readJson<{ success?: boolean; downloads?: AudiobookDownload[] }>(
      audiobookClient.get('audiobooks/downloads', {
        searchParams: activeOnly ? { active: 1 } : {},
      }),
    );
    return data?.success && Array.isArray(data.downloads) ? data.downloads : [];
  } catch (err) {
    console.error('Failed to load audiobook downloads:', err);
    return [];
  }
}

// ---------------------------------------------------------------------------
// Watchlist — followed authors
// ---------------------------------------------------------------------------

export async function fetchFollowedAuthors(): Promise<AudiobookFollowedAuthor[]> {
  try {
    const data = await readJson<{ success?: boolean; authors?: AudiobookFollowedAuthor[] }>(
      audiobookClient.get('audiobooks/watchlist'),
    );
    return data?.success && Array.isArray(data.authors) ? data.authors : [];
  } catch (err) {
    console.error('Failed to load followed authors:', err);
    return [];
  }
}

/**
 * Follow an author so their new releases get wishlisted.
 *
 * The cutoff is the day you follow them — following an author means "tell me
 * about the next one", not "queue the eighty-eight they already wrote".
 */
export async function followAuthor(name: string, coverUrl = ''): Promise<boolean> {
  if (!name) return false;
  try {
    const data = await readJson<{ success?: boolean }>(
      audiobookClient.post('audiobooks/watchlist', { json: { name, cover_url: coverUrl } }),
    );
    return Boolean(data?.success);
  } catch (err) {
    console.error(`Failed to follow ${name}:`, err);
    return false;
  }
}

export async function unfollowAuthor(name: string): Promise<boolean> {
  if (!name) return false;
  try {
    const data = await readJson<{ success?: boolean }>(
      audiobookClient.delete(`audiobooks/watchlist/${encodeURIComponent(name)}`),
    );
    return Boolean(data?.success);
  } catch (err) {
    console.error(`Failed to unfollow ${name}:`, err);
    return false;
  }
}

/** Check followed authors now instead of waiting for the daily automation. */
/** Change one followed author's settings from their card. */
export async function updateFollowedAuthor(
  name: string,
  fields: { auto_wishlist?: number; narrator_mode?: string; since_date?: string },
): Promise<boolean> {
  try {
    const data = await readJson<{ success?: boolean }>(
      audiobookClient.patch(`audiobooks/watchlist/${encodeURIComponent(name)}`, { json: fields }),
    );
    return Boolean(data?.success);
  } catch (err) {
    console.error(`Failed to update the followed author ${name}:`, err);
    return false;
  }
}

export async function runAuthorScan(): Promise<Record<string, number> | null> {
  try {
    const data = await readJson<{ success?: boolean; summary?: Record<string, number> }>(
      audiobookClient.post('audiobooks/watchlist/scan', { json: {} }),
    );
    return data?.success ? (data.summary ?? null) : null;
  } catch (err) {
    console.error('Failed to check followed authors:', err);
    return null;
  }
}
