/**
 * As-is downloads from basic search: POST /api/download, the file keeps its
 * own name and tags. the server makes it a batch, so it shows on the
 * Downloads page.
 *
 * enriched downloads are their own flow in their own modal
 * (-ui/enriched-modal.tsx). stream is gone from the page on purpose.
 */

import type { DownloadResponse } from './-basic.api';
import type { BasicAlbum, BasicResult, BasicTrack, DownloadTarget } from './-basic.types';

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

/** the chooser's as-is answer, sent where it goes */
export function startDownload(target: DownloadTarget): void {
  if (target.kind === 'track') void downloadTrack(target.track);
  else if (target.kind === 'album') void downloadAlbum(target.album);
  else void downloadAlbumTrack(target.album, target.trackIndex);
}
