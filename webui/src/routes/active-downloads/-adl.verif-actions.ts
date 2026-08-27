/**
 * The review-queue actions: play, compare, audit, approve, delete, recover —
 * and their bulk equivalents.
 *
 * Two parallel families on purpose, because the two item types are genuinely
 * different things: an UNVERIFIED row is a file that WAS imported and needs
 * confirming, keyed by library_history id; a QUARANTINED entry was never
 * imported at all and lives only as a file plus a sidecar, keyed by entry id.
 * They hit different endpoints and their confirm copy differs accordingly.
 */

import type { AdlDeletedEntry, AdlDownload, AdlQuarantineEntry } from './-adl.types';

import {
  purgeDeletedFiles,
  quarantineApprove,
  quarantineClear,
  quarantineCompareStream,
  quarantineDelete,
  quarantineEntry,
  quarantinePlay,
  quarantineRecover,
  restoreDeletedFiles,
  verificationApprove,
  verificationCleanOrphans,
  verificationCompareStream,
  verificationDelete,
  verificationEntry,
  verificationPlay,
} from './-adl.api';
import { verificationHistoryId } from './-adl.helpers';
import { ADL_DONE_STATUSES, ADL_REVIEW_STATUSES } from './-adl.types';

const toast = (message: string, type: string) => window.showToast?.(message, type);

/**
 * Play a file through the real media player.
 *
 * The player chrome is filled BEFORE the request so the user sees what is
 * loading; the vanilla used the full player rather than a bare Audio element
 * specifically so a re-render could not wipe playback out.
 */
async function playThroughPlayer(
  info: Record<string, unknown>,
  request: () => Promise<{ success?: boolean; error?: string }>,
): Promise<void> {
  try {
    window.setTrackInfo?.(info);
    window.showLoadingAnimation?.();
    const data = await request();
    if (!data.success) throw new Error(data.error || 'Playback failed');
    await window.startAudioPlayback?.();
  } catch (error) {
    window.hideLoadingAnimation?.();
    toast(`Playback failed: ${(error as Error).message}`, 'error');
  }
}

/**
 * Find a comparison candidate and stream it.
 *
 * Server-side on purpose: the local file's duration guides the ranking, which
 * is what stops a ten-hour YouTube loop winning the match.
 */
async function compareThroughPlayer(
  request: () => Promise<{ success?: boolean; error?: string; result?: unknown }>,
  setBusy?: (busy: boolean) => void,
): Promise<void> {
  setBusy?.(true);
  try {
    const data = await request();
    if (data.success && data.result && typeof window.startStream === 'function') {
      await window.startStream(data.result);
    } else {
      toast(data.error || 'No stream candidate found for comparison', 'error');
    }
  } catch (error) {
    toast(`Stream failed: ${(error as Error).message}`, 'error');
  }
  setBusy?.(false);
}

async function openAudit(
  request: () => Promise<{ success?: boolean; error?: string; entry?: unknown }>,
): Promise<void> {
  try {
    const data = await request();
    if (data.success && data.entry && typeof window.openDownloadAuditModal === 'function') {
      window.openDownloadAuditModal(data.entry);
    } else {
      toast(data.error || 'Audit data not available', 'error');
    }
  } catch {
    toast('Audit load failed', 'error');
  }
}

// ── Unverified (imported, needs confirming) ───────────────────────────────

export function unverifiedPlay(dl: AdlDownload | undefined, historyId: string): Promise<void> {
  return playThroughPlayer(
    {
      title: dl?.title || 'Review track',
      artist: dl?.artist || '',
      album: dl?.album || '',
      // Marks it as a local file so the player shows library chrome.
      is_library: true,
      image_url: dl?.artwork || null,
    },
    () => verificationPlay(historyId),
  );
}

export function unverifiedCompare(historyId: string, setBusy?: (busy: boolean) => void) {
  toast('Searching stream for comparison…', 'info');
  return compareThroughPlayer(() => verificationCompareStream(historyId), setBusy);
}

export function unverifiedAudit(historyId: string) {
  return openAudit(() => verificationEntry(historyId));
}

