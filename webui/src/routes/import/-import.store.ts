import { create } from 'zustand';
import { combine } from 'zustand/middleware';
import { useShallow } from 'zustand/shallow';

import type { ImportQueueEntry, ImportQueueJob } from './-import.types';

/**
 * The imports this page is running itself (the matcher's "Import N tracks").
 * Kept across route changes so leaving the matcher for the inbox does not
 * lose the progress bar. The worker's own imports are not here; they come
 * through the inbox rows.
 */
function createInitialState() {
  return {
    queue: [] as ImportQueueEntry[],
    nextQueueId: 0,
  };
}

export const useImportWorkflowStore = create(
  combine(createInitialState(), (set, get) => ({
    clearFinishedJobs: () => {
      set((state) => ({ queue: state.queue.filter((entry) => entry.status === 'running') }));
    },
    enqueueQueueJob: (job: ImportQueueJob) => {
      const id = get().nextQueueId + 1;
      const entry: ImportQueueEntry = {
        id,
        type: job.type,
        label: job.label,
        sublabel: job.sublabel,
        imageUrl: job.imageUrl,
        status: 'running',
        processed: 0,
        total: job.items.length,
        errors: [],
      };
      set((state) => ({
        nextQueueId: id,
        queue: [...state.queue, entry],
      }));
      return id;
    },
    updateQueueEntry: (entryId: number, patch: Partial<ImportQueueEntry>) => {
      set((state) => ({
        queue: state.queue.map((entry) => (entry.id === entryId ? { ...entry, ...patch } : entry)),
      }));
    },
  })),
);

export function resetImportWorkflowStore() {
  useImportWorkflowStore.setState(createInitialState());
}

export function useImportQueueWorkflow() {
  return useImportWorkflowStore(
    useShallow((state) => ({
      queue: state.queue,
      clearFinishedJobs: state.clearFinishedJobs,
      enqueueQueueJob: state.enqueueQueueJob,
      updateQueueEntry: state.updateQueueEntry,
    })),
  );
}
