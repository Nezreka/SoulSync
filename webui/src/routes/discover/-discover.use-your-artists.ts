import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { YourArtist } from './-discover.your-artists';
import type {
  ArtistPool,
  ArtistsModalSort,
  ArtistsModalState,
} from './-discover.your-artists-actions';
import type { ArtistInfo } from './-ui/artist-info-modal';

import {
  fetchAllYourArtists,
  fetchArtistInfo,
  fetchYourArtists,
  fetchYourArtistsSources,
  refreshYourArtists,
  saveDiscoverSettings,
} from './-discover.api';
import {
  enabledSources,
  SOURCES_NONE_SELECTED,
  SOURCES_SAVE_FAILED,
  SOURCES_SAVED,
} from './-discover.your-albums-actions';
import {
  applyArtistsModalFilter,
  ARTISTS_DEFAULT_SOURCES,
  ARTISTS_MODAL_SEARCH_DEBOUNCE_MS,
  ARTISTS_REFRESH_FAILED,
  ARTISTS_REFRESH_MAX_ATTEMPTS,
  ARTISTS_REFRESH_POLL_MS,
  artistSourcesSavePayload,
  artistsModalQuery,
  artistsRefreshSettled,
  artistsRefreshToast,
  INITIAL_ARTISTS_MODAL_STATE,
  infoLookupId,
  initialArtistSourcesState,
  poolWatchlistValue,
  setArtistsModalPage,
  toggleArtistSource,
  WATCHLIST_TOGGLE_FAILED,
  watchlistRequest,
  watchlistToast,
} from './-discover.your-artists-actions';

/**
 * The Your Artists interactions — sources modal, refresh poll, the View All
 * modal, and the info modal with its watchlist toggle.
 *
 * Transcribed from discover.js 5578-5826 (refresh, sources, all-artists
 * modal) and 5356-5575 (info modal + toggle + card sync), over the module
 * that already owns every rule; this hook is fetch + state + the module.
 *
 * Where the vanilla wrote DOM (subtitle text, card eye icons, the
 * `window._yaArtists` pool), this keeps the equivalent STATE: `watchOverrides`
 * is `_syncYaCardWatchlist` — poolId → on_watchlist — which the page merges
 * over the query rows so every card and modal agrees without refetching.
 */

export type ArtistsToast = { message: string; level: 'success' | 'error' | 'warning' | 'info' };

const INFO_TIMEOUT_MS = 8000;

export interface YourArtistsController {
  sources: {
    open: boolean;
    state: Record<string, boolean>;
    connected: string[];
    openModal: () => void;
    closeModal: () => void;
    toggle: (id: string) => void;
    save: () => Promise<void>;
    /** Set after a save, for the section subtitle. */
    savedEnabled: string[] | null;
  };
  refresh: { refreshing: boolean; start: () => Promise<void> };
  browse: {
    open: boolean;
    state: ArtistsModalState;
    total: number | null;
    artists: YourArtist[];
    phase: 'loading' | 'error' | 'ready';
    openModal: () => void;
    closeModal: () => void;
    filter: (change: Partial<{ source: string; sort: ArtistsModalSort; search: string }>) => void;
    page: (page: number) => void;
  };
  info: {
    pool: ArtistPool | null;
    data: ArtistInfo | null;
    phase: 'loading' | 'error' | 'ready';
    open: (pool: ArtistPool) => void;
    close: () => void;
    toggleWatch: (pool: ArtistPool) => Promise<void>;
  };
  /** poolId → on_watchlist, the port of _syncYaCardWatchlist (5559). */
  watchOverrides: Record<string, number>;
}

