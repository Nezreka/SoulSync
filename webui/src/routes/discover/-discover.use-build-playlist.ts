import { useCallback, useEffect, useRef, useState } from 'react';

import type { BpPlaylistMeta, SeedArtist } from './-discover.build-playlist';

import {
  bpAddArtist,
  bpDownloadName,
  BP_DOWNLOAD_PLAYLIST_ID,
  BP_GENERATE_URL,
  BP_NEED_ONE,
  BP_NO_PLAYLIST_TRACKS,
  BP_SEARCH_DEBOUNCE_MS,
  BP_SEARCH_FAILED,
  bpGenerateBody,
  bpGenerateError,
  bpQueryIsEmpty,
  bpRemoveArtist,
  bpResultSubtitle,
  bpSearchOutcome,
  bpSearchUrl,
} from './-discover.build-playlist';

/**
 * The Build-a-Playlist controller.
 *
 * Transcribed from `searchBuildPlaylistArtists` (10894-10954),
 * `addBuildPlaylistArtist`/`removeBuildPlaylistArtist` (10956-10982),
 * `generateBuildPlaylist` (11021-11101) and
 * `openDownloadModalForBuildPlaylist` (11103-11115), over the module.
 *
 * The search's two NON-result outcomes are messages, not empty lists: "No
 * artists found for <q>" and "All results already selected" render in the
 * results area with different meanings, and a failed request toasts the
 * server's error and leaves the area as it was (10910-10913). Adding a seed
 * CLEARS the search (10974-10977). Generate's two failure modes keep their
 * distinct fallbacks through `bpGenerateError`. Download hands the RAW
 * generated tracks to the shared modal (11114 — no conversion), under the
 * `build_playlist_custom` id the module documents as deliberately NOT the
 * sync path's `discover_build_playlist`.
 *
 * Sync is not here: the page hands {syncKey:'build_playlist', tracks} to
 * usePlaylistSync, which already routes it (SYNC_TRACK_SOURCES has the type).
 */

export type BpToast = { message: string; level: 'error' | 'warning' };

export type BpDownload =
  | { kind: 'no-tracks'; toast: string; level: 'warning' }
  | { kind: 'ok'; virtualId: string; name: string; tracks: unknown[] };

export interface BuildPlaylistController {
  query: string;
  setQuery: (query: string) => void;
  results: SeedArtist[];
  /** The no-results / all-selected copy, when the search answered without rows. */
  resultsMessage: string | null;
  searching: boolean;
  selected: SeedArtist[];
  addSeed: (artist: SeedArtist) => void;
  removeSeed: (artistId: string) => void;
  infoOpen: boolean;
  toggleInfo: () => void;
  generating: boolean;
  /** null until a playlist has been generated. */
  tracks: unknown[] | null;
  metadata: BpPlaylistMeta | undefined;
  resultSubtitle: string;
  generate: () => Promise<void>;
  /** The pure half of the download handoff; the caller opens the modal. */
  download: () => BpDownload;
}

export function useBuildPlaylist(onToast: (toast: BpToast) => void): BuildPlaylistController {
  const toastRef = useRef(onToast);
  toastRef.current = onToast;

  const [query, setQueryState] = useState('');
  const [results, setResults] = useState<SeedArtist[]>([]);
  const [resultsMessage, setResultsMessage] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<SeedArtist[]>([]);
  const [infoOpen, setInfoOpen] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [tracks, setTracks] = useState<unknown[] | null>(null);
  const [metadata, setMetadata] = useState<BpPlaylistMeta | undefined>(undefined);
  const [resultSubtitle, setResultSubtitle] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const gen = useRef(0);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const setQuery = useCallback((raw: string) => {
    setQueryState(raw);
    if (timer.current) clearTimeout(timer.current);
    // Empty clears the results area NOW and fires nothing (10901-10905).
    if (bpQueryIsEmpty(raw)) {
      setResults([]);
      setResultsMessage(null);
      setSearching(false);
      return;
    }
    timer.current = setTimeout(() => {
      gen.current += 1;
      const g = gen.current;
      setSearching(true);
      void (async () => {
        try {
          const res = await fetch(bpSearchUrl(raw.trim()));
          const data = (await res.json()) as {
            success?: boolean;
            error?: string;
            artists?: SeedArtist[];
          };
          if (gen.current !== g) return;
          if (!res.ok) {
            // A failed request toasts and leaves the area AS IT WAS (10910).
            toastRef.current({ message: data.error || BP_SEARCH_FAILED, level: 'error' });
            return;
          }
          // Selected seeds are filtered from the results at OUTCOME time, so
          // the ref: the debounce may fire after a seed was added.
          const outcome = bpSearchOutcome(data, raw.trim(), selectedRef.current);
          if (outcome.kind === 'results') {
            setResults(outcome.artists);
            setResultsMessage(null);
          } else {
            setResults([]);
            setResultsMessage(outcome.message);
          }
        } catch {
          if (gen.current === g) {
            toastRef.current({ message: BP_SEARCH_FAILED, level: 'error' });
          }
        } finally {
          if (gen.current === g) setSearching(false);
        }
      })();
    }, BP_SEARCH_DEBOUNCE_MS);
  }, []);

  const addSeed = useCallback((artist: SeedArtist) => {
    setSelected((prev) => {
      const out = bpAddArtist(prev, artist);
      if (!out.added) {
        toastRef.current({ message: out.warning, level: 'warning' });
        return prev;
      }
      return out.selected;
    });
    // Adding clears the search box and its results (10974-10977).
    setQueryState('');
    setResults([]);
    setResultsMessage(null);
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const removeSeed = useCallback((artistId: string) => {
    setSelected((prev) => bpRemoveArtist(prev, artistId));
  }, []);

  const generate = useCallback(async () => {
    if (selected.length === 0) {
      toastRef.current({ message: BP_NEED_ONE, level: 'warning' });
      return;
    }
    setGenerating(true);
    try {
      const res = await fetch(BP_GENERATE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(bpGenerateBody(selected)),
      });
      const data = (await res.json()) as Parameters<typeof bpGenerateError>[1];
      const error = bpGenerateError(res.ok, data);
      if (error) {
        // The vanilla hides any previous results on failure (11094-11096).
        setTracks(null);
        toastRef.current({ message: error, level: 'error' });
        return;
      }
      setTracks(data.playlist!.tracks!);
      setMetadata((data.playlist as { metadata?: BpPlaylistMeta }).metadata);
      setResultSubtitle(bpResultSubtitle(selected));
    } catch (e) {
      setTracks(null);
      toastRef.current({ message: (e as Error).message, level: 'error' });
    } finally {
      setGenerating(false);
    }
  }, [selected]);

  const download = useCallback((): BpDownload => {
    if (!tracks || tracks.length === 0) {
      return { kind: 'no-tracks', toast: BP_NO_PLAYLIST_TRACKS, level: 'warning' };
    }
    return {
      kind: 'ok',
      virtualId: BP_DOWNLOAD_PLAYLIST_ID,
      name: bpDownloadName(selected),
      // RAW tracks, no conversion (11114) — the download modal owns shaping.
      tracks,
    };
  }, [tracks, selected]);

  return {
    query,
    setQuery,
    results,
    resultsMessage,
    searching,
    selected,
    addSeed,
    removeSeed,
    infoOpen,
    toggleInfo: () => setInfoOpen((v) => !v),
    generating,
    tracks,
    metadata,
    resultSubtitle,
    generate,
    download,
  };
}
