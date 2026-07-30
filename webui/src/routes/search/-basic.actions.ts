/**
 * The download / stream / matched-download actions for basic search results.
 *
 * Ported from downloads.js, with one correction of record: three of those
 * handlers — matchedDownloadTrack, matchedDownloadAlbum and
 * matchedDownloadAlbumTrack — are declared TWICE, once in downloads.js
 * (4607/4618/4629) and once in wishlist-tools.js (2639/2651/2664), with
 * genuinely different behaviour. Both are top-level declarations in classic
 * scripts and wishlist-tools.js loads second (index.html:10869 vs 10872), so
 * its versions win and the downloads.js copies have never run. The live ones
 * are what is ported here.
 */

import type { BasicAlbum, BasicResult, BasicTrack } from './-basic.types';

import { postDownload } from './-basic.api';

/**
 * Sources whose "filename" is an opaque id rather than a path.
 *
 * They stream through the server, so the browser-codec check does not apply —
 * running it would reject every one of them for having no extension.
 */
const STREAMING_SOURCES = new Set(['youtube', 'tidal', 'qobuz', 'hifi']);

function isStreamingSource(username: string | null | undefined): boolean {
  return STREAMING_SOURCES.has(String(username ?? ''));
}

export async function downloadTrack(track: BasicTrack): Promise<void> {
  try {
    const data = await postDownload(track);
    if (data.success) window.showToast?.(`Download started: ${track.title ?? ''}`, 'success');
    else window.showToast?.(`Download failed: ${data.error}`, 'error');
  } catch (error) {
    console.error('Download error:', error);
    window.showToast?.('Failed to start download', 'error');
  }
}

export async function downloadAlbum(album: BasicAlbum): Promise<void> {
  try {
    const data = await postDownload(album);
    // The album route answers with a per-album summary ("Started 12 of 14…"),
    // so its message is shown rather than a generic line.
    if (data.success) window.showToast?.(data.message ?? '', 'success');
    else window.showToast?.(`Album download failed: ${data.error}`, 'error');
  } catch (error) {
    console.error('Album download error:', error);
    window.showToast?.('Failed to start album download', 'error');
  }
}

/**
 * One track out of an album.
 *
 * `result_type` is overridden to 'track': the row came out of an album, and
 * without this the server would take the album branch and look for a `tracks`
 * array the track does not have.
 */
export async function downloadAlbumTrack(album: BasicAlbum, trackIndex: number): Promise<void> {
  const track = album.tracks?.[trackIndex];
  if (!track) return;
  try {
    const data = await postDownload({ ...track, result_type: 'track' });
    if (data.success) window.showToast?.(`Download started: ${track.title ?? ''}`, 'success');
    else window.showToast?.(`Track download failed: ${data.error}`, 'error');
  } catch (error) {
    console.error('Track download error:', error);
    window.showToast?.('Failed to start track download', 'error');
  }
}

export function matchedDownloadTrack(track: BasicTrack): void {
  window.openMatchingModal?.(track, false, null);
}

/**
 * An album's matched download.
 *
 * The FIRST TRACK is handed to the modal as the thing to identify, with the
 * album as context — a folder has no artist/title metadata of its own worth
 * matching on, so the modal searches with a real track's tags and then applies
 * the answer to the whole album (wishlist-tools.js:2651).
 */
export function matchedDownloadAlbum(album: BasicAlbum): void {
  const reference = album.tracks?.[0] ?? album;
  window.openMatchingModal?.(reference, true, album);
}

export function matchedDownloadAlbumTrack(album: BasicAlbum, trackIndex: number): void {
  const track = album.tracks?.[trackIndex];
  if (!track) return;
  // `false` even though an album is passed: this is ONE track, and the modal
  // would otherwise ask the user to pick an album for a file they already
  // located inside one.
  window.openMatchingModal?.(track, false, album);
}

export async function streamTrack(track: BasicTrack): Promise<void> {
  try {
    if (!isStreamingSource(track.username) && track.filename) {
      if (!window.isAudioFormatSupported?.(track.filename)) {
        const format = window.getFileExtension?.(track.filename) ?? '';
        window.showToast?.(
          `Sorry, ${format.toUpperCase()} format is not supported in your browser. Try downloading instead.`,
          'error',
        );
        return;
      }
    }
    await window.startStream?.(track);
  } catch (error) {
    console.error('Track streaming error:', error);
    window.showToast?.('Failed to start track stream', 'error');
  }
}

/**
 * Stream one track of an album.
 *
 * The first branch is not a special case for tidiness: results from the
 * streaming sources arrive FLAT — the "album" is the track itself, with no
 * `tracks` array — so the row the user clicked is the album object, and its
 * title doubles as the album name the player displays.
 */
export async function streamAlbumTrack(album: BasicAlbum, trackIndex: number): Promise<void> {
  try {
    if (isStreamingSource(album.username)) {
      const flat = album as unknown as BasicTrack;
      await window.startStream?.({ ...flat, album: flat.title });
      return;
    }

    const track = album.tracks?.[trackIndex];
    if (!track) {
      window.showToast?.('Track not found in album', 'error');
      return;
    }

    // Album tracks can arrive without the fields the player needs; the album
    // is the fallback for each of them.
    const payload = {
      ...track,
      username: track.username || album.username,
      filename: track.filename,
      artist: track.artist || album.artist,
      album: track.album || album.album_title,
    };

    if (!isStreamingSource(payload.username) && payload.filename) {
      if (!window.isAudioFormatSupported?.(payload.filename)) {
        const format = window.getFileExtension?.(payload.filename) ?? '';
        window.showToast?.(
          `Sorry, ${format.toUpperCase()} format is not supported in web browsers. Try downloading instead.`,
          'error',
        );
        return;
      }
    }

    await window.startStream?.(payload);
  } catch (error) {
    console.error('Album track streaming error:', error);
    window.showToast?.('Failed to start track stream', 'error');
  }
}

/**
 * Download a result the user chose NOT to match, from the matched-download
 * modal's "Skip Matching" button.
 *
 * Published on window because that modal is still vanilla
 * (wishlist-tools.js:skipMatching). It replaces a call chain that could not
 * work, for two independent reasons:
 *
 *   1. it went `startDownload(window.currentSearchResults.indexOf(result))`,
 *      and `startDownload` (search.js:1329) indexed `searchResults` — a
 *      DIFFERENT array, the core.js global, only ever written by
 *      `performSearch()`, which nothing calls and whose `#search-input`
 *      element does not exist in index.html. So it was permanently `[]`, the
 *      lookup returned undefined, and the function returned silently;
 *   2. `startDownload` POSTs `/api/downloads/start`, which is not a route.
 *
 * The result object is passed straight through instead of being round-tripped
 * through an index, which is what made a cross-array lookup possible at all.
 */
export async function downloadUnmatched(result: BasicResult): Promise<void> {
  if (!result) return;
  if (result.result_type === 'album') {
    // The vanilla's album branch toasted "Starting album download (unmatched)"
    // over a comment reading "This would need to be implemented" — it reported
    // a download it never started.
    await downloadAlbum(result);
    return;
  }
  await downloadTrack(result);
}
