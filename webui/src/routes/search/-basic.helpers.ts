/**
 * Pure filter/sort/format logic for basic search.
 *
 * Ported from `applyFiltersAndSort` + `calculateRelevanceScore`
 * (wishlist-tools.js:1666-1755), with four defects fixed rather than carried
 * over. Each is called out at the code that fixes it; the short version:
 *
 *   1. every row scored 0 on Quality, because `quality_score` never reached
 *      the browser (fixed server-side in `core/search/basic.py`);
 *   2. Size/Bitrate/Duration read track-only field names, so every album
 *      ranked 0 and sank below every track regardless of its actual size;
 *   3. Name and Uploader both compared `album_title || title` — Uploader
 *      never looked at a username, and albums never sorted at all;
 *   4. the direction was inverted: the comparator already emits each key's
 *      natural order, and the vanilla then reversed it whenever the user had
 *      NOT asked for a reversal, so the default view was backwards under a ↓.
 */

import type { BasicAlbum, BasicResult, BasicTrack, FilterState, SortKey } from './-basic.types';

import { isAlbum } from './-basic.types';

/** The display name of a result, whichever shape it is. */
export function resultTitle(result: BasicResult): string {
  return (isAlbum(result) ? result.album_title : result.title) || '';
}

/** Total bytes, whichever shape it is (`total_size` on an album). */
export function resultSize(result: BasicResult): number {
  return (isAlbum(result) ? result.total_size : result.size) || 0;
}

/**
 * The format a result should be filtered and labelled by.
 *
 * An album carries `dominant_quality` and no `quality` at all, so the vanilla's
 * `result.quality || 'Mixed'` label rendered every album — including a pure
 * FLAC one — as "Mixed".
 */
export function resultFormat(result: BasicResult): string {
  return (isAlbum(result) ? result.dominant_quality : result.quality) || '';
}

/**
 * An album's bitrate is the best of its tracks — the same rule
 * `AlbumResult.audio_quality` uses server-side (`max(bitrates)`).
 */
export function resultBitrate(result: BasicResult): number {
  if (!isAlbum(result)) return result.bitrate || 0;
  return result.tracks.reduce((best, track) => Math.max(best, track.bitrate || 0), 0);
}

/** An album's duration is its runtime: the sum of its tracks (milliseconds). */
export function resultDuration(result: BasicResult): number {
  if (!isAlbum(result)) return result.duration || 0;
  return result.tracks.reduce((total, track) => total + (track.duration || 0), 0);
}

/**
 * 0..1 relevance, weighted search terms 40% / quality 25% / uploader
 * reliability 20% / file completeness 15%.
 *
 * The term guard is not cosmetic: terms of one character are dropped, so a
 * one-letter query left `termMatches / 0` = NaN, which poisons every
 * comparison it takes part in and scrambles the whole list.
 */
export function relevanceScore(result: BasicResult, query: string): number {
  const terms = query
    .toLowerCase()
    .split(' ')
    .filter((term) => term.length > 1);

  const haystack = [
    resultTitle(result),
    result.artist ?? '',
    isAlbum(result) ? '' : (result.album ?? ''),
  ]
    .join(' ')
    .toLowerCase();

  const matched = terms.filter((term) => haystack.includes(term)).length;
  const termScore = terms.length ? matched / terms.length : 0;

  const reliability =
    ((result.free_upload_slots || 0) > 0 ? 0.5 : 0) +
    Math.min(1, (result.upload_speed || 0) / 500) * 0.5;

  const completeness =
    Math.min(1, resultBitrate(result) / 320) * 0.5 + (resultDuration(result) > 0 ? 0.5 : 0);

  return (
    termScore * 0.4 + (result.quality_score || 0) * 0.25 + reliability * 0.2 + completeness * 0.15
  );
}

/**
 * The value a result sorts on, and which way that key naturally runs.
 *
 * Keeping both in one place is what stops a repeat of the Uploader bug: the
 * vanilla decided direction by `typeof`, so the moment `a[key]` came back a
 * string it ran the title comparison — no matter which key had been asked for.
 */
type SortValue = { text: string } | { number: number };

function sortValue(result: BasicResult, key: SortKey, query: string): SortValue {
  switch (key) {
    case 'relevance':
      return { number: relevanceScore(result, query) };
    case 'quality_score':
      return { number: result.quality_score || 0 };
    case 'size':
      return { number: resultSize(result) };
    case 'bitrate':
      return { number: resultBitrate(result) };
    case 'duration':
      return { number: resultDuration(result) };
    case 'title':
      return { text: resultTitle(result).toLowerCase() };
    case 'username':
      return { text: (result.username || '').toLowerCase() };
  }
}

/**
 * Compare in each key's NATURAL order: numbers high-to-low (a bigger score,
 * bitrate or file is the better hit), text A-to-Z.
 */
function compare(a: SortValue, b: SortValue): number {
  if ('text' in a && 'text' in b) return a.text.localeCompare(b.text);
  if ('number' in a && 'number' in b) return b.number - a.number;
  return 0; // unreachable: a key yields one kind for every shape.
}

export function filterResults(results: BasicResult[], filters: FilterState): BasicResult[] {
  return results.filter((result) => {
    if (filters.type !== 'all' && result.result_type !== filters.type) return false;
    if (filters.format !== 'all' && resultFormat(result).toLowerCase() !== filters.format) {
      return false;
    }
    return true;
  });
}

/**
 * Filter, then sort, then reverse ONLY if the user asked for it.
 *
 * `Array.prototype.sort` is stable per spec, so equal-scoring rows keep the
 * order the server sent — which is itself quality-ranked.
 */
export function applyFiltersAndSort(
  results: BasicResult[],
  filters: FilterState,
  query: string,
): BasicResult[] {
  const filtered = filterResults(results, filters);
  const sorted = [...filtered].sort((a, b) =>
    compare(sortValue(a, filters.sort, query), sortValue(b, filters.sort, query)),
  );
  if (filters.reversed) sorted.reverse();
  return sorted;
}

// ── Display formatting ────────────────────────────────────────────────────

/** `12.3 MB`, or the vanilla's honest placeholder when there is no size. */
export function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return 'Unknown size';
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** `320kbps`, or '' — the vanilla renders nothing rather than a zero. */
export function formatBitrate(bitrate: number | null | undefined): string {
  return bitrate ? `${bitrate}kbps` : '';
}

/** The album's format for its detail line; 'Mixed' only when truly unknown. */
export function albumFormatLabel(album: BasicAlbum): string {
  return album.dominant_quality || 'Mixed';
}

/**
 * Indices at which a new disc starts, detected from track numbers resetting.
 *
 * A multi-disc folder arrives as one flat track list, and the only signal is a
 * track number that fails to advance. Index 0 is never a break — the header for
 * disc 1 is rendered from the set being non-empty, not from a break at the top.
 */
export function detectDiscBreaks(tracks: BasicTrack[]): Set<number> {
  const breaks = new Set<number>();
  let lastNumber = 0;
  tracks.forEach((track, index) => {
    const number = track.track_number || 0;
    if (index > 0 && number > 0 && number <= lastNumber) breaks.add(index);
    if (number > 0) lastNumber = number;
  });
  return breaks;
}
