import { useCallback, useEffect, useRef, useState } from 'react';

import type { YourAlbumsResponse } from './-discover.types';
import type { BatchProgressState, BatchRow, YourAlbum } from './-discover.your-albums-actions';

import {
  fetchYourAlbums,
  fetchYourAlbumsSources,
  refreshYourAlbums,
  saveDiscoverSettings,
} from './-discover.api';
import {
  YOUR_ALBUMS_DEFAULT_SORT,
  YOUR_ALBUMS_DEFAULT_STATUS,
  yourAlbumsSubtitle,
} from './-discover.your-albums';
import {
  BATCH_ENDPOINT,
  BATCH_NO_SOURCES,
  batchRequestBody,
  batchSummary,
  initialBatchProgress,
  missingAlbumsOutcome,
  prepareBatchRows,
  reduceBatchEvent,
  selectedBatchRows,
  SOURCES_NONE_SELECTED,
  SOURCES_SAVE_FAILED,
  SOURCES_SAVED,
  splitNdjson,
  toggleSource,
  YOUR_ALBUMS_DEFAULT_SOURCES,
  YOUR_ALBUMS_REFRESH_POLL_MS,
  YOUR_ALBUMS_REFRESH_TIMEOUT_MS,
  YOUR_ALBUMS_SEARCH_DEBOUNCE_MS,
  enabledSources,
  initialSourcesState,
  sourcesSavePayload,
} from './-discover.your-albums-actions';

/**
 * The Your Albums interactions — the filterable grid, refresh, the sources
 * modal, and the missing-albums batch flow.
 *
 * Transcribed from discover.js 1349-1470 (controller + grid reload), 1407-1424
 * (the stale poller), 1579-1603 (refresh), 1605-1663 (sources) and 1730-2030
 * (download-missing → batch modal → ndjson stream), over the module that owns
 * every rule.
 *
 * The vanilla runs the section through TWO fetch paths — the section
 * controller for first load and `loadYourAlbumsGrid` for every filter change.
 * They ask the same endpoint with the same params, so the hook has ONE.
 * Its stale branch is the vanilla's `isStale`: a stale payload with zero
 * total shows the "fetching from connected services" state and polls at 5s
 * up to 12 attempts (1407-1424).
 */

export type AlbumsToast = { message: string; level: 'success' | 'error' | 'warning' | 'info' };

const STALE_POLL_MS = 5000;
const STALE_POLL_MAX = 12;

export interface GridState {
  page: number;
  search: string;
  status: string;
  sort: string;
}

export const INITIAL_GRID: GridState = {
  page: 1,
  search: '',
  status: YOUR_ALBUMS_DEFAULT_STATUS,
  sort: YOUR_ALBUMS_DEFAULT_SORT,
};

export interface YourAlbumsController {
  grid: {
    state: GridState;
    albums: YourAlbum[];
    total: number;
    subtitle: string;
    /** `stale` is the fetching-from-services state, with its own poller. */
    phase: 'loading' | 'error' | 'ready' | 'stale';
    /** stats.missing > 0 — shows the Download button (1385). */
    canDownloadMissing: boolean;
    /** total === 0 && !stale — the vanilla hides the whole section (1360). */
    hidden: boolean;
    filter: (change: Partial<Omit<GridState, 'page'>>) => void;
    page: (page: number) => void;
  };
  refresh: { refreshing: boolean; start: () => Promise<void> };
  sources: {
    open: boolean;
    state: Record<string, boolean>;
    connected: string[];
    openModal: () => void;
    closeModal: () => void;
    toggle: (id: string) => void;
    save: () => Promise<void>;
    savedEnabled: string[] | null;
  };
  batch: {
    open: boolean;
    rows: BatchRow[];
    selected: number[];
    phase: 'select' | 'running' | 'done';
    progress: BatchProgressState | null;
    openForMissing: () => Promise<void>;
    close: () => void;
    toggleRow: (index: number, checked: boolean) => void;
    selectAll: (select: boolean) => void;
    submit: () => Promise<void>;
  };
}

