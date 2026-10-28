/**
 * The sync page's controller: everything the page needs, assembled once.
 *
 * Split from the JSX deliberately. The wiring is where this port's failures
 * have lived — a second pipeline controller, an unreachable global, a superset
 * standing in for display order — and none of them are visible in markup. The
 * panel mounting is mechanical by comparison, so it stays in the component and
 * this stays testable on its own.
 *
 * THE ENGINE IS RESOLVED AT CALL TIME, not at mount. Every member reads its
 * `window` function inside the closure, so a page that mounts before the
 * vanilla scripts finish still works, and a missing global is a no-op rather
 * than a crash at construction. `isSyncing` and the playlist list are the two
 * that needed accessors added (core.js) — `activeSyncPollers` and
 * `spotifyPlaylists` are top-level `let`s and have no window property at all.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';

import type { SyncModals } from './-sync.use-modals';
import type { PipelineController } from './-sync.use-pipeline';
import type { SourceVertical } from './-sync.use-vertical';

import { mirroredPipelineStateWriter } from './-sync.mirrored';
import { SYNC_SOURCES } from './-sync.sources';
import { useSyncModals } from './-sync.use-modals';
import { useMirroredPipeline } from './-sync.use-pipeline';
import { useStandalone } from './-sync.use-standalone';
import { useSourceVertical } from './-sync.use-vertical';

export interface SyncPage {
  /**
   * The mirrored playlists' identify/sync state. The one vertical left: every
   * source tab imports into a mirror now, so a playlist is only ever
   * identified, synced or downloaded from there.
   */
  mirrored: SourceVertical;
  modals: SyncModals;
  /** The page's ONE controller — see -sync.use-pipeline and S3b-i. */
  pipeline: PipelineController;
  standalone: boolean;
  /** Handed to MirroredTab. `key` names the instance: the Mirrored and My
   *  Playlists tabs are two of them, and a pipeline run has to refresh both. */
  registerMirroredReload: (reload: () => void, key?: string) => void;
  /**
   * Refetch the mirrored rows. The page already holds the tab's reload for the
   * pipeline controller; the import tab needs the same one, because
   * importFileSubmit's tail re-loaded that list after writing a playlist
   * (sync-services.js 449-455). No-op until MirroredTab has mounted and
   * registered — which matches the vanilla, where the list simply was not
   * there to refresh yet.
   */
  reloadMirrored: () => void;
}

export function useSyncPage(): SyncPage {
  const standalone = useStandalone();
  const modals = useSyncModals();

  /** Each mirrored tab's row refetch, filled on its mount. */
  const mirroredReloads = useRef(new Map<string, () => void>());
  const registerMirroredReload = useCallback((reload: () => void, key = 'mirrored') => {
    mirroredReloads.current.set(key, reload);
  }, []);

  const mirrored = useSourceVertical(SYNC_SOURCES.mirrored);

  const onPipelineState = useMemo(() => mirroredPipelineStateWriter(mirrored), [mirrored]);
  const reloadMirrored = useCallback(() => {
    for (const reload of mirroredReloads.current.values()) reload();
  }, []);
  const pipeline = useMirroredPipeline({ onState: onPipelineState, reload: reloadMirrored });

  /**
   * #1289: let the vanilla manual-match tool trigger a mirrored refetch.
   * The tool lives outside React (shell/manual-library-match.ts) and only
   * re-renders its own list on save/delete — the mirrored card counts it
   * just changed would stay stale until the next manual refresh. Exposing
   * the tab's reload on window reuses the exact path the pipeline controller
   * already uses. No-op when the tab isn't mounted (e.g. tool opened from
   * the Tools page).
   */
  useEffect(() => {
    window.reloadMirroredTab = reloadMirrored;
    return () => {
      delete window.reloadMirroredTab;
    };
  }, [reloadMirrored]);

  return {
    mirrored,
    modals,
    pipeline,
    standalone,
    registerMirroredReload,
    reloadMirrored,
  };
}
