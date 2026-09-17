import { useQueryClient } from '@tanstack/react-query';
import { HTTPError } from 'ky';
import { useState } from 'react';

import type { ImportQueueJob } from '../-import.types';

import {
  invalidateImportStagingQueries,
  processImportAlbumTrack,
  processImportSingleFile,
  resolveAutoImportResult,
} from '../-import.api';
import { getTrackDisplayInfo, IMPORT_PLACEHOLDER_IMAGE } from '../-import.helpers';
import { useImportQueueWorkflow, useImportWorkflowStore } from '../-import.store';

/** The header's Refresh: drop finished jobs and re-read the import folder. */
export function useInboxRefresh() {
  const queryClient = useQueryClient();
  const clearFinishedJobs = useImportWorkflowStore((state) => state.clearFinishedJobs);
  const [refreshing, setRefreshing] = useState(false);
  return {
    refreshing,
    refresh: async () => {
      setRefreshing(true);
      try {
        clearFinishedJobs();
        await invalidateImportStagingQueries(queryClient);
      } finally {
        setRefreshing(false);
      }
    },
  };
}

/**
 * Runs a matcher job: one request per track, progress into the store, and
 * the inbox re-read when it is done. A job that came from an inbox item
 * with a history row records itself against that row, so history says
 * "imported by hand" instead of a stale "needs identification".
 */
export function useImportQueueActions() {
  const queryClient = useQueryClient();
  const { enqueueQueueJob, updateQueueEntry } = useImportQueueWorkflow();

  const runQueueJob = async (entryId: number, job: ImportQueueJob) => {
    let processed = 0;
    const errors: string[] = [];

    for (let index = 0; index < job.items.length; index += 1) {
      const itemName =
        job.type === 'album'
          ? getTrackDisplayInfo(job.items[index], index).name
          : job.items[index].title || job.items[index].filename || `File ${index + 1}`;

      updateQueueEntry(entryId, {
        sublabel: `Importing ${index + 1}/${job.items.length}: ${itemName}`,
        processed,
        errors: [...errors],
      });

      try {
        const payload =
          job.type === 'album'
            ? await processImportAlbumTrack({
                album: job.albumData,
                match: job.items[index],
              })
            : await processImportSingleFile(job.items[index]);

        processed += payload.processed || 0;
        if (payload.errors?.length) {
          errors.push(...payload.errors);
        }
      } catch (error) {
        if (isMediaServerNotConnectedError(error)) {
          // The whole batch would fail the same gate check on every remaining item —
          // stop instead of repeating the same error once per file.
          updateQueueEntry(entryId, {
            status: 'error',
            processed,
            errors: [getErrorMessage(error)],
            blockedByMediaServer: true,
          });
          void invalidateImportStagingQueries(queryClient);
          return;
        }
        errors.push(`${itemName}: ${getErrorMessage(error)}`);
      }

      updateQueueEntry(entryId, {
        processed,
        errors: [...errors],
      });
    }

    if (processed > 0 && job.historyId != null) {
      try {
        await resolveAutoImportResult(job.historyId);
      } catch {
        // history bookkeeping only; the files are already in the library
      }
    }

    updateQueueEntry(entryId, {
      status: errors.length > 0 && processed === 0 ? 'error' : 'done',
      sublabel:
        errors.length > 0 && processed === 0
          ? 'Nothing imported'
          : `${processed} of ${job.items.length} imported`,
      processed,
      errors,
    });
    void invalidateImportStagingQueries(queryClient);
  };

  return {
    addQueueJob: (job: ImportQueueJob) => {
      const id = enqueueQueueJob(job);
      void runQueueJob(id, job);
    },
  };
}

export function RefreshIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M13.65 2.35A8 8 0 1 0 16 8h-2a6 6 0 1 1-1.76-4.24L10 6h6V0l-2.35 2.35z" />
    </svg>
  );
}

export function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  );
}

export function FolderIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}

export function DiscIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="2.5" />
    </svg>
  );
}

export function NoteIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M9 18V6l10-2v12" />
      <circle cx="6.5" cy="18" r="2.5" />
      <circle cx="16.5" cy="16" r="2.5" />
    </svg>
  );
}

export function fallbackImage(event: { currentTarget: HTMLImageElement }) {
  if (event.currentTarget.src.endsWith(IMPORT_PLACEHOLDER_IMAGE)) return;
  event.currentTarget.src = IMPORT_PLACEHOLDER_IMAGE;
}

export function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error';
}

export function isMediaServerNotConnectedError(error: unknown): boolean {
  if (!(error instanceof HTTPError)) return false;
  const data = error.data;
  return Boolean(
    data &&
    typeof data === 'object' &&
    (data as { error_code?: unknown }).error_code === 'media_server_not_connected',
  );
}

export async function confirmAction({
  title,
  message,
  confirmText,
}: {
  title: string;
  message: string;
  confirmText: string;
}): Promise<boolean> {
  if (window.showConfirmDialog) {
    return await window.showConfirmDialog({ title, message, confirmText });
  }
  return true;
}
