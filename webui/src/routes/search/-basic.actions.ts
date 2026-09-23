/**
 * What happens after the user picks how a basic search result comes in.
 *
 * Two ways today, picked in the download chooser:
 *   - as-is: POST /api/download, the file keeps its own name and tags
 *   - enriched: hands off to the matching modal, which is still vanilla
 *     (wishlist-tools.js openMatchingModal) until the rebuild replaces it
 *
 * stream used to live here too. it's gone from the page on purpose.
 */

import type { DownloadResponse } from './-basic.api';
import type {
  BasicAlbum,
  BasicResult,
  BasicTrack,
  DownloadMode,
  DownloadTarget,
} from './-basic.types';

import { postDownload } from './-basic.api';

/**
 * A blocklisted artist answers with {blocked}. Ask, and on yes send the same
 * download again with ignore_blocklist. Returns the final answer, or null when
 * the user said no.
 */
async function postWithBlocklistCheck(
  payload: BasicResult | (Record<string, unknown> & { result_type: string }),
): Promise<DownloadResponse | null> {
  const data = await postDownload(payload);
  if (!data.blocked) return data;
  const name = data.blocked_name || 'this artist';
  const ok = await window.showConfirmDialog?.({
    title: 'On your blocklist',
    message: `${name} is on your blocklist. Download this anyway?`,
    confirmText: 'Download anyway',
    cancelText: 'Skip',
  });
  if (!ok) {
    window.showToast?.(`Skipped, ${name} is blocklisted`, 'info');
    return null;
  }
  return postDownload({ ...payload, ignore_blocklist: true });
}

export async function downloadTrack(track: BasicTrack): Promise<void> {
  try {
    const data = await postWithBlocklistCheck(track);
    if (!data) return;
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
 * `result_type` is forced to 'track': without it the server would take the
 * album branch and look for a `tracks` array this row doesn't have. the
 * server's TrackResult already stamps it, this just doesn't lean on that.
 */
export async function downloadAlbumTrack(album: BasicAlbum, trackIndex: number): Promise<void> {
  const track = album.tracks?.[trackIndex];
  if (!track) return;
  try {
    const data = await postWithBlocklistCheck({ ...track, result_type: 'track' });
    if (!data) return;
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
 * An album's enriched download.
 *
 * The FIRST TRACK is handed to the modal as the thing to identify, with the
 * album as context. a folder has no artist/title tags of its own worth
 * matching on, so the modal searches with a real track's and applies the
 * answer to the whole album.
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

/**
 * The matching modal's "Skip Matching" button lands here (published on window
 * by the search page, the modal is vanilla). the result object is passed
 * straight through, never round-tripped through an index.
 */
export async function downloadUnmatched(result: BasicResult): Promise<void> {
  if (!result) return;
  if (result.result_type === 'album') {
    await downloadAlbum(result);
    return;
  }
  await downloadTrack(result);
}

/** The chooser's answer, sent where it goes. */
export function startDownload(target: DownloadTarget, mode: DownloadMode): void {
  if (mode === 'enriched') {
    if (target.kind === 'track') matchedDownloadTrack(target.track);
    else if (target.kind === 'album') matchedDownloadAlbum(target.album);
    else matchedDownloadAlbumTrack(target.album, target.trackIndex);
    return;
  }
  if (target.kind === 'track') void downloadTrack(target.track);
  else if (target.kind === 'album') void downloadAlbum(target.album);
  else void downloadAlbumTrack(target.album, target.trackIndex);
}
