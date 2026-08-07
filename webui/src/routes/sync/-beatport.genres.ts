/**
 * The genre browser's data layer — beatport-ui.js 2234-2625.
 *
 * The genre list is cheap; the genre IMAGES are one HTTP request each, against
 * a scraper, for ~40 genres. The vanilla therefore does something more careful
 * than it looks:
 *
 *  1. fetch the list and render it immediately with emoji placeholders,
 *  2. then fill the images in the background with TWO cooperative workers
 *     pulling from a shared queue, 100ms apart,
 *  3. cache each resolved url ON the genre object, so reopening the modal
 *     shows what was already fetched and only queues the rest,
 *  4. and PAUSE the workers when the modal closes, resuming from the same
 *     queue next time.
 *
 * All four survive here. The one change is where the cache lives: the vanilla
 * mutates `genre.imageUrl` in place, which React cannot see. The urls live in a
 * Map instead, and the loader reports each one as it lands.
 */

import type { BeatportGenre } from './-beatport.api';

import { fetchBeatportGenreImage, fetchBeatportGenres } from './-beatport.api';
import { filterBeatportGenres } from './-beatport.core';

/**
 * The vanilla finds a card by BOTH attributes at once
 * (`[data-genre-slug="…"][data-genre-id="…"]`, 2511-2513), so the identity of a
 * genre is the pair, not either alone. Kept as the cache key for the same
 * reason: two genres sharing a slug would otherwise share an image.
 */
export function beatportGenreKey(genre: Pick<BeatportGenre, 'slug' | 'id'>): string {
  return `${genre.slug}:${genre.id}`;
}

/**
 * Module-scoped, mirroring `genreBrowserCache` (2235-2241).
 *
 * WHY IT SURVIVES A CLOSE: the vanilla keeps this across modal opens on purpose
 * — the comment at 2318 says so — because re-opening would otherwise re-scrape
 * every genre image. Two of the vanilla's five fields are dropped: `lastLoaded`
 * is written and never read, and `imageWorkers` is declared and never assigned.
 */
interface GenreBrowserCache {
  genres: BeatportGenre[] | null;
  images: Map<string, string>;
  imagesLoaded: boolean;
  imageLoadingActive: boolean;
}

const cache: GenreBrowserCache = {
  genres: null,
  images: new Map(),
  imagesLoaded: false,
  imageLoadingActive: false,
};

/** Test seam. Nothing in production clears this — a reload is the reset. */
export function resetGenreBrowserCache(): void {
  cache.genres = null;
  cache.images = new Map();
  cache.imagesLoaded = false;
  cache.imageLoadingActive = false;
}

export function getCachedGenres(): BeatportGenre[] | null {
  return cache.genres;
}

/** A copy, so a caller cannot mutate the cache by holding the Map. */
export function getCachedGenreImages(): Map<string, string> {
  return new Map(cache.images);
}

export function isGenreImageLoadingActive(): boolean {
  return cache.imageLoadingActive;
}

export function areGenreImagesLoaded(): boolean {
  return cache.imagesLoaded;
}

/**
 * 2327-2330: closing the modal does not cancel the in-flight work, it PAUSES
 * it. The workers check the flag between items and stop; the queue is rebuilt
 * from whatever is still missing next time the modal opens.
 */
export function pauseGenreImageLoading(): void {
  cache.imageLoadingActive = false;
}

/* ── The list (2341-2448) ─────────────────────────────────────────────────── */

/**
 * Fetches, filters and caches the genre list.
 *
 * Note this endpoint checks `response.ok`, alone among the Beatport fetches,
 * and reports the status line in the message the user sees (2362-2364).
 */
export async function loadBeatportGenreList(signal?: AbortSignal): Promise<BeatportGenre[]> {
  const data = await fetchBeatportGenres(signal);
  const filtered = filterBeatportGenres(data.genres ?? []);
  cache.genres = filtered;
  cache.imagesLoaded = false;
  return filtered;
}

/* ── The images (2526-2625) ───────────────────────────────────────────────── */

/**
 * 2433: images load only when there are MORE THAN five genres.
 *
 * That is a strict `>`, so a list of exactly five gets no images at all and
 * keeps its emoji. Transcribed rather than corrected — the real list is ~40, so
 * the threshold only bites when the scrape has mostly failed, and in that case
 * not firing 5 more requests at a struggling backend is defensible.
 */