export async function unverifiedApprove(historyId: string, onDone: () => void): Promise<void> {
  try {
    const data = await verificationApprove(historyId);
    if (data.success) {
      toast('Marked as human-verified 🛡✔', 'success');
      onDone();
    } else toast(data.error || 'Approve failed', 'error');
  } catch {
    toast('Approve failed', 'error');
  }
}

export async function unverifiedDelete(historyId: string, onDone: () => void): Promise<void> {
  const confirmed = await window.showConfirmDialog?.({
    title: 'Delete Unverified File',
    message:
      'This permanently deletes the downloaded file from disk and removes the review entry. Cannot be undone.',
    confirmText: 'Delete',
    cancelText: 'Cancel',
    destructive: true,
  });
  if (!confirmed) return;
  try {
    const data = await verificationDelete(historyId);
    if (data.success) {
      toast('File deleted', 'success');
      onDone();
    } else toast(data.error || 'Delete failed', 'error');
  } catch {
    toast('Delete failed', 'error');
  }
}

/** Every row the bulk actions apply to — the same set the ⚠ filter shows. */
export function reviewableHistoryIds(downloads: AdlDownload[]): string[] {
  return downloads
    .filter(
      (d) =>
        ADL_DONE_STATUSES.includes(d.status) &&
        ADL_REVIEW_STATUSES.includes(String(d.verification_status)),
    )
    .map((d) => verificationHistoryId(d))
    .filter((id): id is string => Boolean(id));
}

export async function approveAllUnverified(ids: string[], onDone: () => void): Promise<void> {
  if (!ids.length) return;
  const confirmed = await window.showConfirmDialog?.({
    title: 'Approve Unverified Files',
    message: `Mark all ${ids.length} unverified entries as human-verified? The AcoustID scanner will skip them from now on.`,
    confirmText: 'Approve All',
    cancelText: 'Cancel',
  });
  if (!confirmed) return;

  let ok = 0;
  for (const id of ids) {
    try {
      const data = await verificationApprove(id);
      if (data.success) ok += 1;
    } catch {
      // Counted as a failure by omission; one bad row must not stop the rest.
    }
  }
  toast(`Approved ${ok}/${ids.length} entries 🛡✔`, ok === ids.length ? 'success' : 'error');
  onDone();
}

export async function deleteAllUnverified(ids: string[], onDone: () => void): Promise<void> {
  if (!ids.length) return;
  const confirmed = await window.showConfirmDialog?.({
    title: 'Delete Unverified Files',
    message: `This permanently deletes all ${ids.length} unverified files from disk and removes their review entries. Cannot be undone.`,
    confirmText: 'Delete All',
    cancelText: 'Cancel',
    destructive: true,
  });
  if (!confirmed) return;

  let ok = 0;
  for (const id of ids) {
    try {
      const data = await verificationDelete(id);
      if (data.success) ok += 1;
    } catch {
      // As above.
    }
  }
  toast(`Deleted ${ok}/${ids.length} files`, ok === ids.length ? 'success' : 'error');
  onDone();
}

/**
 * Drop review rows whose file is gone.
 *
 * Log rows only — it never deletes a file, and the server refuses outright if
 * the whole library looks offline, so a disconnected NAS cannot wipe the queue.
 */
export async function cleanOrphans(onDone: () => void): Promise<void> {
  const confirmed = await window.showConfirmDialog?.({
    title: 'Clean orphaned entries',
    message:
      'Remove review entries whose file no longer exists on disk (deleted, replaced, or re-downloaded elsewhere)? This removes only the stale log rows — it never deletes a file. It checks the filesystem first and refuses if your library looks offline.',
    confirmText: 'Clean up',
    cancelText: 'Cancel',
  });
  if (!confirmed) return;
  try {
    const data = await verificationCleanOrphans();
    if (data.success) {
      const removed = data.removed ?? 0;
      toast(
        `Removed ${removed} orphaned entr${removed === 1 ? 'y' : 'ies'} (checked ${data.checked ?? 0})`,
        'success',
      );
      onDone();
    } else toast(data.error || 'Clean-up failed', 'error');
  } catch {
    toast('Clean-up failed', 'error');
  }
}

