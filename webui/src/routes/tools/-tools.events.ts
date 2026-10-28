/**
 * The socket → React seam for the Tools page.
 *
 * `socket` is a module-scoped `let` in core.js, so a React route cannot
 * subscribe to it directly. core.js re-broadcasts the three frames the Tools
 * page cares about as window CustomEvents — the same `ss:` seam already used by
 * `ss:watchlist-scan` and `ss:automation-progress`, and added the same way:
 * purely additive, with the existing vanilla handlers left untouched.
 *
 * The vanilla handlers keep driving the DASHBOARD nodes they own (the worker orb
 * `#repair-button`, its tooltip, and the orb's own `#repair-findings-badge`).
 * What they must stop driving, once P7 deletes the vanilla tools markup, is the
 * tools-side DOM this page now owns — the tab badge, the master toggle, the job
 * cards and the media-scan card. Those come through here instead.
 */

import { useShellEvent } from '@/platform/shell/use-shell-event';

import type { MediaScanStatus, RepairJobProgress, RepairStatus } from './-tools.types';

import { toolsMedia } from './-tools.media';

/** `enrichment:repair` — worker state, findings count, master enabled flag. */
export const REPAIR_STATUS_EVENT = 'ss:repair-status';
/** `repair:progress` — a partial map of jobId → live frame. */
export const REPAIR_PROGRESS_EVENT = 'ss:repair-progress';
/** `video:repair:progress`, the video side's frame, same shape. core.js
 *  re-broadcasts it since the video tools page went React. */
export const VIDEO_REPAIR_PROGRESS_EVENT = 'ss:video-repair-progress';
/** `scan:media` — pushed every 2s whether or not a scan is running. */
export const MEDIA_SCAN_EVENT = 'ss:media-scan';

export interface MediaScanFrame {
  success?: boolean;
  status?: MediaScanStatus;
}

export function useRepairStatusEvent(onFrame: (frame: RepairStatus) => void): void {
  // video has no status push (its worker has no dashboard orb). the music
  // frame must not land on the video page, so it listens to nothing there and
  // the hero polls instead.
  useShellEvent<RepairStatus>(toolsMedia() === 'video' ? null : REPAIR_STATUS_EVENT, onFrame);
}

export function useRepairProgressEvent(
  onFrame: (frames: Record<string, RepairJobProgress>) => void,
): void {
  // the tools page keys its tree on the media, so this is fixed per mount
  const name = toolsMedia() === 'video' ? VIDEO_REPAIR_PROGRESS_EVENT : REPAIR_PROGRESS_EVENT;
  useShellEvent<Record<string, RepairJobProgress>>(name, onFrame);
}

export function useMediaScanEvent(onFrame: (frame: MediaScanFrame) => void): void {
  useShellEvent<MediaScanFrame>(MEDIA_SCAN_EVENT, onFrame);
}

/**
 * Whether a media-scan frame is a REAL completion.
 *
 * The server emits `scan:media` every two seconds regardless of activity
 * (web_server.py, the status-push loop), and an idle payload is what it sends
 * when nothing has ever run. Treating a bare idle frame as "a scan finished" is
 * what made the vanilla pop "✅ Media scan completed" about two seconds after
 * every single page load, and relabel the card as though one had.
 *
 * A completion is only a completion if the previous frame was actually scanning.
 */
export function isMediaScanCompletion(
  previousStatus: string | null,
  nextStatus: string | null,
): boolean {
  return previousStatus === 'scanning' && nextStatus === 'idle';
}

/**
 * The status key for a frame.
 *
 * NB `is_scanning` is a phantom field: neither `/api/scan/status` nor the
 * `scan:media` emit has ever carried it — both return
 * `web_scan_manager.get_scan_status()`, which reports
 * `status: 'idle' | 'scheduled' | 'scanning'`. The vanilla branched on
 * `status.is_scanning`, so its "Media server scanning…" arm was unreachable and
 * the live progress message never appeared. Key off `status` instead.
 */
export function mediaScanStatusKey(status: MediaScanStatus | null | undefined): string {
  return status?.status || 'unknown';
}
