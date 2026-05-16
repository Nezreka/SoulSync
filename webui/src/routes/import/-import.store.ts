import { create } from 'zustand';
import { combine } from 'zustand/middleware';
import { useShallow } from 'zustand/react/shallow';

import type {
  ImportAlbumMatchPayload,
  ImportAlbumResult,
  ImportQueueEntry,
  ImportQueueJob,
  ImportStagingFile,
  ImportTrackResult,
} from './-import.types';
import { getStagingFileKey } from './-import.helpers';

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
    selectedSingles: new Set<string>(),
    singlesManualMatches: {} as Record<string, ImportTrackResult>,
    openSingleSearch: null as string | null,
    singleSearches: {} as Record<string, SingleSearchState>,
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
    toggleSingle: (fileKey: string) => {
      set((state) => {
        const selectedSingles = new Set(state.selectedSingles);
        if (selectedSingles.has(fileKey)) selectedSingles.delete(fileKey);
        else selectedSingles.add(fileKey);
        return { selectedSingles };
      });
    },
    toggleAllSingles: (stagingFiles: ImportStagingFile[]) => {
      set((state) => ({
        selectedSingles: (() => {
          const fileKeys = stagingFiles.map(getStagingFileKey);
          return state.selectedSingles.size === fileKeys.length &&
            fileKeys.every((key) => state.selectedSingles.has(key))
            ? new Set<string>()
            : new Set(fileKeys);
        })(),
      }));
    },
    clearSinglesSelection: () => {
      set({
        selectedSingles: new Set<string>(),
        singlesManualMatches: {},
      });
    },
    syncSinglesWorkflow: (stagingFiles: ImportStagingFile[]) => {
      const validKeys = new Set(stagingFiles.map(getStagingFileKey));
      set((state) => ({
        selectedSingles: new Set([...state.selectedSingles].filter((key) => validKeys.has(key))),
        singlesManualMatches: Object.fromEntries(
          Object.entries(state.singlesManualMatches).filter(([key]) => validKeys.has(key)),
        ),
        openSingleSearch:
          state.openSingleSearch && validKeys.has(state.openSingleSearch)
            ? state.openSingleSearch
            : null,
        singleSearches: Object.fromEntries(
          Object.entries(state.singleSearches).filter(([key]) => validKeys.has(key)),
        ),
      }));
    },
    setOpenSingleSearch: (openSingleSearch: string | null) => set({ openSingleSearch }),
    ensureSingleSearch: (fileKey: string, query: string) => {
      set((state) => ({
        singleSearches: {
          ...state.singleSearches,
          [fileKey]: state.singleSearches[fileKey] ?? {
            query,
            loading: false,
            error: null,
            results: [],
          },
        },
      }));
    },
    setSingleSearch: (fileKey: string, updater: StateUpdater<SingleSearchState>) => {
      set((state) => {
        const current = state.singleSearches[fileKey] ?? {
          query: '',
          loading: false,
          error: null,
          results: [],
        };
        return {
          singleSearches: {
            ...state.singleSearches,
            [fileKey]: resolveState(current, updater),
          },
        };
      });
    },
    selectSingleMatch: (fileKey: string, track: ImportTrackResult) => {
      set((state) => ({
        singlesManualMatches: { ...state.singlesManualMatches, [fileKey]: track },
        selectedSingles: new Set(state.selectedSingles).add(fileKey),
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
      syncSinglesWorkflow: state.syncSinglesWorkflow,
      toggleAllSingles: state.toggleAllSingles,
      toggleSingleInStore: state.toggleSingle,
    })),
  );
}