// ── Quarantine (never imported) ───────────────────────────────────────────

export function quarantinePlayEntry(entry: AdlQuarantineEntry): Promise<void> {
  return playThroughPlayer(
    {
      // Suffixed so the player cannot be mistaken for the imported copy.
      title: `${entry.expected_track || entry.original_filename || 'Quarantined file'} (quarantined)`,
      artist: entry.expected_artist || '',
      album: '',
      is_library: true,
    },
    () => quarantinePlay(entry.id),
  );
}

export function quarantineCompare(entry: AdlQuarantineEntry, setBusy?: (busy: boolean) => void) {
  toast(`Searching stream for "${entry.expected_track || ''}"…`, 'info');
  return compareThroughPlayer(() => quarantineCompareStream(entry.id), setBusy);
}

export function quarantineAudit(entry: AdlQuarantineEntry) {
  // These files were never imported, so no real history row exists — the
  // backend synthesises a history-shaped entry from the sidecar.
  return openAudit(() => quarantineEntry(entry.id));
}

export async function quarantineApproveEntry(
  entry: AdlQuarantineEntry,
  onDone: () => void,
): Promise<void> {
  try {
    const data = await quarantineApprove(entry.id);
    if (data.success) {
      const removed = data.removed_siblings?.length ?? 0;
      const extra = removed
        ? ` (${removed} duplicate candidate${removed > 1 ? 's' : ''} removed)`
        : '';
      toast(`Approved — re-importing, will be marked human-verified 🛡✔${extra}`, 'success');
      onDone();
    } else toast(data.error || 'Approve failed', 'error');
  } catch {
    toast('Approve failed', 'error');
  }
}

/** The only option for a legacy sidecar with no embedded pipeline context. */
export async function quarantineRecoverEntry(
  entry: AdlQuarantineEntry,
  onDone: () => void,
): Promise<void> {
  try {
    const data = await quarantineRecover(entry.id);
    if (data.success) {
      toast('Moved to Staging — finish via the Import page', 'success');
      onDone();
    } else toast(data.error || 'Recover failed', 'error');
  } catch {
    toast('Recover failed', 'error');
  }
}

export async function quarantineDeleteEntry(
  entry: AdlQuarantineEntry,
  onDone: () => void,
): Promise<void> {
  const confirmed = await window.showConfirmDialog?.({
    title: 'Delete Quarantined File',
    message: 'This permanently removes the file and its metadata sidecar. Cannot be undone.',
    confirmText: 'Delete',
    cancelText: 'Cancel',
    destructive: true,
  });
  if (!confirmed) return;
  try {
    const data = await quarantineDelete(entry.id);
    if (data.success) {
      toast('Quarantined file deleted', 'success');
      onDone();
    } else toast(data.error || 'Delete failed', 'error');
  } catch {
    toast('Delete failed', 'error');
  }
}

/** Stagger between bulk quarantine approvals — each spawns a re-import thread. */
export const QUARANTINE_APPROVE_STAGGER_MS = 500;

export async function approveAllQuarantine(
  entries: AdlQuarantineEntry[],
  onDone: () => void,
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
): Promise<void> {
  // Legacy sidecars cannot be one-click approved; say so rather than
  // silently doing nothing.
  const approvable = entries.filter((entry) => entry.has_full_context);
  if (!approvable.length) {
    toast('No one-click-approvable entries (legacy sidecars need Recover)', 'info');
    return;
  }
  const confirmed = await window.showConfirmDialog?.({
    title: 'Approve Quarantined Files',
    message: `Approve and re-import all ${approvable.length} quarantined files? They will be imported into the library marked human-verified.`,
    confirmText: 'Approve & Import All',
    cancelText: 'Cancel',
  });
  if (!confirmed) return;

  let ok = 0;
  for (const entry of approvable) {
    try {
      const data = await quarantineApprove(entry.id);
      if (data.success) ok += 1;
    } catch {
      // Keep going; the toast reports the tally.
    }
    // Each approve spawns a server-side re-import thread — stagger them so a
    // bulk approve does not start fifty at once.
    await sleep(QUARANTINE_APPROVE_STAGGER_MS);
  }
  toast(
    `Approved ${ok}/${approvable.length} — re-importing as human-verified`,
    ok === approvable.length ? 'success' : 'error',
  );
  onDone();
}

