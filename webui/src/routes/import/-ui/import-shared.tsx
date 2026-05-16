import { useQuery, useQueryClient } from '@tanstack/react-query';

import type { ImportQueueJob } from '../-import.types';

import {
  importStagingFilesQueryOptions,
  invalidateImportStagingQueries,
  processImportAlbumTrack,
  processImportSingleFile,
} from '../-import.api';
import { getTrackDisplayInfo, IMPORT_PLACEHOLDER_IMAGE } from '../-import.helpers';
import { useImportQueueWorkflow, useImportWorkflowStore } from '../-import.store';

export function useImportStaging() {
  const queryClient = useQueryClient();
  const clearFinishedJobs = useImportWorkflowStore((state) => state.clearFinishedJobs);
  const stagingQuery = useQuery({
    ...importStagingFilesQueryOptions(),
  });

  return {
    refreshStaging: async () => {
      clearFinishedJobs();
      await invalidateImportStagingQueries(queryClient);
    },
    stagingFiles: stagingQuery.data?.files ?? [],
    stagingPath: stagingQuery.data?.staging_path || 'Not configured',
    stagingQuery,
  };
}

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
        sublabel: `Processing ${index + 1}/${job.items.length}: ${itemName}`,
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
        errors.push(`${itemName}: ${getErrorMessage(error)}`);
      }

      updateQueueEntry(entryId, {
        processed,
        errors: [...errors],
      });
    }

    updateQueueEntry(entryId, {
      status: errors.length > 0 && processed === 0 ? 'error' : 'done',
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

export function fallbackImage(event: { currentTarget: HTMLImageElement }) {
  if (event.currentTarget.src.endsWith(IMPORT_PLACEHOLDER_IMAGE)) return;
  event.currentTarget.src = IMPORT_PLACEHOLDER_IMAGE;
}

export function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error';
}
