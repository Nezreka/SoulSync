import { useCallback, useRef, useState } from 'react';

import type { DiscoverMix } from './-discover.mixes';

import { fetchDecadeTracks } from './-discover.api';
import { discoverTrackToSpotifyShape } from './-discover.helpers';

/**
 * The mix modal's controller — open/close, lazy tracks, selection, and the
 * download-selected handoff.
 *
 * Transcribed from `openMixModalByKey`/`openMixModal` (discover.js 4926-5040)
 * and the #1079 selection functions (4772-4827). The vanilla keeps the
 * selection in checkbox DOM state and the lazy tracks by MUTATING the registry
 * entry (`mix.tracks = tracks`, 5012); here both are hook state, and the lazy
 * cache outlives a close so re-opening a decade is instant — the vanilla gets
 * the same effect from its mutation.
 *
 * What this hook does NOT do: fire the download. `downloadSelection()` is the
 * PURE half of `_downloadSelectedMixTracks` (4806-4827) — subset, spotify
 * shapes, the collision-proof virtual id, the "(N selected)" name — handed to
 * the caller, who closes the modal and opens the shared download modal, plus
 * the two early-out toasts as data.
 */

export type LazyTrackSource = () => Promise<unknown[]>;

/**
 * A decade mix loads its tracks on open (2675-2680): `/api/discover/decade/N`,
 * taking `tracks` off the payload. Other lazy families (ListenBrainz, Last.fm)
 * register through `extraLazy` when their sections wire up.
 */
export function defaultLazySource(mix: DiscoverMix): LazyTrackSource | null {
  const decade = /^decade_(\d+)$/.exec(mix.key);
  if (decade) {
    const year = Number(decade[1]);
    return async () => {
      const data = await fetchDecadeTracks(year);
      return Array.isArray(data.tracks) ? data.tracks : [];
    };
  }
  return null;
}

export type DownloadSelectionResult =
  | { kind: 'none-selected'; toast: string; level: 'info' }
  | { kind: 'stale'; toast: string; level: 'error' }
  | { kind: 'ok'; virtualId: string; name: string; tracks: Record<string, unknown>[] };

export interface MixModalController {
  /** The open mix, or null. Tracks come from `tracks`, not from the mix. */
  mix: DiscoverMix | null;
  /** Undefined while a lazy mix is still fetching. */
  tracks: unknown[] | undefined;
  loading: boolean;
  error: boolean;
  selected: number[];
  open: (key: string) => void;
  close: () => void;
  toggleTrack: (index: number) => void;
  selectAll: (indices: number[]) => void;
  clearSelection: () => void;
  /** The pure half of _downloadSelectedMixTracks; the caller acts on it. */
  downloadSelection: () => DownloadSelectionResult;
  /**
   * tracks for any mix key, fetching a lazy one if it hasn't loaded yet.
   *
   * the card's Play button needs the tracklist without opening the modal, and
   * a decade or listenbrainz mix carries none until something asks. null means
   * the key is unknown or its fetch failed.
   */
  loadTracks: (key: string) => Promise<unknown[] | null>;
}

export function useMixModal(
  registry: Record<string, DiscoverMix>,
  extraLazy?: (mix: DiscoverMix) => LazyTrackSource | null,
): MixModalController {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [lazy, setLazy] = useState<Record<string, unknown[] | 'loading' | 'error'>>({});
  // A stale lazy fetch resolving after close/reopen must not clobber state.
  const generation = useRef(0);

  const mix = openKey ? (registry[openKey] ?? null) : null;

  const open = useCallback(
    (key: string) => {
      const m = registry[key];
      if (!m) return;
      generation.current += 1;
      const gen = generation.current;
      setOpenKey(key);
      // A fresh open starts unselected, like the vanilla's fresh checkboxes.
      setSelected([]);
      if (m.tracks || lazy[key] === 'loading' || Array.isArray(lazy[key])) return;
      const source = extraLazy?.(m) ?? defaultLazySource(m);
      if (!source) return;
      setLazy((prev) => ({ ...prev, [key]: 'loading' }));
      source()
        .then((tracks) => {
          if (generation.current !== gen) return;
          setLazy((prev) => ({ ...prev, [key]: tracks || [] }));
        })
        .catch(() => {
          if (generation.current !== gen) return;
          setLazy((prev) => ({ ...prev, [key]: 'error' }));
        });
    },
    [registry, lazy, extraLazy],
  );

  const close = useCallback(() => {
    generation.current += 1;
    setOpenKey(null);
    setSelected([]);
  }, []);

  const lazyState = openKey ? lazy[openKey] : undefined;
  const tracks = mix?.tracks ?? (Array.isArray(lazyState) ? lazyState : undefined);
  const loading = lazyState === 'loading';
  const error = lazyState === 'error';

  const toggleTrack = useCallback((index: number) => {
    setSelected((prev) =>
      prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index],
    );
  }, []);
  const selectAll = useCallback((indices: number[]) => setSelected(indices), []);
  const clearSelection = useCallback(() => setSelected([]), []);

  const loadTracks = useCallback(
    async (key: string): Promise<unknown[] | null> => {
      const m = registry[key];
      if (!m) return null;
      if (m.tracks) return m.tracks;
      const cached = lazy[key];
      if (Array.isArray(cached)) return cached;
      const source = extraLazy?.(m) ?? defaultLazySource(m);
      if (!source) return [];
      setLazy((prev) => ({ ...prev, [key]: 'loading' }));
      try {
        const fetched = (await source()) || [];
        setLazy((prev) => ({ ...prev, [key]: fetched }));
        return fetched;
      } catch {
        setLazy((prev) => ({ ...prev, [key]: 'error' }));
        return null;
      }
    },
    [registry, lazy, extraLazy],
  );

  const downloadSelection = useCallback((): DownloadSelectionResult => {
    if (selected.length === 0) {
      return { kind: 'none-selected', toast: 'Select at least one track first', level: 'info' };
    }
    const all = tracks ?? [];
    const subset = selected
      .map((i) => all[i] as Record<string, unknown> | undefined)
      .filter((t): t is Record<string, unknown> => Boolean(t));
    if (subset.length === 0) {
      return { kind: 'stale', toast: 'Selected tracks are no longer available', level: 'error' };
    }
    const idBase = mix?.syncKey || mix?.key || 'discover_selected';
    return {
      kind: 'ok',
      // Unique so a subset download never collides with the whole-playlist
      // download's state, which stays keyed by the playlist's own id (4818).
      virtualId: `${idBase}_sel_${Date.now()}`,
      name: `${mix?.title || 'Playlist'} (${subset.length} selected)`,
      tracks: subset.map((t) => discoverTrackToSpotifyShape(t as never)),
    };
  }, [selected, tracks, mix]);

  return {
    mix,
    tracks,
    loading,
    error,
    selected,
    open,
    close,
    toggleTrack,
    selectAll,
    clearSelection,
    downloadSelection,
    loadTracks,
  };
}
