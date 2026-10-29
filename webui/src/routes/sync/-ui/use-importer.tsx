/**
 * The import half of the source tabs: which playlists are already in
 * SoulSync, and the one action, import (or open, when it's already in).
 *
 * The page provides where an import lands (the Mirrored tab, with the new
 * playlist open); without a provider, as in a tab's own tests, landing does
 * nothing and the tab still imports.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

import { DEEZER_PLAYLIST_PROGRESS_EVENT, deezerProgressLabel } from '../-sync.events';
import {
  canCopyAsMine,
  copyPlaylistAsMine,
  fetchMirrorIndex,
  importPlaylist,
  mirrorKey,
  type ImportSpec,
} from '../-sync.importer';

export interface ImportLanding {
  /** Show one imported playlist: the Mirrored tab, its window open. */
  land: (mirrorId: number, name: string, already: boolean) => void;
  /** After a batch: the Mirrored tab, reloaded, nothing opened. */
  showList: () => void;
  /** A copy kept as your own (#1913): My Playlists, its window open. */
  landMine?: (playlistId: number, name: string) => void;
}

const NO_LANDING: ImportLanding = { land: () => {}, showList: () => {} };

const ImportLandingContext = createContext<ImportLanding>(NO_LANDING);

export const ImportLandingProvider = ImportLandingContext.Provider;

export type ImportStatus = 'checking' | 'new' | 'importing' | 'imported';

export interface Importer {
  statusOf: (source: string, sourceId: string | number) => ImportStatus;
  /** Open it when it's already in, import it (then open it) when it isn't. */
  importOrOpen: (spec: ImportSpec) => Promise<void>;
  /** Import every one not in yet, one at a time, then show the list. */
  importAll: (specs: ImportSpec[]) => Promise<void>;
  /** A tab that mirrored a playlist itself (a pasted link) records it here. */
  markImported: (source: string, sourceId: string | number, mirrorId: number) => void;
  mirrorIdOf: (source: string, sourceId: string | number) => number | undefined;
  /** Keep it as your own copy in My Playlists instead (#1913); undefined when
   *  the playlist can't be copied (it saves server-side). */
  copyAsMineFor: (spec: ImportSpec) => (() => void) | undefined;
  /** A copy of this one is being made. */
  copyingOf: (source: string, sourceId: string | number) => boolean;
  landing: ImportLanding;
}

/** The loading overlay's text while one import runs; deezer narrates its album pass. */
function useImportOverlay() {
  return useCallback(async <T,>(spec: ImportSpec, run: () => Promise<T>): Promise<T> => {
    const label = `Importing ${spec.name}`;
    window.showLoadingOverlay?.(`${label}...`);
    const onProgress = (event: Event) => {
      const text = deezerProgressLabel(
        (event as CustomEvent<Parameters<typeof deezerProgressLabel>[0]>).detail,
        String(spec.sourceId),
      );
      if (text) window.showLoadingOverlay?.(`${label} — ${text}`);
    };
    if (spec.source === 'deezer')
      window.addEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT, onProgress);
    try {
      return await run();
    } finally {
      if (spec.source === 'deezer') {
        window.removeEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT, onProgress);
      }
      window.hideLoadingOverlay?.();
    }
  }, []);
}