export function useYourArtists(onToast: (toast: ArtistsToast) => void): YourArtistsController {
  const queryClient = useQueryClient();
  const toastRef = useRef(onToast);
  toastRef.current = onToast;

  // ── Sources modal (5608-5669) ─────────────────────────────────────────
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [sourcesState, setSourcesState] = useState<Record<string, boolean>>({});
  const [connected, setConnected] = useState<string[]>([]);
  const [savedEnabled, setSavedEnabled] = useState<string[] | null>(null);

  const openSources = useCallback(() => {
    setSourcesOpen(true);
    void (async () => {
      // The vanilla swallows a failed sources fetch and falls back to the
      // defaults with nothing connected (5615-5622).
      let enabled: string[] = [...ARTISTS_DEFAULT_SOURCES];
      let conn: string[] = [];
      try {
        const data = await fetchYourArtistsSources();
        if (data.enabled) enabled = data.enabled;
        if (data.connected) conn = data.connected;
      } catch {
        /* defaults stand */
      }
      setSourcesState(initialArtistSourcesState(enabled));
      setConnected(conn);
    })();
  }, []);

  const toggleSource = useCallback(
    (id: string) => {
      setSourcesState((prev) => {
        const { state, hint } = toggleArtistSource(prev, id, connected);
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
      await saveDiscoverSettings(artistSourcesSavePayload(enabled));
      setSourcesOpen(false);
      setSavedEnabled(enabled);
      toastRef.current({ message: SOURCES_SAVED, level: 'success' });
    } catch {
      toastRef.current({ message: SOURCES_SAVE_FAILED, level: 'error' });
    }
  }, [sourcesState]);

  // ── Refresh (5578-5605) ───────────────────────────────────────────────
  const [refreshing, setRefreshing] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(
    () => () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    },
    [],
  );

  const startRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await refreshYourArtists();
    } catch {
      toastRef.current({ message: ARTISTS_REFRESH_FAILED, level: 'error' });
      setRefreshing(false);
      return;
    }
    let attempts = 0;
    if (pollTimer.current) clearInterval(pollTimer.current);
    pollTimer.current = setInterval(() => {
      void (async () => {
        attempts += 1;
        if (attempts > ARTISTS_REFRESH_MAX_ATTEMPTS) {
          if (pollTimer.current) clearInterval(pollTimer.current);
          // DIVERGENCE 1 (module header): the vanilla's give-up path leaves
          // the button dead forever; this re-enables it.
          setRefreshing(false);
          return;
        }
        try {
          const data = await fetchYourArtists();
          if (artistsRefreshSettled(data)) {
            if (pollTimer.current) clearInterval(pollTimer.current);
            queryClient.setQueryData(['discover', 'your-artists'], data);
            setRefreshing(false);
            toastRef.current({
              message: artistsRefreshToast((data as { total?: number }).total ?? 0),
              level: 'success',
            });
          }
        } catch {
          /* the vanilla's poll swallows failures and keeps trying (5601) */
        }
      })();
    }, ARTISTS_REFRESH_POLL_MS);
  }, [queryClient]);

  // ── The View All modal (5723-5826) ────────────────────────────────────
  const [browseOpen, setBrowseOpen] = useState(false);
  const [browseState, setBrowseState] = useState<ArtistsModalState>(INITIAL_ARTISTS_MODAL_STATE);
  const [browseTotal, setBrowseTotal] = useState<number | null>(null);
  const [browseArtists, setBrowseArtists] = useState<YourArtist[]>([]);
  const [browsePhase, setBrowsePhase] = useState<'loading' | 'error' | 'ready'>('loading');
  const browseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const browseGen = useRef(0);

  const loadBrowse = useCallback((state: ArtistsModalState) => {
    browseGen.current += 1;
    const gen = browseGen.current;
    setBrowsePhase('loading');
    void (async () => {
      try {
        const data = await fetchAllYourArtists(artistsModalQuery(state));
        if (browseGen.current !== gen) return;
        // The subtitle keeps its LAST total across reloads (5785) — only a
        // real answer replaces it.
        setBrowseTotal((data as { total?: number }).total ?? 0);
        setBrowseArtists((data.artists as YourArtist[]) ?? []);
        setBrowsePhase('ready');
      } catch {
        if (browseGen.current !== gen) return;
        setBrowsePhase('error');
      }
    })();
  }, []);

  const openBrowse = useCallback(() => {
    setBrowseOpen(true);
    setBrowseState(INITIAL_ARTISTS_MODAL_STATE);
    setBrowseTotal(null);
    loadBrowse(INITIAL_ARTISTS_MODAL_STATE);
  }, [loadBrowse]);

  const closeBrowse = useCallback(() => {
    setBrowseOpen(false);
    if (browseTimer.current) clearTimeout(browseTimer.current);
  }, []);

  const filterBrowse = useCallback(
    (change: Partial<{ source: string; sort: ArtistsModalSort; search: string }>) => {
      const next = applyArtistsModalFilter(browseState, change);
      setBrowseState(next);
      if (browseTimer.current) clearTimeout(browseTimer.current);
      if ('search' in change) {
        // Only typing debounces (5761-5764); pills and sort reload now.
        browseTimer.current = setTimeout(() => loadBrowse(next), ARTISTS_MODAL_SEARCH_DEBOUNCE_MS);
      } else {
        loadBrowse(next);
      }
    },
    [browseState, loadBrowse],
  );

  const pageBrowse = useCallback(
    (page: number) => {
      const next = setArtistsModalPage(browseState, page);
      setBrowseState(next);
      loadBrowse(next);
    },
    [browseState, loadBrowse],
  );

  // ── The info modal (5356-5515) + watch toggle (5517-5575) ─────────────
  const [infoPool, setInfoPool] = useState<ArtistPool | null>(null);
  const [infoData, setInfoData] = useState<ArtistInfo | null>(null);
  const [infoPhase, setInfoPhase] = useState<'loading' | 'error' | 'ready'>('loading');
  const [watchOverrides, setWatchOverrides] = useState<Record<string, number>>({});
  const infoGen = useRef(0);

  const openInfo = useCallback((pool: ArtistPool) => {
    infoGen.current += 1;
    const gen = infoGen.current;
    setInfoPool(pool);
    setInfoData(null);
    setInfoPhase('loading');
    void (async () => {
      // The vanilla aborts the enrichment fetch after 8s (5412-5414); here
      // the timeout races it — either way the modal stops waiting.
      try {
        const data = await Promise.race([
          fetchArtistInfo(
            infoLookupId(pool.active_source_id, pool.artist_name ?? ''),
            pool.artist_name ?? '',
          ),
          new Promise<never>((_, reject) =>
            setTimeout(() => reject(new Error('timeout')), INFO_TIMEOUT_MS),
          ),
        ]);
        if (infoGen.current !== gen) return;
        setInfoData(data as ArtistInfo);
        setInfoPhase('ready');
      } catch {
        if (infoGen.current !== gen) return;
        setInfoPhase('error');
      }
    })();
  }, []);

  const closeInfo = useCallback(() => {
    infoGen.current += 1;
    setInfoPool(null);
  }, []);

  const toggleWatch = useCallback(
    async (pool: ArtistPool) => {
      const poolId = String(pool.id ?? '');
      const currently = Boolean(watchOverrides[poolId] ?? pool.on_watchlist);
      const req = watchlistRequest(currently, {
        sourceId: pool.active_source_id ?? '',
        artistName: pool.artist_name ?? '',
        source: pool.active_source ?? '',
      });
      try {
        const res = await fetch(req.url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(req.body),
        });
        if (!res.ok) return;
        const toast = watchlistToast(currently, pool.artist_name ?? '');
        toastRef.current({ message: toast.message, level: toast.level });
        // _syncYaCardWatchlist (5559): every card showing this pool follows.
        setWatchOverrides((prev) => ({ ...prev, [poolId]: poolWatchlistValue(!currently) }));
      } catch {
        toastRef.current({ message: WATCHLIST_TOGGLE_FAILED, level: 'error' });
      }
    },
    [watchOverrides],
  );

  return {
    sources: {
      open: sourcesOpen,
      state: sourcesState,
      connected,
      openModal: openSources,
      closeModal: () => setSourcesOpen(false),
      toggle: toggleSource,
      save: saveSources,
      savedEnabled,
    },
    refresh: { refreshing, start: startRefresh },
    browse: {
      open: browseOpen,
      state: browseState,
      total: browseTotal,
      artists: browseArtists,
      phase: browsePhase,
      openModal: openBrowse,
      closeModal: closeBrowse,
      filter: filterBrowse,
      page: pageBrowse,
    },
    info: {
      pool: infoPool,
      data: infoData,
      phase: infoPhase,
      open: openInfo,
      close: closeInfo,
      toggleWatch,
    },
    watchOverrides,
  };
}