export const GENRE_IMAGE_MIN_GENRES = 5;

export function shouldLoadGenreImages(genreCount: number): boolean {
  return genreCount > GENRE_IMAGE_MIN_GENRES;
}

export interface GenreImageLoadOptions {
  /** 2531. Two, not one and not four. */
  workers?: number;
  /** 2593: a deliberate throttle between requests, per worker. */
  delayMs?: number;
  sleep?: (ms: number) => Promise<void>;
  signal?: AbortSignal;
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Fill in the genre images, reporting each one as it lands.
 *
 * Returns when the queue is empty OR when something paused it. Which of those
 * happened decides whether the images are marked loaded — 2614-2624 — and that
 * distinction is the whole point: a paused run must NOT be recorded as
 * complete, or the remaining genres would keep their emoji for the session.
 *
 * A genre whose image request fails is counted as done and simply keeps its
 * placeholder (2580-2583). The vanilla never retries one, and neither does
 * this: a failure here costs a picture, and retrying costs the scraper.
 */
export async function loadGenreImagesProgressively(
  genres: readonly BeatportGenre[],
  onImage: (key: string, imageUrl: string) => void,
  options: GenreImageLoadOptions = {},
): Promise<void> {
  const { workers = 2, delayMs = 100, sleep = defaultSleep, signal } = options;

  // 2529: only the ones still missing. Reopening the modal re-enters here and
  // picks up exactly where the pause left off.
  const queue = genres.filter((genre) => !cache.images.has(beatportGenreKey(genre)));

  cache.imageLoadingActive = true;

  // 2539-2544: nothing to do is COMPLETE, not paused.
  if (queue.length === 0) {
    cache.imagesLoaded = true;
    cache.imageLoadingActive = false;
    return;
  }

  async function processImage(genre: BeatportGenre): Promise<void> {
    try {
      const data = await fetchBeatportGenreImage(genre.slug, genre.id, signal);
      // 2555: both a truthy success AND a url. A response with one and not the
      // other leaves the emoji in place.
      if (data?.success && data.image_url) {
        const key = beatportGenreKey(genre);
        cache.images.set(key, data.image_url);
        onImage(key, data.image_url);
      }
    } catch {
      // 2580: a genre without a picture is not an error worth showing.
    }
  }

  async function runWorker(): Promise<void> {
    while (queue.length > 0 && cache.imageLoadingActive) {
      const genre = queue.shift();
      if (genre) {
        await processImage(genre);
        await sleep(delayMs);
      }
      // 2597-2600. This is REDUNDANT with the `while` condition above, and
      // that is not an oversight in the transcription — the vanilla has both.
      //
      // Proven redundant rather than assumed: after the awaits, control reaches
      // this line and then the loop head, which re-tests the same flag; and the
      // flag is set true immediately before the workers start, so the loop can
      // never be entered with it already false. Deleting EITHER check alone
      // therefore changes nothing, which is exactly what the mutation pass
      // found. Deleting BOTH does change things, and is caught.
      //
      // Kept because the vanilla keeps it: a reader diffing the two should find
      // the same shape.
      if (!cache.imageLoadingActive) break;
    }
  }

  await Promise.all(Array.from({ length: workers }, () => runWorker()));

  if (cache.imageLoadingActive) {
    cache.imagesLoaded = true;
    cache.imageLoadingActive = false;
  }
  // Otherwise it was paused: imagesLoaded stays false, so the next open resumes
  // rather than assuming there is nothing left to fetch.
}

/* ── Search (2627-2639) ───────────────────────────────────────────────────── */

/**
 * A plain case-insensitive substring test on the NAME only — not the slug, so
 * searching 'deep-house' finds nothing while 'deep house' finds it.
 *
 * The vanilla hides non-matching cards with `style.display`, overwriting
 * whatever the stylesheet chose; the port filters the list instead. The
 * rendered result is the same, since the hidden cards contributed nothing.
 */
export function filterGenresBySearch<T extends { name: string }>(
  genres: readonly T[],
  searchTerm: string,
): T[] {
  const needle = searchTerm.toLowerCase();
  return genres.filter((genre) => genre.name.toLowerCase().includes(needle));
}