export function useYourAlbums(onToast: (toast: AlbumsToast) => void): YourAlbumsController {
  const toastRef = useRef(onToast);
  toastRef.current = onToast;

  // ── The grid ──────────────────────────────────────────────────────────
  const [gridState, setGridState] = useState<GridState>(INITIAL_GRID);
  const [albums, setAlbums] = useState<YourAlbum[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<{ total?: number; owned?: number; missing?: number }>({});
  const [phase, setPhase] = useState<'loading' | 'error' | 'ready' | 'stale'>('loading');
  const gen = useRef(0);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stalePoll = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback((state: GridState) => {
    gen.current += 1;
    const g = gen.current;
    setPhase('loading');
    void (async () => {
      try {
        const data: YourAlbumsResponse = await fetchYourAlbums({
          page: state.page,
          search: state.search || undefined,
          status: state.status as 'all' | 'missing' | 'owned',
          sort: state.sort,
        });
        if (gen.current !== g) return;
        if (!data.success) throw new Error();
        const s = (data.stats ?? {}) as { total?: number };
        setAlbums((data.albums as YourAlbum[]) ?? []);
        setTotal(data.total ?? 0);
        setStats(data.stats ?? {});
        // isStale (1367): a stale payload with zero total is still building.
        if (data.stale && (s.total ?? 0) === 0) {
          setPhase('stale');
          startStalePoll();
        } else {
          setPhase('ready');
        }
      } catch {
        if (gen.current !== g) return;
        setPhase('error');
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startStalePoll = useCallback(() => {
    if (stalePoll.current) clearInterval(stalePoll.current);
    let attempts = 0;
    stalePoll.current = setInterval(() => {
      void (async () => {
        attempts += 1;
        if (attempts > STALE_POLL_MAX) {
          if (stalePoll.current) clearInterval(stalePoll.current);
          return;
        }
        try {
          const data = await fetchYourAlbums({});
          const t = ((data.stats ?? {}) as { total?: number }).total ?? 0;
          if (data.success && t > 0) {
            if (stalePoll.current) clearInterval(stalePoll.current);
            load(INITIAL_GRID);
          }
        } catch {
          /* keep polling (1422) */
        }
      })();
    }, STALE_POLL_MS);
  }, [load]);

  useEffect(() => {
    load(INITIAL_GRID);
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
      if (stalePoll.current) clearInterval(stalePoll.current);
    };
  }, [load]);

  const filter = useCallback(
    (change: Partial<Omit<GridState, 'page'>>) => {
      // Every filter change restarts at page 1 (the vanilla's handlers reset
      // yourAlbumsPage before reloading).
      const next = { ...gridState, ...change, page: 1 };
      setGridState(next);
      if (searchTimer.current) clearTimeout(searchTimer.current);
      if ('search' in change) {
        searchTimer.current = setTimeout(() => load(next), YOUR_ALBUMS_SEARCH_DEBOUNCE_MS);
      } else {
        load(next);
      }
    },
    [gridState, load],
  );

  const page = useCallback(
    (p: number) => {
      const next = { ...gridState, page: p };
      setGridState(next);
      load(next);
    },
    [gridState, load],
  );

  // ── Refresh (1579-1603) ───────────────────────────────────────────────
  const [refreshing, setRefreshing] = useState(false);
  const refreshPoll = useRef<ReturnType<typeof setInterval> | null>(null);
  const refreshStop = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (refreshPoll.current) clearInterval(refreshPoll.current);
      if (refreshStop.current) clearTimeout(refreshStop.current);
    },
    [],
  );

  const startRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await refreshYourAlbums();
    } catch {
      toastRef.current({ message: 'Failed to start refresh', level: 'error' });
      setRefreshing(false);
      return;
    }
    toastRef.current({ message: 'Refresh started — checking for new albums...', level: 'info' });
    if (refreshPoll.current) clearInterval(refreshPoll.current);
    refreshPoll.current = setInterval(() => {
      void (async () => {
        try {
          const data = await fetchYourAlbums({});
          const t = ((data.stats ?? {}) as { total?: number }).total ?? 0;
          if (data.success && t > 0) {
            if (refreshPoll.current) clearInterval(refreshPoll.current);
            if (refreshStop.current) clearTimeout(refreshStop.current);
            setRefreshing(false);
            load(INITIAL_GRID);
          }
        } catch {
          /* swallowed, like the vanilla's poll (1597) */
        }
      })();
    }, YOUR_ALBUMS_REFRESH_POLL_MS);
    // The hard stop re-enables the button either way (1598).
    if (refreshStop.current) clearTimeout(refreshStop.current);
    refreshStop.current = setTimeout(() => {
      if (refreshPoll.current) clearInterval(refreshPoll.current);
      setRefreshing(false);
    }, YOUR_ALBUMS_REFRESH_TIMEOUT_MS);
  }, [load]);

  // ── Sources modal (1605-1663) ─────────────────────────────────────────
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [sourcesState, setSourcesState] = useState<Record<string, boolean>>({});
  const [connected, setConnected] = useState<string[]>([]);
  const [savedEnabled, setSavedEnabled] = useState<string[] | null>(null);

  const openSources = useCallback(() => {
    setSourcesOpen(true);
    void (async () => {
      let enabled: string[] = [...YOUR_ALBUMS_DEFAULT_SOURCES];
      let conn: string[] = [];
      try {
        const data = await fetchYourAlbumsSources();
        if (data.enabled) enabled = data.enabled;
        if (data.connected) conn = data.connected;
      } catch {
        /* defaults stand (1611-1616) */
      }
      setSourcesState(initialSourcesState(enabled));
      setConnected(conn);
    })();
  }, []);

  const toggle = useCallback(
    (id: string) => {
      setSourcesState((prev) => {
        const { state, hint } = toggleSource(prev, id, connected);
        if (hint) toastRef.current({ message: hint, level: 'warning' });
        return state;
      });
    },
    [connected],
  );

  const saveSources = useCallback(async () => {
    const enabled = enabledSources(sourcesState);
    if (enabled.length === 0) {
      toastRef.current({ message: SOURCES_NONE_SELECTED, level: 'error' });
      return;
    }
    try {
      await saveDiscoverSettings(sourcesSavePayload(enabled));
      setSourcesOpen(false);
      setSavedEnabled(enabled);
      toastRef.current({ message: SOURCES_SAVED, level: 'success' });
    } catch {
      toastRef.current({ message: SOURCES_SAVE_FAILED, level: 'error' });
    }
  }, [sourcesState]);

  // ── The missing-albums batch flow (1730-2030) ─────────────────────────
  const [batchOpen, setBatchOpen] = useState(false);
  const [rows, setRows] = useState<BatchRow[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [batchPhase, setBatchPhase] = useState<'select' | 'running' | 'done'>('select');
  const [progress, setProgress] = useState<BatchProgressState | null>(null);

  const openForMissing = useCallback(async () => {
    let missing: YourAlbum[];
    try {
      const data = await fetchYourAlbums({ page: 1, per_page: 1000, status: 'missing' });
      const outcome = missingAlbumsOutcome(data as Parameters<typeof missingAlbumsOutcome>[0]);
      if (outcome.kind !== 'open') {
        toastRef.current({ message: outcome.message, level: outcome.toast });
        return;
      }
      missing = outcome.missing;
    } catch (e) {
      toastRef.current({ message: `Error: ${(e as Error).message}`, level: 'error' });
      return;
    }
    const prepared = prepareBatchRows(missing);
    if (prepared.length === 0) {
      toastRef.current({ message: BATCH_NO_SOURCES, level: 'warning' });
      return;
    }
    setRows(prepared);
    // Every checkbox renders checked (1849).
    setSelected(prepared.map((r) => r._index));
    setBatchPhase('select');
    setProgress(null);
    setBatchOpen(true);
  }, []);

  const submitBatch = useCallback(async () => {
    const chosen = selectedBatchRows(rows, selected);
    if (chosen.length === 0) return;
    setBatchPhase('running');
    let state = initialBatchProgress(chosen);
    setProgress(state);
    try {
      const res = await fetch(BATCH_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(batchRequestBody(chosen)),
      });
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { lines, rest } = splitNdjson(buffer);
        buffer = rest;
        for (const line of lines) {
          try {
            state = reduceBatchEvent(state, JSON.parse(line), chosen);
            setProgress(state);
          } catch {
            /* a broken line never aborts the stream (2013) */
          }
        }
      }
      const summary = batchSummary(state);
      toastRef.current({ message: summary.toast, level: summary.toastLevel });
      setBatchPhase('done');
    } catch (e) {
      toastRef.current({ message: `Error: ${(e as Error).message}`, level: 'error' });
      setBatchPhase('done');
    }
  }, [rows, selected]);

  return {
    grid: {
      state: gridState,
      albums,
      total,
      subtitle: yourAlbumsSubtitle(stats) ?? '',
      phase,
      canDownloadMissing: (stats.missing ?? 0) > 0,
      hidden: phase === 'ready' && (stats.total ?? 0) === 0,
      filter,
      page,
    },
    refresh: { refreshing, start: startRefresh },
    sources: {
      open: sourcesOpen,
      state: sourcesState,
      connected,
      openModal: openSources,
      closeModal: () => setSourcesOpen(false),
      toggle,
      save: saveSources,
      savedEnabled,
    },
    batch: {
      open: batchOpen,
      rows,
      selected,
      phase: batchPhase,
      progress,
      openForMissing,
      close: () => setBatchOpen(false),
      toggleRow: (index, checked) =>
        setSelected((prev) => (checked ? [...prev, index] : prev.filter((i) => i !== index))),
      selectAll: (select) => setSelected(select ? rows.map((r) => r._index) : []),
      submit: submitBatch,
    },
  };
}