export function useImporter(): Importer {
  const landing = useContext(ImportLandingContext);
  const [index, setIndex] = useState<Map<string, number> | null>(null);
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());
  const indexRef = useRef(index);
  indexRef.current = index;
  const withOverlay = useImportOverlay();
  // an import waits for this, so a link pasted before the index answered
  // opens the playlist that's already in rather than importing it twice
  const indexReady = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    let alive = true;
    indexReady.current = fetchMirrorIndex()
      .catch(
        // not knowing what's in only costs an "Open" label; importing still
        // works, and the backend dedupes a second import of the same playlist
        () => new Map<string, number>(),
      )
      .then((found) => {
        if (!alive) return;
        indexRef.current = found;
        setIndex(found);
      });
    return () => {
      alive = false;
    };
  }, []);

  const markImported = useCallback(
    (source: string, sourceId: string | number, mirrorId: number) => {
      const next = new Map(indexRef.current ?? []).set(mirrorKey(source, sourceId), mirrorId);
      indexRef.current = next;
      setIndex(next);
    },
    [],
  );

  const setBusyKey = useCallback((key: string, on: boolean) => {
    setBusy((prev) => {
      const next = new Set(prev);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);

  const mirrorIdOf = useCallback(
    (source: string, sourceId: string | number) => index?.get(mirrorKey(source, sourceId)),
    [index],
  );

  const statusOf = useCallback(
    (source: string, sourceId: string | number): ImportStatus => {
      const key = mirrorKey(source, sourceId);
      if (busy.has(key)) return 'importing';
      if (index === null) return 'checking';
      return index.has(key) ? 'imported' : 'new';
    },
    [busy, index],
  );

  const importOrOpen = useCallback(
    async (spec: ImportSpec) => {
      const key = mirrorKey(spec.source, spec.sourceId);
      await indexReady.current;
      const existing = indexRef.current?.get(key);
      if (existing !== undefined) {
        landing.land(existing, spec.name, true);
        return;
      }
      setBusyKey(key, true);
      try {
        const mirrorId = await withOverlay(spec, () => importPlaylist(spec));
        markImported(spec.source, spec.sourceId, mirrorId);
        landing.land(mirrorId, spec.name, false);
      } catch (error) {
        window.showToast?.(
          error instanceof Error ? error.message : `Couldn't import ${spec.name}`,
          'error',
        );
      } finally {
        setBusyKey(key, false);
      }
    },
    [landing, markImported, setBusyKey, withOverlay],
  );

  const importAll = useCallback(
    async (specs: ImportSpec[]) => {
      await indexReady.current;
      const todo = specs.filter((s) => !indexRef.current?.has(mirrorKey(s.source, s.sourceId)));
      if (!todo.length) {
        window.showToast?.('Everything here is already in SoulSync', 'info');
        return;
      }
      let done = 0;
      const failed: string[] = [];
      for (const spec of todo) {
        const key = mirrorKey(spec.source, spec.sourceId);
        setBusyKey(key, true);
        window.showLoadingOverlay?.(`Importing ${done + 1} of ${todo.length}: ${spec.name}...`);
        try {
          markImported(spec.source, spec.sourceId, await importPlaylist(spec));
          done += 1;
        } catch {
          failed.push(spec.name);
        } finally {
          setBusyKey(key, false);
        }
      }
      window.hideLoadingOverlay?.();
      if (done) landing.showList();
      window.showToast?.(
        failed.length
          ? `Imported ${done} of ${todo.length}. Couldn't import: ${failed.join(', ')}`
          : `Imported ${done} playlist${done === 1 ? '' : 's'}. They're identifying in the background`,
        failed.length ? 'error' : 'success',
      );
    },
    [landing, markImported, setBusyKey],
  );

  const copyAsMine = useCallback(
    async (spec: ImportSpec) => {
      const key = `mine:${mirrorKey(spec.source, spec.sourceId)}`;
      setBusyKey(key, true);
      try {
        const playlistId = await withOverlay(spec, () => copyPlaylistAsMine(spec));
        if (landing.landMine) landing.landMine(playlistId, spec.name);
        else window.showToast?.(`${spec.name} is in My Playlists`, 'success');
      } catch (error) {
        window.showToast?.(
          error instanceof Error ? error.message : `Couldn't copy ${spec.name}`,
          'error',
        );
      } finally {
        setBusyKey(key, false);
      }
    },
    [landing, setBusyKey, withOverlay],
  );

  const copyAsMineFor = useCallback(
    (spec: ImportSpec) => (canCopyAsMine(spec) ? () => void copyAsMine(spec) : undefined),
    [copyAsMine],
  );

  const copyingOf = useCallback(
    (source: string, sourceId: string | number) => busy.has(`mine:${mirrorKey(source, sourceId)}`),
    [busy],
  );

  return {
    statusOf,
    importOrOpen,
    importAll,
    markImported,
    mirrorIdOf,
    copyAsMineFor,
    copyingOf,
    landing,
  };
}