export async function clearAllQuarantine(
  entries: AdlQuarantineEntry[],
  onDone: () => void,
): Promise<void> {
  if (!entries.length) return;
  const confirmed = await window.showConfirmDialog?.({
    title: 'Clear Quarantine',
    message: `This permanently deletes all ${entries.length} quarantined files and their metadata sidecars. Cannot be undone.`,
    confirmText: 'Delete All',
    cancelText: 'Cancel',
    destructive: true,
  });
  if (!confirmed) return;
  try {
    const data = await quarantineClear();
    if (data.success) toast('Quarantine cleared', 'success');
    else toast(data.error || 'Clear failed', 'error');
  } catch {
    toast('Clear failed', 'error');
  }
  onDone();
}

/* ── The deleted-files (recycle bin) actions ─────────────────────────────── */

/**
 * Third family: a DELETED entry was in the library once and got removed by a
 * tool. It has no sidecar and no history row - just the file, its manifest
 * record, and two verbs.
 */

function reportDeletedResult(
  data: { restored?: string[]; purged?: string[]; errors?: { id: string; error: string }[] },
  verb: string,
): void {
  const done = (data.restored ?? data.purged ?? []).length;
  const errors = data.errors ?? [];
  if (done && !errors.length) toast(`${verb} ${done} file${done === 1 ? '' : 's'}`, 'success');
  else if (done) toast(`${verb} ${done}, ${errors.length} failed: ${errors[0].error}`, 'error');
  else if (errors.length) toast(errors[0].error, 'error');
}

export async function restoreDeletedEntry(entry: AdlDeletedEntry, onDone: () => void) {
  try {
    reportDeletedResult(await restoreDeletedFiles([entry.id]), 'Restored');
  } catch {
    toast('Restore failed', 'error');
  }
  onDone();
}

export async function purgeDeletedEntry(entry: AdlDeletedEntry, onDone: () => void) {
  const confirmed = await window.showConfirmDialog?.({
    title: 'Delete Permanently',
    message: `This permanently deletes "${entry.name}". It cannot be restored afterwards.`,
    confirmText: 'Delete',
    cancelText: 'Cancel',
    destructive: true,
  });
  if (!confirmed) {
    onDone();
    return;
  }
  try {
    reportDeletedResult(await purgeDeletedFiles([entry.id]), 'Deleted');
  } catch {
    toast('Delete failed', 'error');
  }
  onDone();
}

export async function restoreAllDeleted(entries: AdlDeletedEntry[], onDone: () => void) {
  if (!entries.length) return;
  const confirmed = await window.showConfirmDialog?.({
    title: 'Restore All',
    message: `Restore all ${entries.length} files to where they were removed from? Files whose original path is occupied are skipped.`,
    confirmText: 'Restore All',
    cancelText: 'Cancel',
  });
  if (!confirmed) return;
  try {
    reportDeletedResult(await restoreDeletedFiles(entries.map((e) => e.id)), 'Restored');
  } catch {
    toast('Restore failed', 'error');
  }
  onDone();
}

export async function emptyDeletedBin(count: number, onDone: () => void) {
  if (!count) return;
  const confirmed = await window.showConfirmDialog?.({
    title: 'Empty Bin',
    message: `This permanently deletes all ${count} files in the bin. Cannot be undone.`,
    confirmText: 'Empty Bin',
    cancelText: 'Cancel',
    destructive: true,
  });
  if (!confirmed) return;
  try {
    reportDeletedResult(await purgeDeletedFiles(null, true), 'Deleted');
  } catch {
    toast('Empty bin failed', 'error');
  }
  onDone();
}
