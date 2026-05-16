import { create } from 'zustand';
import { combine } from 'zustand/middleware';
import { useShallow } from 'zustand/react/shallow';

import type {
  ImportAlbumMatchPayload,
  ImportAlbumResult,
  ImportQueueEntry,
  ImportQueueJob,
  ImportTrackResult,
} from './-import.types';

export type SingleSearchState = {
  query: string;
  loading: boolean;
  error: string | null;
  results: ImportTrackResult[];
};

type StateUpdater<T> = T | ((current: T) => T);

function resolveState<T>(current: T, updater: StateUpdater<T>) {
  return typeof updater === 'function' ? (updater as (value: T) => T)(current) : updater;
}

function createInitialWorkflowState() {
  return {
    queue: [] as ImportQueueEntry[],
    nextQueueId: 0,
    albumQuery: '',
    albumResults: null as ImportAlbumResult[] | null,
    albumSearchError: null as string | null,
    albumSearchLoading: false,
    autoGroupFilePaths: null as string[] | null,
    selectedAlbum: null as ImportAlbumResult | null,
    albumMatch: null as ImportAlbumMatchPayload | null,
    albumMatchError: null as string | null,
    albumMatchLoading: false,
    matchOverrides: {} as Record<number, number>,
    selectedSingles: new Set<number>(),
    singlesManualMatches: {} as Record<number, ImportTrackResult>,
    openSingleSearch: null as number | null,
    singleSearches: {} as Record<number, SingleSearchState>,
  };
}

export const useImportWorkflowStore = create(
  combine(createInitialWorkflowState(), (set, get) => ({
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
    resetAlbumSearch: () => {
      set({
        albumQuery: '',
        albumResults: null,
        albumSearchError: null,
        albumSearchLoading: false,
        autoGroupFilePaths: null,
        selectedAlbum: null,
        albumMatch: null,
        albumMatchError: null,
        albumMatchLoading: false,
        matchOverrides: {},
      });
    },
    setAlbumQuery: (albumQuery: string) => set({ albumQuery }),
    setAlbumResults: (albumResults: ImportAlbumResult[] | null) => set({ albumResults }),
    setAlbumSearchError: (albumSearchError: string | null) => set({ albumSearchError }),
    setAlbumSearchLoading: (albumSearchLoading: boolean) => set({ albumSearchLoading }),
    setAlbumSearchContext: (albumQuery: string, autoGroupFilePaths: string[] | null) => {
      set({
        albumQuery,
        albumSearchLoading: true,
        albumSearchError: null,
        albumResults: null,
        selectedAlbum: null,
        albumMatch: null,
        autoGroupFilePaths,
      });
    },
    setSelectedAlbum: (selectedAlbum: ImportAlbumResult | null) => set({ selectedAlbum }),
    setAlbumMatch: (albumMatch: ImportAlbumMatchPayload | null) => set({ albumMatch }),
    setAlbumMatchError: (albumMatchError: string | null) => set({ albumMatchError }),
    setAlbumMatchLoading: (albumMatchLoading: boolean) => set({ albumMatchLoading }),
    clearAutoGroupFilePaths: () => set({ autoGroupFilePaths: null }),
    setMatchOverrides: (updater: StateUpdater<Record<number, number>>) => {
      set((state) => ({ matchOverrides: resolveState(state.matchOverrides, updater) }));
    },
    toggleSingle: (index: number) => {
      set((state) => {
        const selectedSingles = new Set(state.selectedSingles);
        if (selectedSingles.has(index)) selectedSingles.delete(index);
        else selectedSingles.add(index);
        return { selectedSingles };
      });
    },
    toggleAllSingles: (fileCount: number) => {
      set((state) => ({
        selectedSingles:
          state.selectedSingles.size === fileCount
            ? new Set<number>()
            : new Set(Array.from({ length: fileCount }, (_, index) => index)),
      }));
    },
    clearSinglesSelection: () => {
      set({
        selectedSingles: new Set<number>(),
        singlesManualMatches: {},
      });
    },
    setOpenSingleSearch: (openSingleSearch: number | null) => set({ openSingleSearch }),
    ensureSingleSearch: (index: number, query: string) => {
      set((state) => ({
        singleSearches: {
          ...state.singleSearches,
          [index]: state.singleSearches[index] ?? {
            query,
            loading: false,
            error: null,
            results: [],
          },
        },
      }));
    },
    setSingleSearch: (index: number, updater: StateUpdater<SingleSearchState>) => {
      set((state) => {
        const current = state.singleSearches[index] ?? {
          query: '',
          loading: false,
          error: null,
          results: [],
        };
        return {
          singleSearches: {
            ...state.singleSearches,
            [index]: resolveState(current, updater),
          },
        };
      });
    },
    selectSingleMatch: (fileIndex: number, track: ImportTrackResult) => {
      set((state) => ({
        singlesManualMatches: { ...state.singlesManualMatches, [fileIndex]: track },
        selectedSingles: new Set(state.selectedSingles).add(fileIndex),
        openSingleSearch: null,
      }));
    },
  })),
);

export function resetImportWorkflowStore() {
  useImportWorkflowStore.setState(createInitialWorkflowState());
}

export function useImportQueueWorkflow() {
  return useImportWorkflowStore(
    useShallow((state) => ({
      clearFinishedJobs: state.clearFinishedJobs,
      enqueueQueueJob: state.enqueueQueueJob,
      queue: state.queue,
      updateQueueEntry: state.updateQueueEntry,
    })),
  );
}

export function useAlbumImportWorkflow() {
  return useImportWorkflowStore(
    useShallow((state) => ({
      albumMatch: state.albumMatch,
      albumMatchError: state.albumMatchError,
      albumMatchLoading: state.albumMatchLoading,
      albumQuery: state.albumQuery,
      albumResults: state.albumResults,
      albumSearchError: state.albumSearchError,
      albumSearchLoading: state.albumSearchLoading,
      autoGroupFilePaths: state.autoGroupFilePaths,
      clearAutoGroupFilePaths: state.clearAutoGroupFilePaths,
      matchOverrides: state.matchOverrides,
      resetAlbumWorkflow: state.resetAlbumSearch,
      selectedAlbum: state.selectedAlbum,
      setAlbumMatch: state.setAlbumMatch,
      setAlbumMatchError: state.setAlbumMatchError,
      setAlbumMatchLoading: state.setAlbumMatchLoading,
      setAlbumQuery: state.setAlbumQuery,
      setAlbumResults: state.setAlbumResults,
      setAlbumSearchContext: state.setAlbumSearchContext,
      setAlbumSearchError: state.setAlbumSearchError,
      setAlbumSearchLoading: state.setAlbumSearchLoading,
      setMatchOverrides: state.setMatchOverrides,
      setSelectedAlbum: state.setSelectedAlbum,
    })),
  );
}

export function useSinglesImportWorkflow() {
  return useImportWorkflowStore(
    useShallow((state) => ({
      clearSinglesSelection: state.clearSinglesSelection,
      ensureSingleSearch: state.ensureSingleSearch,
      openSingleSearch: state.openSingleSearch,
      selectedSingles: state.selectedSingles,
      selectSingleMatchInStore: state.selectSingleMatch,
      setOpenSingleSearch: state.setOpenSingleSearch,
      setSingleSearch: state.setSingleSearch,
      singleSearches: state.singleSearches,
      singlesManualMatches: state.singlesManualMatches,
      toggleAllSingles: state.toggleAllSingles,
      toggleSingleInStore: state.toggleSingle,
    })),
  );
}
